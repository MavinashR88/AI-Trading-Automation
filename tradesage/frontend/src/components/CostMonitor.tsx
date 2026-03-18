import React, { useState, useEffect } from 'react';

const API = 'http://localhost:8000';

interface CostSummary {
  date: string;
  total_usd: number;
  budget_usd: number;
  budget_pct: number;
  total_calls: number;
  llm_mode: string;
  by_model: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_usd: number }>;
}

interface HistoryEntry {
  date: string;
  cost_usd: number;
  calls: number;
}

interface Props {
  onClose: () => void;
}

const MODE_COLORS: Record<string, string> = {
  testing: 'text-sky-400 bg-sky-900/40 border-sky-700',
  live:    'text-purple-400 bg-purple-900/40 border-purple-700',
  free:    'text-emerald-400 bg-emerald-900/40 border-emerald-700',
};

const MODE_DESC: Record<string, string> = {
  testing: 'Haiku for everything (~$0.05–0.20/day)',
  live:    'Sonnet heavy / Haiku light (~$0.30–0.80/day)',
  free:    'Ollama locally ($0/day)',
};

export function CostMonitorBadge({ onClick }: { onClick: () => void }) {
  const [summary, setSummary] = useState<CostSummary | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API}/api/cost/today`)
        .then(r => r.json())
        .then(setSummary)
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  if (!summary) return null;

  const pct = summary.budget_pct;
  const overBudget = pct >= 100;
  const warning = pct >= 80 && pct < 100;

  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-bold transition-all hover:opacity-80 ${
        overBudget ? 'bg-red-900/50 border-red-600 text-red-300 animate-pulse' :
        warning     ? 'bg-yellow-900/40 border-yellow-600 text-yellow-300' :
                     'bg-slate-800 border-slate-600 text-slate-300'
      }`}
      title="Click to open LLM cost monitor"
    >
      💰 ${summary.total_usd.toFixed(2)}/${summary.budget_usd.toFixed(2)}
      <span className={`px-1.5 py-0.5 rounded text-xs font-bold border ${MODE_COLORS[summary.llm_mode] || 'text-slate-400 bg-slate-800 border-slate-600'}`}>
        {summary.llm_mode.toUpperCase()}
      </span>
    </button>
  );
}

export default function CostMonitor({ onClose }: Props) {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [newMode, setNewMode] = useState('');
  const [newBudget, setNewBudget] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const load = () => {
    fetch(`${API}/api/cost/today`).then(r => r.json()).then(d => { setSummary(d); setNewMode(d.llm_mode); }).catch(() => {});
    fetch(`${API}/api/cost/history?days=7`).then(r => r.json()).then(d => setHistory(d.history || [])).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const saveMode = async () => {
    if (!newMode) return;
    setSaving(true);
    try {
      const r = await fetch(`${API}/api/llm-mode`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode }),
      });
      const d = await r.json();
      setMsg(`✓ Mode switched to ${d.mode.toUpperCase()}`);
      load();
    } catch { setMsg('Error saving mode'); }
    setSaving(false);
  };

  const saveBudget = async () => {
    const usd = parseFloat(newBudget);
    if (isNaN(usd) || usd < 0.10) { setMsg('Budget must be ≥ $0.10'); return; }
    setSaving(true);
    try {
      await fetch(`${API}/api/budget`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ budget_usd: usd }),
      });
      setMsg(`✓ Budget set to $${usd.toFixed(2)}/day`);
      load();
    } catch { setMsg('Error saving budget'); }
    setSaving(false);
  };

  const maxHistory = Math.max(...history.map(h => h.cost_usd), 0.01);

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <div>
            <h3 className="font-bold text-white">LLM Cost Monitor</h3>
            <p className="text-xs text-slate-400">Track and control AI spending</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl">✕</button>
        </div>

        <div className="p-4 space-y-4">
          {summary && (
            <>
              {/* Today's spend */}
              <div>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-slate-400">Today</span>
                  <span className="text-white font-mono">${summary.total_usd.toFixed(4)} / ${summary.budget_usd.toFixed(2)} ({summary.budget_pct.toFixed(0)}%)</span>
                </div>
                <div className="h-3 bg-slate-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${summary.budget_pct >= 100 ? 'bg-red-500' : summary.budget_pct >= 80 ? 'bg-yellow-500' : 'bg-indigo-500'}`}
                    style={{ width: `${Math.min(summary.budget_pct, 100)}%` }}
                  />
                </div>
                <p className="text-xs text-slate-500 mt-1">{summary.total_calls} API calls today</p>
              </div>

              {/* By model */}
              {Object.entries(summary.by_model).length > 0 && (
                <div className="space-y-1">
                  {Object.entries(summary.by_model).map(([model, stats]) => (
                    <div key={model} className="flex justify-between text-xs text-slate-400 bg-slate-800 rounded-lg px-3 py-2">
                      <span className="font-mono truncate">{model.replace('claude-', '')}</span>
                      <span>{stats.calls} calls · ${stats.cost_usd.toFixed(4)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Mode selector */}
              <div className="bg-slate-800 rounded-xl p-3 space-y-2">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">LLM Mode</p>
                <div className="flex gap-2">
                  {(['testing', 'live', 'free'] as const).map(m => (
                    <button key={m} onClick={() => setNewMode(m)}
                      className={`flex-1 py-1.5 text-xs font-bold rounded-lg border transition-all ${newMode === m ? MODE_COLORS[m] : 'bg-slate-700 border-slate-600 text-slate-400'}`}>
                      {m.toUpperCase()}
                    </button>
                  ))}
                </div>
                {newMode && <p className="text-xs text-slate-500">{MODE_DESC[newMode]}</p>}
                <button onClick={saveMode} disabled={saving || newMode === summary.llm_mode}
                  className="w-full py-1.5 text-sm font-bold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40">
                  {saving ? 'Saving…' : 'Apply Mode'}
                </button>
              </div>

              {/* Budget setter */}
              <div className="bg-slate-800 rounded-xl p-3 space-y-2">
                <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Daily Budget</p>
                <div className="flex gap-2">
                  <input
                    type="number" step="0.10" min="0.10"
                    placeholder={`Current: $${summary.budget_usd.toFixed(2)}`}
                    value={newBudget}
                    onChange={e => setNewBudget(e.target.value)}
                    className="flex-1 bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                  <button onClick={saveBudget} disabled={saving}
                    className="px-3 py-1.5 text-sm font-bold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-40">
                    Save
                  </button>
                </div>
              </div>
            </>
          )}

          {/* 7-day history */}
          {history.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Last 7 Days</p>
              <div className="flex items-end gap-1 h-16">
                {history.slice(0, 7).reverse().map(h => {
                  const heightPct = (h.cost_usd / maxHistory) * 100;
                  return (
                    <div key={h.date} className="flex-1 flex flex-col items-center gap-1 group relative">
                      <div className="absolute -top-6 text-xs text-slate-400 opacity-0 group-hover:opacity-100 whitespace-nowrap">
                        ${h.cost_usd.toFixed(3)}
                      </div>
                      <div className="w-full bg-indigo-500/70 rounded-sm" style={{ height: `${Math.max(heightPct, 2)}%` }} />
                      <span className="text-slate-600 text-xs">{h.date.slice(5)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {msg && <p className="text-xs text-center text-emerald-400">{msg}</p>}
        </div>
      </div>
    </div>
  );
}
