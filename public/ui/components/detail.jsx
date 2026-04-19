const TOOL_COPY = {
  get_order: 'order lookup',
  get_customer: 'customer lookup',
  get_customer_orders: 'customer order lookup',
  get_product: 'product lookup',
  search_knowledge_base: 'knowledge base search',
  check_refund_eligibility: 'refund eligibility check',
  issue_refund: 'refund issued',
  cancel_order: 'order cancelled',
  initiate_exchange: 'exchange initiated',
  send_reply: 'customer reply sent',
  escalate: 'human escalation created',
};

const IRREVERSIBLE_TOOLS = new Set(['issue_refund', 'cancel_order', 'initiate_exchange']);

function humanTool(tool) {
  return TOOL_COPY[tool] || tool.replace(/_/g, ' ');
}

function toolStepDetail(ticket, toolName) {
  const trace = ticket.trace || [];
  for (const step of trace) {
    if (step.kind !== 'tool' || step.label !== toolName) continue;
    if (typeof step.detail === 'string' && step.detail.trim()) return step.detail;
    if (typeof step.message === 'string' && step.message.trim()) return step.message;
  }
  return '';
}

function summarizeDecision(ticket, isDLQ) {
  const tools = ticket.tools_used || [];
  const failures = ticket.failures || [];
  const recoveredFailures = failures.filter(f => f.recovered).length;
  const unrecoveredFailures = failures.filter(f => !f.recovered).length;
  const irreversibleTool = tools.find(tool => IRREVERSIBLE_TOOLS.has(tool)) || null;
  const category = humanizeCategory(ticket.category || 'support').toLowerCase();
  const basis = basisLabel(ticket.decision_basis);
  const refundDetail = toolStepDetail(ticket, 'issue_refund').toLowerCase();
  const refundAlreadyHandled =
    refundDetail.includes('issued: false') ||
    refundDetail.includes('already refunded') ||
    refundDetail.includes('idempotent');

  let lead = 'This ticket is still being evaluated.';
  if (isDLQ) lead = 'This ticket was moved to the failed queue after an unrecovered tool failure.';
  else if (ticket.outcome === 'resolved') lead = 'This ticket was resolved automatically.';
  else if (ticket.outcome === 'declined') lead = 'This ticket was declined under the current policy rules.';
  else if (ticket.outcome === 'escalated') lead = 'This ticket was escalated for human review.';

  const detail = `It was treated as a ${category} case and followed a ${basis.toLowerCase()} path.`;

  let action = 'No customer-facing action has completed yet.';
  if (irreversibleTool === 'issue_refund' && refundAlreadyHandled) {
    action = 'Refund status was checked; no new refund was issued because the order was already refunded.';
  } else if (irreversibleTool) {
    action = `Irreversible action taken: ${humanTool(irreversibleTool)}.`;
  } else if (ticket.reply) action = 'A customer reply was sent automatically.';
  else if (ticket.outcome === 'declined' || ticket.outcome === 'escalated' || isDLQ) {
    action = 'No irreversible customer action was taken automatically.';
  }

  let recovery = 'No recovery steps were needed.';
  let recoveryStatus = 'No recovery needed';
  if (unrecoveredFailures > 0) {
    recovery = `${unrecoveredFailures} unrecovered failure${unrecoveredFailures > 1 ? 's' : ''} remained in the run.`;
    recoveryStatus = 'Unrecovered failure';
  } else if (recoveredFailures > 0) {
    recovery = `${recoveredFailures} failure${recoveredFailures > 1 ? 's' : ''} recovered before completion.`;
    recoveryStatus = 'Recovered before completion';
  }

  return {
    lead,
    detail,
    action,
    recovery,
    recoveryStatus,
    irreversibleAction: irreversibleTool === 'issue_refund' && refundAlreadyHandled
      ? 'No new refund issued'
      : irreversibleTool ? humanTool(irreversibleTool) : 'None',
    toolsUsed: tools.length ? tools.map(humanTool).join(', ') : 'No tool calls recorded',
  };
}

