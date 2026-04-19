// Reasoning trace viewer — the hero feature
function TraceViewer({ ticket }) {
  const totalMs = ticket.trace.reduce((max, s) => {
    const end = s.t + (s.ms || 0);
    return end > max ? end : max;
  }, 0);
  const toolCalls = ticket.trace.filter(s => s.kind === 'tool');
  const retries = ticket.trace.filter(s => s.kind === 'recover').length;
  const errs = ticket.trace.filter(s => s.status === 'error').length;

  return (
    <div>
      <div className="trace">
        {ticket.trace.map((s, i) => <TraceRow key={i} step={s} />)}
        <div className="trace-summary">
          <span><b>{ticket.trace.length}</b> steps</span>
          <span><b>{toolCalls.length}</b> tool calls</span>
          <span><b>{retries}</b> retries</span>
          <span className={errs ? 'warn' : ''} style={{color: errs ? 'var(--warn-fg)' : ''}}><b>{errs}</b> errors</span>
          <span style={{marginLeft:'auto'}}><b>{fmt.ms(Math.max(totalMs, ticket.duration_ms || 0))}</b> wall-clock</span>
        </div>
      </div>
    </div>
  );
}

function TraceRow({ step }) {
  const isErr = step.status === 'error';
  const isWrite = step.kind === 'tool' && window.TOOLS.find(t => t.id === step.tool)?.kind === 'write' && !isErr;

  const rowCls = [
    'trace-row',
    isErr && 'err',
    step.kind === 'decide' && 'decide',
    step.kind === 'recover' && 'recover',
    isWrite && 'write',
  ].filter(Boolean).join(' ');

  let icon, iconCls;
  if (step.kind === 'classify') { icon = 'C'; iconCls = 'classify'; }
  else if (step.kind === 'decide') { icon = '◆'; iconCls = 'decide'; }
  else if (step.kind === 'recover') { icon = '↻'; iconCls = 'recover'; }
  else if (step.kind === 'tool') {
    icon = isErr ? '!' : (isWrite ? '⇒' : '·');
    iconCls = `tool ${isErr ? 'err' : (isWrite ? 'write' : '')}`;
  }

  return (
    <div className={rowCls}>
      <div className="t">+{String(step.t).padStart(4,' ')}ms</div>
      <div className="gutter">│</div>
      <div><span className={`icon ${iconCls}`}>{icon}</span></div>
      <div className="body">
        {step.kind === 'classify' && (
          <>
            <div className="line1">
              <span>classify_intent → <b>{step.result || '—'}</b></span>
              <span className="ms">{fmt.ms(step.ms)} · conf {fmt.pct(step.conf)}</span>
            </div>
            {step.note && <div className="note">// {compactText(step.note, 88)}</div>}
          </>
        )}
        {step.kind === 'tool' && (
          <>
            <div className="line1">
              <span style={{color: isWrite ? 'var(--accent)' : 'var(--fg)'}}>{step.tool}</span>
              <span className="args">({argsPreview(step.args)})</span>
              {isErr
                ? <span className="err-line"> → {step.error}</span>
                : step.result && <span className="args"> → {resultPreview(step.result)}</span>}
              <span className="ms">{fmt.ms(step.ms)}</span>
              {step.attempt && step.attempt > 1 && <span className="attempt">attempt {step.attempt}</span>}
            </div>
          </>
        )}
        {step.kind === 'decide' && (
          <>
            <div className="line1">
              <span style={{color:'var(--basis-hybrid)'}}>{step.label || 'decide'}</span>
              <span className="ms">{fmt.ms(step.ms)}</span>
            </div>
            {step.note && <div className="note">// {compactText(step.note, 120)}</div>}
          </>
        )}
        {step.kind === 'recover' && (
          <>
            <div className="line1">
              <span style={{color:'var(--warn)'}}>{step.label || 'recover'}</span>
              <span className="ms">{fmt.ms(step.ms)}</span>
            </div>
            {step.note && <div className="note">// {compactText(step.note, 120)}</div>}
          </>
        )}
      </div>
    </div>
  );
}

function argsPreview(args) {
  if (!args) return '';
  const raw = Object.entries(args).map(([k,v]) => {
    const vs = typeof v === 'string' ? `"${v}"` : JSON.stringify(v);
    return `${k}: ${vs}`;
  }).join(', ');
  return compactText(raw, 76);
}
function resultPreview(res) {
  if (!res) return 'ok';
  const entries = Object.entries(res).slice(0, 2);
  const text = '{ ' + entries.map(([k,v]) => {
    const vs = typeof v === 'string' ? `"${v}"` : (typeof v === 'object' ? '…' : String(v));
    return `${k}: ${vs}`;
  }).join(', ') + (Object.keys(res).length > 2 ? ', …' : '') + ' }';
  return compactText(text, 92);
}

function compactText(text, limit = 96) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (value.length <= limit) return value;
  return value.slice(0, limit - 1) + '…';
}

Object.assign(window, { TraceViewer });
