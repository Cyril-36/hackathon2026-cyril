// ShopWave Support Console — in-browser ticket store.
//
// Replaces the old synthetic data/tickets.js. On page load, window.TICKETS /
// RUN_META / TOOLS / RUN_STATS are EMPTY — the App boots via api.snapshot()
// and populates them before first render.
//
// During a live run, SSE events arrive and applyEvent(state, ev) mutates
// the matching ticket in place: tool_start pushes a trace row, tool_end
// fills in the result + timing, classify/decide/ticket_done set metadata.
// The reducer never throws; unknown events are ignored (forward-compatible
// with future event types added to the backend).

(function () {
  window.RUN_META = {
    run_id: '',
    started_at: '',
    ended_at: '',
    duration_ms: 0,
    mode: 'rules',
    chaos: false,
    concurrency: 1,
    model: 'rules-deterministic',
    policy_version: 'kb-v1.0',
    tool_registry_version: 'tools-v1.0',
  };
  window.TOOLS = [];
  window.TICKETS = [];
  window.RUN_STATS = {
    total: 0,
    resolved: 0,
    escalated: 0,
    declined: 0,
    dlq: 0,
    failed: 0,
    recovered: 0,
    avg_confidence: 0,
    by_basis: {},
    tokens_in: 0,
    tokens_out: 0,
    tool_calls: 0,
  };

  // ---- Stats recompute -----------------------------------------------------

  function recomputeStats(tickets) {
    const processed = tickets.filter(t => t.outcome && t.outcome !== 'pending');
    const by = (pred) => tickets.filter(pred).length;
    const by_basis = {};
    for (const t of tickets) {
      const b = t.decision_basis || 'pending';
      by_basis[b] = (by_basis[b] || 0) + 1;
    }
    const confSum = processed.reduce((a, t) => a + (t.agent_confidence || 0), 0);
    return {
      total: tickets.length,
      resolved: by(t => t.outcome === 'resolved'),
      escalated: by(t => t.outcome === 'escalated'),
      declined: by(t => t.outcome === 'declined'),
      dlq: by(t => (t.failures || []).some(f => !f.recovered)),
      failed: by(t => (t.failures || []).length > 0),
      recovered: by(t => t.recovery_attempted),
      avg_confidence: processed.length ? +(confSum / processed.length).toFixed(3) : 0,
      by_basis,
      tokens_in: 0,
      tokens_out: 0,
      tool_calls: tickets.reduce((a, t) => a + (t.tools_used || []).length, 0),
    };
  }

  // ---- Event reducer -------------------------------------------------------

  // Finds a ticket by id; creates a placeholder if missing.
  function getOrCreate(tickets, ticketId, fallbackSubject) {
    let t = tickets.find(x => x.id === ticketId);
    if (!t) {
      t = {
        id: ticketId,
        subject: fallbackSubject || ticketId,
        body: '',
        source: '',
        customer: { email: '', name: ticketId, tier: 'standard' },
        order_id: '',
        priority: 'P3',
        urgency: 'low',
        category: 'pending',
        classified_confidence: 0,
        evidence_confidence: 0,
        outcome: 'pending',
        decision_basis: 'pending',
        agent_confidence: 0,
        auto_replied: false,
        duration_ms: 0,
        tokens: { in: null, out: null },
        trace: [],
        failures: [],
        tools_used: [],
        recovery_attempted: false,
        reply: null,
        escalation_summary: null,
      };
      tickets.push(t);
    }
    return t;
  }

  // Apply a single SSE event (wire format). Mutates tickets in place,
  // returns true if state changed (caller triggers a re-render).
  function applyEvent(tickets, ev, meta) {
    if (!ev || !ev.type) return false;
    if (ev.type === 'run_start') {
      if (meta) {
        meta.run_id = ev.run_id || meta.run_id;
        meta.mode = ev.mode || meta.mode;
        meta.chaos = !!ev.chaos && ev.chaos > 0;
        meta.started_at = ev.started_at || new Date().toISOString();
        meta.duration_ms = 0;
      }
      return true;
    }
    if (ev.type === 'run_done') {
      if (meta) {
        meta.ended_at = ev.ended_at || new Date().toISOString();
      }
      return true;
    }
    if (!ev.ticket_id) return false;

    const t = getOrCreate(tickets, ev.ticket_id, ev.subject);
    if (!t.__started_ms && typeof ev.ts_ms === 'number') {
      t.__started_ms = ev.ts_ms;
    }
    const relMs = (typeof ev.ts_ms === 'number' && typeof t.__started_ms === 'number')
      ? ev.ts_ms - t.__started_ms : 0;

    switch (ev.type) {
      case 'ticket_start':
        t.subject = ev.subject || t.subject;
        if (ev.customer_email) {
          t.customer = t.customer || {};
          t.customer.email = ev.customer_email;
          if (!t.customer.name) {
            const local = ev.customer_email.split('@')[0] || 'customer';
            t.customer.name = local.replace(/[._]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
          }
          t.customer.tier = (ev.tier || 0) >= 2 ? 'premium' : 'standard';
        }
        t.source = ev.source || t.source;
        t.outcome = 'running';
        break;

      case 'classify':
        t.category = ev.category || t.category;
        t.urgency = ev.urgency || t.urgency;
        t.classified_confidence = ev.classifier_confidence ?? ev.confidence ?? t.classified_confidence;
        t.priority = ({urgent:'P1',high:'P1',medium:'P2',normal:'P2',low:'P3'})[ev.urgency] || t.priority;
        t.trace.push({
          t: relMs, ms: 0, kind: 'classify', label: 'classify_intent',
          result: ev.category, conf: ev.classifier_confidence ?? ev.confidence, status: 'ok',
        });
        break;

      case 'trace':
        t.trace.push({
          t: relMs, ms: 0, kind: 'decide', label: ev.step, note: ev.note, status: 'ok',
        });
        break;

      case 'tool_start':
        t.trace.push({
          t: relMs, ms: 0, kind: 'tool', tool: ev.tool, args: ev.args || {},
          status: 'running',
        });
        break;

      case 'tool_end': {
        // Find the most recent running row for this tool and finalise it.
        for (let i = t.trace.length - 1; i >= 0; i--) {
          const row = t.trace[i];
          if (row.kind === 'tool' && row.tool === ev.tool && row.status === 'running') {
            row.status = 'ok';
            row.ms = ev.ms || 0;
            row.result = ev.result;
            row.attempts = ev.attempts;
            break;
          }
        }
        if (!t.tools_used.includes(ev.tool)) t.tools_used.push(ev.tool);
        break;
      }

      case 'tool_failure':
        // Mark the latest running row as failed (but don't remove it) so the
        // recovery row shows up as a follow-up.
        for (let i = t.trace.length - 1; i >= 0; i--) {
          const row = t.trace[i];
          if (row.kind === 'tool' && row.tool === ev.tool && row.status === 'running') {
            row.status = 'error';
            row.error = ev.error;
            row.ms = 0;
            break;
          }
        }
        t.trace.push({
          t: relMs, ms: 0, kind: 'tool', tool: ev.tool,
          status: ev.retryable ? 'running' : 'error',
          error: ev.error, attempt: ev.attempt, note: ev.retryable ? 'retrying…' : 'terminal',
        });
        break;

      case 'tool_recovered':
        t.recovery_attempted = true;
        t.trace.push({
          t: relMs, ms: 0, kind: 'recover', tool: ev.tool,
          status: 'recovered', attempts: ev.attempts, last_error: ev.last_error,
          note: `recovered after ${ev.attempts} retries`,
        });
        t.failures.push({
          tool: ev.tool, error: ev.last_error, retry_count: ev.attempts, recovered: true,
        });
        break;

      case 'tool_dead_lettered':
        t.failures.push({
          tool: ev.tool, error: ev.error, retry_count: ev.attempts, recovered: false,
        });
        t.trace.push({
          t: relMs, ms: 0, kind: 'tool', tool: ev.tool, status: 'error',
          error: ev.error, note: 'dead-lettered',
        });
        break;

      case 'decide':
        t.evidence_confidence = ev.evidence_confidence ?? t.evidence_confidence;
        t.agent_confidence = ev.action_confidence ?? ev.confidence ?? t.agent_confidence;
        t.trace.push({
          t: relMs, ms: 0, kind: 'decide', label: 'evaluate',
          note:
            `verify_ok=${ev.verify_ok} irreversible=${ev.irreversible}` +
            (ev.evidence_confidence != null || ev.action_confidence != null
              ? ` evidence=${ev.evidence_confidence ?? '—'} action=${ev.action_confidence ?? '—'}`
              : ''),
          status: 'ok',
        });
        break;

      case 'ticket_done':
        t.outcome = ev.outcome || t.outcome;
        t.decision_basis = ev.decision_basis || t.decision_basis;
        t.classified_confidence = ev.classifier_confidence ?? t.classified_confidence;
        t.evidence_confidence = ev.evidence_confidence ?? t.evidence_confidence;
        t.agent_confidence = ev.action_confidence ?? ev.confidence ?? t.agent_confidence;
        t.duration_ms = ev.duration_ms || t.duration_ms;
        t.reply = ev.reply_sent;
        t.escalation_summary = ev.escalation_summary;
        t.recovery_attempted = !!ev.recovery_attempted;
        t.auto_replied = !!ev.reply_sent;
        t.category = ev.category || t.category;
        t.urgency = ev.urgency || t.urgency;
        if (ev.tools_used) t.tools_used = ev.tools_used;
        break;

      default:
        // Unknown event types are ignored — forward-compat.
        return false;
    }
    return true;
  }

  window.TicketStore = {
    recomputeStats,
    applyEvent,
    getOrCreate,
  };
})();