function DecisionSummary({ ticket, isDLQ }) {
  const summary = summarizeDecision(ticket, isDLQ);
  return (
    <div className="detail-section">
      <h3>Decision Summary</h3>
      <div className="summary-box">
        <p className="lead">{summary.lead}</p>
        <p>{summary.detail} {summary.action} {summary.recovery}</p>
      </div>
      <div className="kv-grid summary-grid">
        <div className="kv">
          <span className="k">Tools used</span>
          <span className="v">{summary.toolsUsed}</span>
        </div>
        <div className="kv">
          <span className="k">Irreversible action</span>
          <span className="v">{summary.irreversibleAction}</span>
        </div>
        <div className="kv">
          <span className="k">Customer reply</span>
          <span className="v">{ticket.reply ? 'Sent automatically' : 'None'}</span>
        </div>
        <div className="kv">
          <span className="k">Recovery status</span>
          <span className="v">{summary.recoveryStatus}</span>
        </div>
      </div>
    </div>
  );
}

// Right pane — ticket detail with tabs
function TicketDetail({ ticket }) {
  const [tab, setTab] = React.useState('trace');
  // Reset tab to trace when ticket changes
  React.useEffect(() => { setTab('trace'); }, [ticket?.id]);

  if (!ticket) {
    return (
      <div className="detail">
        <div className="detail-empty">
          <div style={{fontFamily:'var(--font-mono)', color:'var(--fg-4)', fontSize:11, letterSpacing: 2}}>NO TICKET SELECTED</div>
          <div style={{marginTop:6}}>Choose a ticket from the list to inspect its trace.</div>
        </div>
      </div>
    );
  }

  const toolCount = ticket.trace.filter(s => s.kind === 'tool').length;
  const errCount = ticket.trace.filter(s => s.status === 'error').length;
  const retryCount = ticket.trace.filter(s => s.kind === 'recover').length;
  const unrecoveredFailures = (ticket.failures || []).filter(f => !f.recovered);
  const isDLQ = unrecoveredFailures.length > 0;
  const hasRecovery = errCount > 0 || retryCount > 0 || isDLQ;
  const tin = ticket.tokens && ticket.tokens.in != null ? ticket.tokens.in : null;
  const tout = ticket.tokens && ticket.tokens.out != null ? ticket.tokens.out : null;
  return (
    <div className="detail">
      <div className="detail-head">
        <div className="crumbs">
          <span>run/{window.RUN_META.run_id}</span>
          <span style={{color:'var(--fg-4)'}}>›</span>
          <span style={{color:'var(--fg-1)'}}>{ticket.id}</span>
          <span style={{marginLeft:'auto', display:'flex', gap:10}}>
            <PriorityPill p={ticket.priority} />
            <span className="cat-pill">{humanizeCategory(ticket.category)}</span>
            <OutcomePill outcome={isDLQ ? 'dlq' : ticket.outcome} />
          </span>
        </div>
        <h2>{ticket.subject}</h2>
        <div className="meta-row">
          <span><span className="k">from</span><span className="v">{ticket.customer.name}</span></span>
          <span><span className="k">email</span><span className="v">{ticket.customer.email}</span></span>
          <span><span className="k">tier</span><span className="v">{ticket.customer.tier}</span></span>
          {ticket.order_id && <span><span className="k">order</span><span className="v">{ticket.order_id}</span></span>}
          <span><span className="k">source</span><span className="v">{ticket.source || '—'}</span></span>
          {ticket.received_at && <span><span className="k">received</span><span className="v">{fmt.time(ticket.received_at)}</span></span>}
          <span><span className="k">duration</span><span className="v">{fmt.ms(ticket.duration_ms || 0)}</span></span>
          <span>
            <span className="k">tokens</span>
            <span className="v">
              {tin != null ? `${tin}↓` : '—'} {tout != null ? `${tout}↑` : '—'}
            </span>
          </span>
        </div>
      </div>

      <div className="detail-tabs">
        <button className={`tab ${tab==='trace'?'active':''}`} onClick={()=>setTab('trace')}>Trace <span className="count">{ticket.trace.length}</span></button>
        <button className={`tab ${tab==='recovery'?'active':''}`} onClick={()=>setTab('recovery')}>
          Recovery{hasRecovery ? <span className="count" style={{color:'var(--warn)'}}>●</span> : null}
        </button>
        <button className={`tab ${tab==='outcome'?'active':''}`} onClick={()=>setTab('outcome')}>
          {ticket.outcome === 'resolved' ? 'Reply' : ticket.outcome === 'declined' ? 'Decline' : 'Escalation'}
        </button>
        <button className={`tab ${tab==='raw'?'active':''}`} onClick={()=>setTab('raw')}>Raw JSON</button>
      </div>

      <div className="detail-body">
        {tab === 'trace' && <TraceTab ticket={ticket} />}
        {tab === 'recovery' && <RecoveryTab ticket={ticket} />}
        {tab === 'outcome' && <OutcomeTab ticket={ticket} />}
        {tab === 'raw' && <RawTab ticket={ticket} />}
      </div>
    </div>
  );
}

