// ShopWave Support Console — thin network layer.
// Exposes window.api with three methods the UI uses.

window.api = (function () {
  async function snapshot(runId) {
    // runId: undefined/null = clean audit_log.json
    //        'latest'         = most recent file in runs/
    //        '<id>'           = specific run archive
    const url = runId
      ? `/api/snapshot?run_id=${encodeURIComponent(runId)}`
      : '/api/snapshot';
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error('snapshot failed: ' + r.status);
    return r.json();
  }

  async function health() {
    const r = await fetch('/api/health', { cache: 'no-store' });
    if (!r.ok) throw new Error('health failed: ' + r.status);
    return r.json();
  }

  async function tickets() {
    const r = await fetch('/api/tickets', { cache: 'no-store' });
    if (!r.ok) throw new Error('tickets failed: ' + r.status);
    return r.json();
  }

  async function startRun(opts) {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts || {}),
    });
    if (!r.ok) {
      let msg = 'run failed: ' + r.status;
      try { msg += ' ' + (await r.text()).slice(0, 200); } catch {}
      throw new Error(msg);
    }
    return r.json();
  }

  /** Subscribe to live events for a run.
   *  Returns an unsubscribe function.
   *  onEvent is called with the parsed event payload. */
  function subscribe(runId, onEvent, onDone, onError) {
    const es = new EventSource(`/api/events?run_id=${encodeURIComponent(runId)}`);
    es.onmessage = (m) => {
      try {
        const data = JSON.parse(m.data);
        onEvent && onEvent(data);
        if (data.type === 'run_done') {
          es.close();
          onDone && onDone(data);
        }
      } catch (e) {
        // swallow parse errors — keep the stream alive
        console.warn('event parse error', e);
      }
    };
    es.addEventListener('done', () => {
      es.close();
      onDone && onDone(null);
    });
    es.onerror = (e) => {
      // EventSource auto-reconnects on transient errors. Surface only
      // the final state (readyState CLOSED) to the caller.
      if (es.readyState === EventSource.CLOSED) {
        onError && onError(e);
      }
    };
    return () => es.close();
  }

  async function dlq() {
    const r = await fetch('/api/dlq', { cache: 'no-store' });
    if (!r.ok) return [];
    return r.json();
  }

  return { snapshot, tickets, startRun, subscribe, dlq, health };
})();
