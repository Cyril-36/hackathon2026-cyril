// Main App — boots from /api/snapshot, runs the real agent via /api/run,
// and streams live events over /api/events (SSE). The old mock setTimeout
// simulation is gone — every ticket, trace row and stat you see is produced
// by the Python agent.
const { useState, useEffect, useMemo, useRef, useReducer, useCallback } = React;

const DEFAULT_SETTINGS = /*EDITMODE-BEGIN*/{
  "mode": "rules",
  "chaosRate": 0.0,
  "seed": 42,
  "autoSelect": true,
  "accentHue": "green",
  "density": "high"
}/*EDITMODE-END*/;

// Migrate old { chaos: bool } localStorage payloads to { chaosRate: number }.
function migrateSettings(raw) {
  if (!raw) return {};
  const out = { ...raw };
  if ('chaos' in out && !('chaosRate' in out)) {
    out.chaosRate = out.chaos ? 0.30 : 0.0;
    delete out.chaos;
  }
  return out;
}

// ---- Reducer -------------------------------------------------------------
// All mutations funnel through here so React notices. The reducer delegates
// event application to window.TicketStore.applyEvent (from data.js).

const EMPTY_META = {
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

function initialState() {
  return {
    tickets: [],
    tools: [],
    meta: { ...EMPTY_META },
    stats: window.TicketStore.recomputeStats([]),
    // UI-only flags:
    running: false,
    runId: null,
    error: null,
  };
}

function rootReducer(state, action) {
  switch (action.type) {
    case 'snapshot': {
      const { meta, tools, tickets, stats } = action.payload;
      return {
        ...state,
        meta: { ...EMPTY_META, ...meta },
        tools: tools || [],
        tickets: (tickets || []).map(t => ({ ...t })),
        stats: stats || window.TicketStore.recomputeStats(tickets || []),
      };
    }
    case 'run_started': {
      // Reset ticket state for a fresh run. We keep the fixtures but drop
      // any prior outcomes so the live stream rewrites them.
      const fresh = state.tickets.map(t => ({
        ...t,
        outcome: 'pending',
        decision_basis: 'pending',
        agent_confidence: 0,
        classified_confidence: 0,
        evidence_confidence: 0,
        trace: [],
        failures: [],
        tools_used: [],
        recovery_attempted: false,
        reply: null,
        escalation_summary: null,
        duration_ms: 0,
        __started_ms: null,
      }));
      return {
        ...state,
        tickets: fresh,
        stats: window.TicketStore.recomputeStats(fresh),
        running: true,
        runId: action.runId,
        meta: { ...state.meta, run_id: action.runId, started_at: new Date().toISOString(), ended_at: '', duration_ms: 0 },
        error: null,
      };
    }
    case 'event': {
      const tickets = state.tickets.slice();
      const meta = { ...state.meta };
      const changed = window.TicketStore.applyEvent(tickets, action.ev, meta);
      if (!changed) return state;
      // Reassign references so React sees deltas on the mutated ticket.
      const mutatedId = action.ev.ticket_id;
      if (mutatedId) {
        const i = tickets.findIndex(t => t.id === mutatedId);
        if (i >= 0) tickets[i] = { ...tickets[i], trace: tickets[i].trace.slice() };
      }
      return {
        ...state,
        tickets,
        meta,
        stats: window.TicketStore.recomputeStats(tickets),
      };
    }
    case 'run_done': {
      return {
        ...state,
        running: false,
        meta: {
          ...state.meta,
          ended_at: new Date().toISOString(),
          duration_ms: state.meta.started_at
            ? (Date.now() - new Date(state.meta.started_at).getTime())
            : state.meta.duration_ms,
        },
      };
    }
    case 'run_error': {
      return { ...state, running: false, error: action.error || 'run failed' };
    }
    default:
      return state;
  }
}

// ---- Component -----------------------------------------------------------

function App() {
  const [state, dispatch] = useReducer(rootReducer, null, initialState);
  const { tickets, tools, meta, stats, running, error } = state;

  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const [settings, setSettings] = useState(() => {
    try {
      const saved = localStorage.getItem('sw_settings');
      return saved ? { ...DEFAULT_SETTINGS, ...migrateSettings(JSON.parse(saved)) } : DEFAULT_SETTINGS;
    } catch { return DEFAULT_SETTINGS; }
  });
  const [modeAvailability, setModeAvailability] = useState({ rules: true, hybrid: true, llm: true });
  const [lastRunId, setLastRunId] = useState(() => {
    try { return localStorage.getItem('sw_last_run_id') || null; } catch { return null; }
  });
  const [viewingRunId, setViewingRunId] = useState(null); // null == clean audit_log
  const [dlqCount, setDlqCount] = useState(0);
  const unsubRef = useRef(null);

  // Shim: some components still read window.TICKETS / TOOLS / RUN_META /
  // RUN_STATS directly (BasisDot and the TraceRow tool lookup). Keep them
  // synced with the reducer state so those components keep working without
  // a wholesale rewrite.
  useEffect(() => {
    window.TICKETS = tickets;
    window.TOOLS = tools;
    window.RUN_META = meta;
    window.RUN_STATS = stats;
  }, [tickets, tools, meta, stats]);

  // ---- Initial snapshot fetch (S3: hero fallback) -------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [snap, health] = await Promise.all([
          window.api.snapshot(),
          window.api.health().catch(() => null),
        ]);
        if (cancelled) return;
        dispatch({ type: 'snapshot', payload: snap });
        if (health && health.modes) setModeAvailability(health.modes);
      } catch (e) {
        if (cancelled) return;
        dispatch({ type: 'run_error', error: `snapshot failed: ${e.message}` });
      }
    })();
    return () => { cancelled = true; if (unsubRef.current) unsubRef.current(); };
  }, []);

  // Refresh persisted DLQ count when we land on a view or finish a run.
  const refreshDlq = useCallback(async () => {
    try {
      const rows = await window.api.dlq();
      setDlqCount(Array.isArray(rows) ? rows.length : 0);
    } catch { /* non-fatal */ }
  }, []);
  useEffect(() => { refreshDlq(); }, [refreshDlq]);

  // Load a specific run archive (or reset to clean audit_log on null).
  const loadSnapshot = useCallback(async (runId) => {
    try {
      const snap = await window.api.snapshot(runId);
      dispatch({ type: 'snapshot', payload: snap });
      setViewingRunId(runId || null);
      refreshDlq();
    } catch (e) {
      dispatch({ type: 'run_error', error: `snapshot failed: ${e.message}` });
    }
  }, [refreshDlq]);

  // Persist settings
  useEffect(() => {
    localStorage.setItem('sw_settings', JSON.stringify(settings));
  }, [settings]);

  // Restore/persist selection
  useEffect(() => {
    if (!tickets.length) return;
    const saved = localStorage.getItem('sw_selected');
    if (saved && tickets.find(t => t.id === saved)) {
      setSelectedId(saved);
    } else if (settings.autoSelect && !selectedId) {
      setSelectedId(tickets[0].id);
    }
  }, [tickets.length]);
  useEffect(() => {
    if (selectedId) localStorage.setItem('sw_selected', selectedId);
  }, [selectedId]);

  // Keyboard shortcuts
  const navigateSelection = useCallback((delta) => {
    const ids = tickets.map(t => t.id);
    const i = ids.indexOf(selectedId);
    if (i < 0) { if (ids.length) setSelectedId(ids[0]); return; }
    const n = (i + delta + ids.length) % ids.length;
    setSelectedId(ids[n]);
  }, [tickets, selectedId]);

  const runAgent = useCallback(async () => {
    if (running) return;
    if (modeAvailability[settings.mode] === false) {
      dispatch({ type: 'run_error', error: `${settings.mode} mode unavailable — set GROQ_API_KEY (or pick rules mode)` });
      return;
    }
    if (unsubRef.current) { unsubRef.current(); unsubRef.current = null; }
    try {
      const body = {
        mode: settings.mode,
        chaos: Math.max(0, Math.min(1, Number(settings.chaosRate) || 0)),
      };
      if (settings.seed !== null && settings.seed !== undefined && settings.seed !== '') {
        body.seed = Number(settings.seed);
      }
      const res = await window.api.startRun(body);
      dispatch({ type: 'run_started', runId: res.run_id });
      setLastRunId(res.run_id);
      setViewingRunId(res.run_id);
      try { localStorage.setItem('sw_last_run_id', res.run_id); } catch {}
      // Throttle ticket_done events so tickets reveal one-by-one (~100ms each),
      // matching the old mockup's setTimeout-based streaming animation.
      const _q = [];
      let _draining = false;
      let _serverDone = false;

      const _drain = () => {
        if (_q.length === 0) {
          _draining = false;
          if (_serverDone) { dispatch({ type: 'run_done' }); refreshDlq(); }
          return;
        }
        const ev = _q.shift();
        dispatch({ type: 'event', ev });
        // Space ticket_done events ~100ms apart; other events flow fast.
        const delay = ev.type === 'ticket_done' ? 70 + Math.random() * 90 : 8;
        setTimeout(_drain, delay);
      };

      unsubRef.current = window.api.subscribe(
        res.run_id,
        (ev) => {
          _q.push(ev);
          if (!_draining) {
            _draining = true;
            setTimeout(_drain, 200); // initial 200ms settle, like old mockup
          }
        },
        () => {
          _serverDone = true;
          if (!_draining && _q.length === 0) { dispatch({ type: 'run_done' }); refreshDlq(); }
        },
        (err) => dispatch({ type: 'run_error', error: String(err?.message || 'stream error') }),
      );
    } catch (e) {
      dispatch({ type: 'run_error', error: e.message });
    }
  }, [running, settings.mode, settings.chaosRate, settings.seed, modeAvailability, refreshDlq]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.tagName === 'INPUT') return;
      if ((e.metaKey || e.ctrlKey) && e.key === 'r') {
        e.preventDefault(); runAgent();
      } else if (e.key === '?') {
        setTweaksOpen(v => !v);
      } else if (e.key === 'j' || e.key === 'ArrowDown') {
        navigateSelection(1);
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        navigateSelection(-1);
      } else if (e.key === 'Escape') {
        setTweaksOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [runAgent, navigateSelection]);

  const selected = tickets.find(t => t.id === selectedId);

  // Host-toolbar Tweaks integration (postMessage protocol)
  useEffect(() => {
    const onMsg = (e) => {
      const d = e.data;
      if (!d || !d.type) return;
      if (d.type === '__activate_edit_mode') setTweaksOpen(true);
      else if (d.type === '__deactivate_edit_mode') setTweaksOpen(false);
    };
    window.addEventListener('message', onMsg);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', onMsg);
  }, []);

  const persistSettings = (newSettings) => {
    setSettings(newSettings);
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: newSettings }, '*');
  };

  // Overlay chaos onto the live meta so the banner + TopBar reflect the
  // slider even when no run is active yet.
  const chaosActive = (settings.chaosRate || 0) > 0 || !!meta.chaos;
  const chaosPct = Math.round((settings.chaosRate || 0) * 100);
  const displayMeta = { ...meta, mode: settings.mode, chaos: chaosActive };

  return (
    <div className="shell" style={{gridTemplateRows: displayMeta.chaos ? '44px 24px auto 1fr 24px' : '44px auto 1fr 24px'}}>
      <TopBar meta={displayMeta} />
      {chaosActive && (
        <div className="chaos-banner">
          <span className="dot" />
          chaos mode engaged · {chaosPct}% tool failure rate · seed {settings.seed ?? 'random'} · recovery & DLQ paths will fire
          <span style={{marginLeft:'auto', color: 'var(--fg-3)'}}>next run will re-inject</span>
        </div>
      )}
      <Dashboard
        stats={stats}
        meta={displayMeta}
        tickets={tickets}
        running={running}
        onRun={runAgent}
        settings={settings}
        setSettings={setSettings}
        modeAvailability={modeAvailability}
      />
      <div className="main">
        <TicketList
          tickets={tickets}
          selectedId={selectedId}
          onSelect={setSelectedId}
          filter={filter}
          setFilter={setFilter}
          search={search}
          setSearch={setSearch}
        />
        <TicketDetail ticket={selected} />
      </div>
      <StatusBar
        meta={displayMeta}
        stats={stats}
        running={running}
        selected={selectedId}
        error={error}
        dlqCount={dlqCount}
        viewingRunId={viewingRunId}
        lastRunId={lastRunId}
        onViewClean={() => loadSnapshot(null)}
        onViewLast={() => lastRunId && loadSnapshot(lastRunId)}
      />
      <Tweaks
        open={tweaksOpen}
        onClose={() => setTweaksOpen(false)}
        settings={settings}
        setSettings={persistSettings}
        modeAvailability={modeAvailability}
      />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