function TraceTab({ ticket }) {
  const priorTickets = ticket.customer?.prior_tickets ?? 0;
  const isDLQ = (ticket.failures || []).some(f => !f.recovered);
  return (
    <>
      <div className="detail-section">
        <h3>Customer Message</h3>
        <div className="body-box">
          <div className="from">{ticket.customer.name} &lt;{ticket.customer.email}&gt; · {ticket.source}</div>
          {ticket.body}
        </div>
      </div>
      <div className="detail-section">
        <h3>Classification</h3>
        <div className="class-grid">
          <div className="kv fact-stack">
            <div className="kv-inline">
              <span className="k">Category</span>
              <span className="v">{humanizeCategory(ticket.category)}</span>
            </div>
            <div className="kv-inline">
              <span className="k">Prior Tickets</span>
              <span className="v">{priorTickets}</span>
            </div>
          </div>
          <div className="kv">
            <span className="k">Classified</span>
            <span className="v"><ConfBar v={ticket.classified_confidence}/></span>
          </div>
          <div className="kv">
            <span className="k">Agent Confidence</span>
            <span className="v"><ConfBar v={ticket.agent_confidence}/></span>
          </div>
          <div className="kv">
            <span className="k">Why this decision was made</span>
            <span className="v">{basisLabel(ticket.decision_basis)}</span>
          </div>
          <div className="kv">
            <span className="k">Auto-Replied</span>
            <span className="v">{ticket.auto_replied ? 'yes' : 'no'}</span>
          </div>
        </div>
        {ticket.expected_action && (
          <div className="expected-box">
            <div className="k">Expected Action</div>
            <div className="v">{ticket.expected_action}</div>
          </div>
        )}
        {ticket.fraud_flags && ticket.fraud_flags.length > 0 && (
          <div style={{marginTop: 12}}>
            <div className="k" style={{fontSize:10, color:'var(--fg-3)', textTransform:'uppercase', letterSpacing:'0.05em', marginBottom: 6}}>Fraud signals</div>
            <div className="flag-list">
              {ticket.fraud_flags.map(f => <span key={f} className="flag">⚠ {f}</span>)}
            </div>
          </div>
        )}
      </div>
      <DecisionSummary ticket={ticket} isDLQ={isDLQ} />
      <div className="detail-section">
        <h3>Reasoning Trace</h3>
        <TraceViewer ticket={ticket} />
      </div>
    </>
  );
}

