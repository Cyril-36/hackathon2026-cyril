// Small shared UI bits
const fmt = {
  ms: (n) => n < 1000 ? `${Math.round(n)}ms` : `${(n/1000).toFixed(2)}s`,
  pct: (n) => `${Math.round((n || 0) * 100)}%`,
  time: (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    return d.toISOString().replace('T', ' ').slice(5, 19) + 'Z';
  },
};

// Real decision_basis values emitted by the agent. Keep the wire values
// raw — this map is only for display.
const BASIS_LABEL = {
  successful_resolution: 'Resolved',
  policy_guard: 'Guarded',
  recovered_and_resolved: 'Recovered',
  tool_failure: 'Tool Failure',
  fraud_detected: 'Fraud',
  low_confidence: 'Low Confidence',
  pending: 'Pending',
  running: 'Running',
};

// Which CSS variable each basis should pull its color from.
const BASIS_CSS = {
  successful_resolution: 'success',
  policy_guard: 'guard',
  recovered_and_resolved: 'recovered',
  tool_failure: 'toolfail',
  fraud_detected: 'fraud',
  low_confidence: 'guard',
  pending: 'pending',
  running: 'pending',
};

function basisLabel(basis) {
  return BASIS_LABEL[basis] || basis || 'Unknown';
}
function basisCssVar(basis) {
  return `var(--basis-${BASIS_CSS[basis] || 'pending'})`;
}

function humanizeCategory(category) {
  if (!category) return '—';
  return String(category)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

const BasisDot = ({ basis }) => (
  <span
    className="basis-dot"
    style={{ background: basisCssVar(basis) }}
    title={`decision: ${basisLabel(basis)}`}
  />
);

const OutcomePill = ({ outcome }) => {
  const out = outcome || 'pending';
  const label = ({
    resolved: 'Resolved',
    escalated: 'Escalated',
    declined: 'Declined',
    pending: 'Pending',
    running: 'Running',
    dlq: 'Failed',
  })[out] || out;
  return (
    <span className={`outcome-pill ${out}`}>
      <span className="dot" />{label}
    </span>
  );
};

const PriorityPill = ({ p }) => (
  <span className={`priority-pill ${p.toLowerCase()}`}>{p}</span>
);

const ConfBar = ({ v }) => {
  const cls = v >= 0.8 ? 'hi' : v >= 0.6 ? 'mid' : 'lo';
  return (
    <span>
      <span className="mono">{fmt.pct(v)}</span>
      <span className={`conf-bar ${cls}`}><span style={{ width: `${v*100}%` }} /></span>
    </span>
  );
};

// Tiny JSON pretty-printer for the Raw tab
function prettyJSON(obj, indent = 0) {
  const pad = (n) => '  '.repeat(n);
  if (obj === null) return <span className="null">null</span>;
  if (typeof obj === 'boolean') return <span className="bool">{String(obj)}</span>;
  if (typeof obj === 'number') return <span className="num">{obj}</span>;
  if (typeof obj === 'string') return <span className="str">"{obj}"</span>;
  if (Array.isArray(obj)) {
    if (obj.length === 0) return <>[]</>;
    return (
      <>
        {'[\n'}
        {obj.map((v, i) => (
          <React.Fragment key={i}>
            {pad(indent+1)}{prettyJSON(v, indent+1)}{i < obj.length-1 ? ',' : ''}{'\n'}
          </React.Fragment>
        ))}
        {pad(indent)}{']'}
      </>
    );
  }
  if (typeof obj === 'object') {
    const keys = Object.keys(obj);
    if (keys.length === 0) return <>{'{}'}</>;
    return (
      <>
        {'{\n'}
        {keys.map((k, i) => (
          <React.Fragment key={k}>
            {pad(indent+1)}<span className="key">"{k}"</span>{': '}{prettyJSON(obj[k], indent+1)}{i < keys.length-1 ? ',' : ''}{'\n'}
          </React.Fragment>
        ))}
        {pad(indent)}{'}'}
      </>
    );
  }
  return String(obj);
}

Object.assign(window, {
  fmt, BasisDot, OutcomePill, PriorityPill, ConfBar, prettyJSON,
  BASIS_LABEL, BASIS_CSS, basisLabel, basisCssVar, humanizeCategory,
});
