// Tweaks panel — mode, chaos rate, seed, density, accent hue
function Tweaks({ open, onClose, settings, setSettings, modeAvailability }) {
  const set = (k, v) => setSettings({ ...settings, [k]: v });

  React.useEffect(() => {
    document.documentElement.style.setProperty('--accent', settings.accentHue === 'green'
      ? 'oklch(0.72 0.15 145)'
      : settings.accentHue === 'cyan' ? 'oklch(0.78 0.14 200)'
      : settings.accentHue === 'violet' ? 'oklch(0.72 0.17 290)'
      : 'oklch(0.8 0.17 85)');
  }, [settings.accentHue]);

  const rate = typeof settings.chaosRate === 'number' ? settings.chaosRate : 0;
  const seedRaw = settings.seed === null || settings.seed === undefined ? '' : String(settings.seed);

  return (
    <div className={`tweaks ${open ? 'open' : ''}`}>
      <div className="hd">
        <span>Tweaks</span>
        <button className="btn ghost" style={{padding:'2px 6px', fontSize: 11}} onClick={onClose}>×</button>
      </div>
      <div className="row2">
        <div className="label" style={{display:'flex', justifyContent:'space-between'}}>
          <span>Chaos injection rate</span>
          <span className="mono" style={{color:'var(--fg-1)'}}>{rate.toFixed(2)}</span>
        </div>
        <input
          type="range"
          min="0" max="1" step="0.01"
          value={rate}
          onChange={(e) => set('chaosRate', parseFloat(e.target.value))}
          style={{width:'100%'}}
        />
        <div style={{color:'var(--fg-3)', fontSize: 10, fontFamily:'var(--font-mono)'}}>
          0 = clean · 0.15 = seeded demo · 0.30 = stress · 1.0 = every tool fails
        </div>
      </div>
      <div className="row2">
        <div className="label">Seed</div>
        <input
          type="number"
          value={seedRaw}
          placeholder="42 (leave blank for server default)"
          onChange={(e) => {
            const v = e.target.value;
            if (v === '') { set('seed', null); return; }
            const n = parseInt(v, 10);
            if (!Number.isNaN(n)) set('seed', n);
          }}
          style={{
            background: 'var(--bg-2)', border: '1px solid var(--border)', borderRadius: 4,
            color: 'var(--fg-1)', fontFamily: 'var(--font-mono)', fontSize: 11,
            padding: '4px 8px', width: '100%', outline: 'none',
          }}
        />
        <div style={{color:'var(--fg-3)', fontSize: 10, fontFamily:'var(--font-mono)'}}>
          fixed seed = byte-identical chaos run every time
        </div>
      </div>
      <div className="row2 toggle">
        <div>
          <div className="label">Auto-open top ticket</div>
          <div style={{color:'var(--fg-3)', fontSize: 10, fontFamily:'var(--font-mono)'}}>select first ticket on load</div>
        </div>
        <button className={`switch ${settings.autoSelect ? 'on' : ''}`} onClick={() => set('autoSelect', !settings.autoSelect)} />
      </div>
      <div className="row2">
        <div className="label">Accent hue</div>
        <div className="seg">
          {['green','cyan','violet','amber'].map(h => (
            <button key={h} className={settings.accentHue === h ? 'active' : ''} onClick={() => set('accentHue', h)}>{h}</button>
          ))}
        </div>
      </div>
      <div className="row2">
        <div className="label">Density</div>
        <div className="seg">
          {['high','medium'].map(d => (
            <button key={d} className={settings.density === d ? 'active' : ''} onClick={() => set('density', d)}>{d}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Tweaks });