function RecoveryTab({ ticket }) {
  const failures = ticket.failures || [];
  const unrecovered = failures.filter(f => !f.recovered);
  const retries = ticket.trace.filter(s => s.kind === 'recover');
  const errs = ticket.trace.filter(s => s.status === 'error');
  const totalRetries = failures.reduce((n, f) => n + (f.retry_count || 0), 0) || retries.length;
  const recovered = failures.length > 0 && unrecovered.length === 0;
  const isDLQ = unrecovered.length > 0;

  return (
    <>
      <div className="detail-section">
        <h3>Recovery Posture</h3>
        <div className="recov">
          <div>
            <div className="k">Retry count</div>
            <div className={`v ${totalRetries ? 'warn' : ''}`}>{totalRetries}</div>
          </div>
          <div>
            <div className="k">Failures</div>
            <div className={`v ${failures.length ? 'err' : ''}`}>{failures.length}</div>
          </div>
          <div>
            <div className="k">Recovered</div>
            <div className={`v ${recovered ? 'ok' : (isDLQ ? 'err' : '')}`}>
              {failures.length === 0 ? 'n/a' : (recovered ? 'yes' : 'no')}
            </div>
          </div>
          <div>
            <div className="k">DLQ</div>
            <div className={`v ${isDLQ ? 'err' : ''}`}>{isDLQ ? 'yes' : 'no'}</div>
          </div>
        </div>
      </div>
      <div className="detail-section">
        <h3>Failure Events</h3>
        {errs.length === 0 && retries.length === 0 && failures.length === 0 ? (
          <div style={{color:'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)'}}>
            // no failures recorded for this ticket — clean path
          </div>
        ) : (
          <div className="trace">
            {ticket.trace
              .filter(s => s.status === 'error' || s.kind === 'recover')
              .map((s, i) => <TraceRow key={i} step={s} />)}
            {failures.length > 0 && ticket.trace.every(s => s.status !== 'error' && s.kind !== 'recover') && (
              // Snapshot-mode fallback: no live trace rows, render from failures array.
              failures.map((f, i) => (
                <div key={`f-${i}`} className={`trace-row ${f.recovered ? 'recover' : 'err'}`}>
                  <div className="t">+0ms</div>
                  <div className="gutter">│</div>
                  <div><span className={`icon ${f.recovered ? 'recover' : 'tool err'}`}>{f.recovered ? '↻' : '!'}</span></div>
                  <div className="body">
                    <div className="line1">
                      <span>{f.tool}</span>
                      <span className="err-line"> → {f.error}</span>
                      <span className="attempt">×{f.retry_count}</span>
                    </div>
                    <div className="note">// {f.recovered ? 'recovered' : 'dead-lettered'}</div>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
      {isDLQ && (
        <div className="detail-section">
          <h3>DLQ Payload</h3>
          <div className="esc-box">
            <div className="hd"><span>dead-letter · {unrecovered.map(f => f.tool).join(', ')}</span><span className="mono">{ticket.id}</span></div>
            <div className="body">{ticket.escalation_summary || 'unrecovered tool failure — see failures array'}</div>
          </div>
        </div>
      )}
    </>
  );
}

function OutcomeTab({ ticket }) {
  if (ticket.outcome === 'resolved' && ticket.reply) {
    return (
      <div className="detail-section">
        <h3>Customer Reply</h3>
        <div className="reply-box">
          <div className="hd">
            <span>send_reply · to: {ticket.customer.email}</span>
            <span style={{color:'var(--accent)'}}>● SENT</span>
          </div>
          <div className="body">{ticket.reply}</div>
        </div>
        <div style={{marginTop: 14}} className="kv-grid">
          <div className="kv"><span className="k">Channel</span><span className="v">{ticket.source || '—'}</span></div>
          <div className="kv"><span className="k">Agent confidence</span><span className="v">{fmt.pct(ticket.agent_confidence)}</span></div>
          <div className="kv"><span className="k">Basis</span><span className="v">{basisLabel(ticket.decision_basis)}</span></div>
        </div>
      </div>
    );
  }
  if (ticket.outcome === 'pending' || ticket.outcome === 'running') {
    return (
      <div className="detail-section">
        <div style={{color:'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)', textAlign:'center', padding: 24}}>
          // awaiting agent decision — outcome will appear here
        </div>
      </div>
    );
  }
  return (
    <div className="detail-section">
      <h3>{ticket.outcome === 'declined' ? 'Decline Summary' : 'Escalation Summary'}</h3>
      <div className="esc-box">
        <div className="hd">
          <span>{ticket.outcome} · basis: {basisLabel(ticket.decision_basis)}</span>
          <span className="mono">{ticket.priority}</span>
        </div>
        <div className="body">{ticket.escalation_summary || '// no summary recorded'}</div>
      </div>
    </div>
  );
}

function RawTab({ ticket }) {
  return (
    <div className="detail-section">
      <div className="raw">{prettyJSON(ticket)}</div>
    </div>
  );
}

Object.assign(window, { TicketDetail });
