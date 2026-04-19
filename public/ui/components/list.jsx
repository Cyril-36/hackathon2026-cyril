// Left pane: search, filter chips, ticket table
function TicketList({ tickets, selectedId, onSelect, filter, setFilter, search, setSearch }) {
  const isDLQ = (t) => (t.failures || []).some(f => !f.recovered);

  // Track tickets that just flipped from pending → outcome for flash animation.
  const [justDone, setJustDone] = React.useState({});
  const prevOutcomes = React.useRef({});

  React.useEffect(() => {
    const updates = {};
    tickets.forEach(t => {
      const prev = prevOutcomes.current[t.id];
      if (prev === 'pending' && t.outcome && t.outcome !== 'pending') {
        updates[t.id] = t.outcome; // e.g. 'resolved', 'escalated', 'declined'
      }
      prevOutcomes.current[t.id] = t.outcome;
    });
    if (Object.keys(updates).length > 0) {
      setJustDone(prev => ({ ...prev, ...updates }));
      // Clear flash class after animation completes (0.8s)
      const ids = Object.keys(updates);
      setTimeout(() => setJustDone(prev => {
        const next = { ...prev };
        ids.forEach(id => delete next[id]);
        return next;
      }), 900);
    }
  }, [tickets]);

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = tickets.filter(t => {
      if (filter === 'dlq') { if (!isDLQ(t)) return false; }
      else if (filter !== 'all' && t.outcome !== filter) return false;
      if (!q) return true;
      return (
        (t.id || '').toLowerCase().includes(q) ||
        (t.subject || '').toLowerCase().includes(q) ||
        (t.category || '').toLowerCase().includes(q) ||
        ((t.customer && t.customer.name) || '').toLowerCase().includes(q)
      );
    });
    return matches;
  }, [tickets, filter, search]);

  const counts = React.useMemo(() => ({
    all: tickets.length,
    resolved: tickets.filter(t => t.outcome === 'resolved').length,
    escalated: tickets.filter(t => t.outcome === 'escalated').length,
    declined: tickets.filter(t => t.outcome === 'declined').length,
    dlq: tickets.filter(isDLQ).length,
  }), [tickets]);

  return (
    <div className="listpane">
      <div className="listpane-head">
        <div className="search">
          <span style={{color:'var(--fg-3)', fontSize:11}}>⌕</span>
          <input
            placeholder="Search tickets, customer, category…"
            aria-label="Search tickets by ID, subject, customer, or category"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <span className="hint">{filtered.length}/{tickets.length}</span>
        </div>
      </div>
      <div className="filter-chips">
        {[['all','All'],['resolved','Resolved'],['escalated','Escalated'],['declined','Declined'],['dlq','Failed Queue']].map(([k, lbl]) => (
          <button
            key={k}
            className={`chip ${filter === k ? 'active' : ''}`}
            onClick={() => setFilter(k)}
          >
            {lbl} <span className="x">{counts[k]}</span>
          </button>
        ))}
      </div>
      <div className="table-head">
        <div>ID</div>
        <div>Subject · Customer</div>
        <div>Category</div>
        <div>Outcome</div>
        <div>Confidence</div>
        <div style={{textAlign:'right'}}>Priority</div>
      </div>
      <div className="rows">
        {filtered.map(t => {
          const flashOutcome = justDone[t.id];
          const flashClass = flashOutcome ? `just-${flashOutcome}` : '';
          return (
            <div
              key={t.id}
              className={`row ${t.id === selectedId ? 'active' : ''} ${flashClass}`}
              tabIndex={0}
              role="button"
              aria-pressed={t.id === selectedId}
              onClick={() => onSelect(t.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelect(t.id);
                }
              }}
            >
              <div className="tid">
                {isDLQ(t) && <span className="pin" title="unrecovered failure (failed queue)">⚠</span>}
                {t.id}
              </div>
              <div className="subject" title={t.subject}>
                {t.subject}
                <span className="sub-meta"> · {t.customer && t.customer.name}</span>
              </div>
              <div><span className="cat-pill">{humanizeCategory(t.category)}</span></div>
              <div className="outcome-cell">
                <OutcomePill outcome={isDLQ(t) ? 'dlq' : t.outcome} />
              </div>
              <div className={`conf mono ${(t.agent_confidence || 0) < 0.6 ? 'low' : (t.agent_confidence || 0) < 0.8 ? 'mid' : 'hi'}`}>
                {fmt.pct(t.agent_confidence)}
              </div>
              <div style={{textAlign:'right'}}><PriorityPill p={t.priority || 'P3'} /></div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)' }}>
            No tickets match.
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { TicketList });
