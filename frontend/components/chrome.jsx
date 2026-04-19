// Top bar + dashboard strip
function TopBar({ meta }) {
  return (
    <div className="topbar">
      <div className="brand">
        <div className="brand-mark" />
        <span>ShopWave</span>
        <span className="brand-sep">/</span>
        <span className="brand-sub">Support Console</span>
      </div>
      <div className="topbar-spacer" />
      <div className="topbar-meta">
        <span className="meta-chip"><span className="k">run</span><span className="v">{meta.run_id || '—'}</span></span>
        <span className="meta-chip"><span className="k">policy</span><span className="v">{meta.policy_version || 'kb-v1.0'}</span></span>
        <span className="meta-chip"><span className="k">model</span><span className="v">{meta.model || '—'}</span></span>
        <span className="meta-chip"><span className="k">concurrency</span><span className="v">×{meta.concurrency || 1}</span></span>
      </div>
    </div>
  );
}

const BASIS_ORDER = [
  'successful_resolution',
  'policy_guard',
  'recovered_and_resolved',
  'tool_failure',
  'fraud_detected',
  'low_confidence',
  'pending',
  'running',
];

function Dashboard({ stats, meta, tickets, onRun, running, settings, setSettings, modeAvailability }) {
  const total = stats.total || 1;
  const avail = modeAvailability || { rules: true, hybrid: true, llm: true };
  const basisEntries = React.useMemo(() => {
    const byBasis = stats.by_basis || {};
    const ordered = [...new Set([...BASIS_ORDER, ...Object.keys(byBasis)])];
    return ordered
      .map((basis) => [basis, byBasis[basis] || 0])
      .filter(([, count]) => count > 0);
  }, [stats.by_basis]);
  return (
    <div className="dash">
      <div className="stat">
        <div className="label">Mode</div>
        <div className="control-stack">
          <select
            className="mode-select"
            value={settings.mode}
            onChange={(e) => setSettings({ ...settings, mode: e.target.value })}
            aria-label="Agent mode"
          >
            {['rules', 'hybrid', 'llm'].map((mode) => (
              <option key={mode} value={mode} disabled={avail[mode] === false}>
                {mode}{avail[mode] === false ? ' (unavailable)' : ''}
              </option>
            ))}
          </select>
          <span className="sub">Chaos testing: {meta.chaos ? 'On' : 'Off'}</span>
        </div>
      </div>
      <div className="stat">
        <div className="label">Tickets</div>
        <div className="value">{stats.total}<span className="sub">processed</span></div>
      </div>
      <div className="stat accent">
        <div className="label">Resolved</div>
        <div className="value">{stats.resolved}<span className="sub">{fmt.pct(stats.resolved/total)}</span></div>
      </div>
      <div className="stat warn">
        <div className="label">Escalated</div>
        <div className="value">{stats.escalated}<span className="sub">{fmt.pct(stats.escalated/total)}</span></div>
      </div>
      <div className="stat err">
        <div className="label">Failed Queue</div>
        <div className="value">{stats.dlq}<span className="sub">{stats.failed || 0} failed runs · {stats.recovered || 0} recovered</span></div>
      </div>
      <div className="stat">
        <div className="label">Avg Confidence</div>
        <div className="value">{fmt.pct(stats.avg_confidence)}<span className="sub">{stats.tool_calls || 0} tool calls</span></div>
      </div>
      <div className="stat">
        <div className="label">Decision Basis</div>
        <div style={{ display: 'flex', flexDirection:'column', gap: 2 }}>
          <div className="basis-bar">
            {basisEntries.map(([basis, count]) => (
              <span
                key={basis}
                style={{ width: `${(count / total) * 100}%`, background: basisCssVar(basis) }}
                title={`${basisLabel(basis)} · ${count}`}
              />
            ))}
          </div>
          <div className="basis-legend">
            {basisEntries.map(([basis, count]) => (
              <span key={basis}>
                <span className="dot" style={{ background: basisCssVar(basis) }} />
                {basisLabel(basis)} {count}
              </span>
            ))}
            {basisEntries.length === 0 && (
              <span style={{color:'var(--fg-4)'}}>no runs yet</span>
            )}
          </div>
        </div>
      </div>
      <div className="run-cell">
        <button className={`btn primary ${running ? 'running' : ''}`} onClick={onRun} disabled={running}>
          {running ? <><span className="dot" />Running…</> : <>▶ Run Agent</>}
        </button>
        <kbd>⌘R</kbd>
      </div>
    </div>
  );
}

function StatusBar({
  meta, stats, running, selected, error,
  dlqCount = 0, viewingRunId = null, lastRunId = null,
  onViewClean, onViewLast,
}) {
  const toolVer = (meta.tool_registry_version || 'tools-v1.0').replace(/^tools-v/, '');
  const tokIn = stats.tokens_in || 0;
  const tokOut = stats.tokens_out || 0;
  return (
    <div className="statusbar">
      <span><span className="ok">●</span> agent online</span>
      <span className="sep">│</span>
      <span>tools-v{toolVer}</span>
      <span className="sep">│</span>
      {viewingRunId ? (
        <span>
          viewing <b className="mono" style={{color:'var(--fg-1)'}}>{viewingRunId.slice(-14)}</b>
          <button
            className="btn ghost"
            style={{padding:'0 6px', fontSize: 10, marginLeft: 6}}
            onClick={onViewClean}
            title="reset to clean audit_log.json snapshot"
          >reset</button>
        </span>
      ) : (
        <span>
          last run {fmt.time(meta.started_at)}
          {lastRunId && (
            <button
              className="btn ghost"
              style={{padding:'0 6px', fontSize: 10, marginLeft: 6}}
              onClick={onViewLast}
              title={`load last web run: ${lastRunId}`}
            >▶ last web run</button>
          )}
        </span>
      )}
      <span className="sep">│</span>
      <span>duration {fmt.ms(meta.duration_ms || 0)}</span>
      {(tokIn || tokOut) > 0 && (
        <>
          <span className="sep">│</span>
          <span>{tokIn.toLocaleString()} in / {tokOut.toLocaleString()} out tokens</span>
        </>
      )}
      {dlqCount > 0 && (
        <>
          <span className="sep">│</span>
          <a
            href="/api/dlq"
            target="_blank"
            rel="noreferrer"
            className="err"
            title={`${dlqCount} dead-lettered entries persisted to dead_letter_queue.json`}
            style={{textDecoration:'none'}}
          >⚠ Failed Queue {dlqCount}</a>
        </>
      )}
      <div className="spacer" />
      {error && <span className="err">⚠ {error}</span>}
      {running && <span className="warn">⟳ streaming live run…</span>}
      {selected && <span>selected <b style={{color:'var(--fg-1)'}}>{selected}</b></span>}
      <span className="sep">│</span>
      <span><kbd>?</kbd> help</span>
    </div>
  );
}

Object.assign(window, { TopBar, Dashboard, StatusBar, BASIS_ORDER });
