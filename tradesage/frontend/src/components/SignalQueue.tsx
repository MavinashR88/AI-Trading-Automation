import React, { useState, useEffect, useRef } from 'react';

interface RiskPreview {
  position_size_usd: number;
  qty: number;
  stop_loss: number;
  take_profit: number;
  risk_ok: boolean;
  risk_note: string;
  atr?: number;
}

interface Factors {
  news_sentiment: number;
  price_momentum: number;
  history: number;
  urgency: number;
  volume: number;
}

interface TradeResult {
  blocked: boolean;
  reason?: string;
  outcome?: string;
  entry_price?: number;
  exit_price?: number;
  pnl_dollars?: number;
  pnl_pct?: number;
  quantity?: number;
  stop_loss?: number;
  take_profit?: number;
  lesson?: string;
}

interface PendingSignal {
  signal_id: string;
  ticker: string;
  action: 'buy' | 'sell';
  price: number;
  sentiment_score: number;
  urgency: string;
  catalyst: string;
  headline: string;
  ai_reasoning: string;
  score: number;
  grade: string;
  breaking: boolean;
  risk: RiskPreview;
  created_at: string;
  status: 'pending' | 'approved' | 'rejected';
  approved_action?: 'buy' | 'sell';
  result?: TradeResult;
  factors?: Factors;
  atr?: number;
  vol_ratio?: number;
  price_momentum_pct?: number;
  // live P&L injected from /api/open-trades
  live_pnl?: number;
  live_pnl_pct?: number;
  current_price?: number;
}

const API = 'http://localhost:8000';

const gradeColor = (g: string) => {
  if (g === 'A+') return 'text-purple-300 bg-purple-900/50 border-purple-600';
  if (g === 'A')  return 'text-emerald-400 bg-emerald-900/40 border-emerald-700';
  if (g === 'B')  return 'text-blue-400 bg-blue-900/40 border-blue-700';
  if (g === 'C')  return 'text-yellow-400 bg-yellow-900/40 border-yellow-700';
  if (g === 'D')  return 'text-orange-400 bg-orange-900/40 border-orange-700';
  return 'text-red-400 bg-red-900/40 border-red-700';
};

const urgencyBadge = (u: string) => {
  if (u === 'immediate') return <span className="text-xs px-2 py-0.5 rounded-full bg-red-600 text-white font-bold animate-pulse">🔴 NOW</span>;
  if (u === 'wait') return <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-600 text-white font-bold">🟡 WATCH</span>;
  return <span className="text-xs px-2 py-0.5 rounded-full bg-slate-600 text-slate-300">⚪ CALM</span>;
};

function FactorBar({ label, value, color }: { label: string; value: number; color: string }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="text-slate-500 text-xs w-28 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400 w-8 text-right">{pct}%</span>
    </div>
  );
}

function LivePnlBadge({ pnl, pct }: { pnl: number; pct: number }) {
  const pos = pnl >= 0;
  return (
    <div className={`text-xs px-2 py-1 rounded-lg font-mono font-bold ${pos ? 'bg-emerald-900/50 text-emerald-300 border border-emerald-700' : 'bg-red-900/50 text-red-300 border border-red-700'}`}>
      {pos ? '+' : ''}${Math.abs(pnl).toFixed(2)} ({pos ? '+' : ''}{pct.toFixed(2)}%)
    </div>
  );
}

// Auto-approve thresholds (score is 0–1)
const AUTO_APPROVE_IMMEDIATE = 0.90;
const AUTO_APPROVE_COUNTDOWN = 0.80;
const COUNTDOWN_SECONDS = 60;

export default function SignalQueue({ wsEvent }: { wsEvent?: { type: string; data: unknown } | null }) {
  const [signals, setSignals] = useState<PendingSignal[]>([]);
  const [scanning, setScanning] = useState(false);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [marketOpen, setMarketOpen] = useState<boolean | null>(null);
  const [skippedTickers, setSkippedTickers] = useState<string[]>([]);
  const [tradesCollapsed, setTradesCollapsed] = useState(false);
  const [countdowns, setCountdowns] = useState<Record<string, number>>({}); // signal_id → seconds remaining
  const cancelledRef = useRef<Set<string>>(new Set());
  const approvedRef = useRef<Set<string>>(new Set());
  const livePnlTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Restore signals on mount
  useEffect(() => {
    fetch(`${API}/api/signals`)
      .then(r => r.json())
      .then(d => setSignals(d.signals || []))
      .catch(() => {});
  }, []);

  // Poll live P&L for open positions every 5s
  useEffect(() => {
    const fetchLivePnl = async () => {
      try {
        const r = await fetch(`${API}/api/open-trades`);
        const d = await r.json();
        const openTrades: Record<string, { live_pnl: number; live_pnl_pct: number; current_price: number }> = {};
        for (const t of (d.trades || [])) {
          openTrades[t.ticker] = {
            live_pnl: t.live_pnl,
            live_pnl_pct: t.live_pnl_pct,
            current_price: t.current_price,
          };
        }
        if (Object.keys(openTrades).length === 0) return;
        setSignals(prev => prev.map(s => {
          const lp = openTrades[s.ticker];
          if (lp && s.status === 'approved' && (!s.result || s.result.outcome === 'OPEN')) {
            return { ...s, live_pnl: lp.live_pnl, live_pnl_pct: lp.live_pnl_pct, current_price: lp.current_price };
          }
          return s;
        }));
      } catch {}
    };

    livePnlTimer.current = setInterval(fetchLivePnl, 5000);
    return () => { if (livePnlTimer.current) clearInterval(livePnlTimer.current); };
  }, []);

  // WebSocket events
  useEffect(() => {
    if (!wsEvent) return;

    if (wsEvent.type === 'pipeline_complete') {
      const d = wsEvent.data as Record<string, unknown>;
      setSignals(prev => prev.map(s => {
        const matches = (d.signal_id as string | undefined)
          ? s.signal_id === d.signal_id
          : s.ticker === (d.ticker as string) && s.status === 'approved' && !s.result;
        if (!matches) return s;
        return { ...s, result: { ...(s.result || {}), blocked: d.blocked as boolean, reason: d.reason as string, outcome: d.outcome as string } };
      }));
    }

    if (wsEvent.type === 'trade_fill') {
      const d = wsEvent.data as Record<string, unknown>;
      setSignals(prev => prev.map(s => {
        if (s.ticker !== (d.ticker as string) || s.status !== 'approved') return s;
        return {
          ...s,
          result: {
            ...(s.result || { blocked: false }),
            entry_price: d.entry_price as number,
            exit_price: d.exit_price as number,
            pnl_dollars: d.pnl_dollars as number,
            pnl_pct: d.pnl_pct as number,
            quantity: d.quantity as number,
            stop_loss: d.stop_loss as number,
            take_profit: d.take_profit as number,
            outcome: (d.outcome as string) || s.result?.outcome,
          },
        };
      }));
    }

    if (wsEvent.type === 'trade_closed') {
      const d = wsEvent.data as Record<string, unknown>;
      setSignals(prev => prev.map(s => {
        if (s.ticker !== (d.ticker as string) || s.status !== 'approved') return s;
        return {
          ...s,
          live_pnl: undefined,
          live_pnl_pct: undefined,
          result: {
            ...(s.result || { blocked: false }),
            outcome: d.outcome as string,
            exit_price: d.exit_price as number,
            pnl_dollars: d.pnl_dollars as number,
            pnl_pct: d.pnl_pct as number,
            entry_price: (d.entry_price as number) || s.result?.entry_price,
            quantity: (d.quantity as number) || s.result?.quantity,
          },
        };
      }));
    }

    if (wsEvent.type === 'lesson') {
      const d = wsEvent.data as Record<string, unknown>;
      const what = (d.what_happened as string) || (d.correction as string) || '';
      if (what) {
        setSignals(prev => prev.map(s => {
          if (s.ticker !== (d.ticker as string) || s.status !== 'approved') return s;
          return { ...s, result: { ...(s.result || { blocked: false }), lesson: what.slice(0, 140) } };
        }));
      }
    }

    if (wsEvent.type === 'signals_ready') {
      const d = wsEvent.data as Record<string, unknown>;
      const newSigs = d.signals as PendingSignal[];
      if (newSigs?.length) {
        setSignals(newSigs);
        startCountdown(newSigs);
      }
    }
  }, [wsEvent]);

  // Auto-approve countdown ticker
  useEffect(() => {
    const interval = setInterval(() => {
      setCountdowns(prev => {
        const next = { ...prev };
        let changed = false;
        for (const [id, secs] of Object.entries(next)) {
          if (secs <= 1) {
            delete next[id];
            changed = true;
            // Fire auto-approve if not cancelled and not already approved
            if (!cancelledRef.current.has(id) && !approvedRef.current.has(id)) {
              approvedRef.current.add(id);
              setSignals(prev2 => {
                const sig = prev2.find(s => s.signal_id === id && s.status === 'pending');
                if (sig) {
                  // fire approve in background
                  fetch(`${API}/api/signals/${id}/approve`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: sig.action }),
                  }).catch(() => {});
                  return prev2.map(s => s.signal_id === id ? { ...s, status: 'approved' as const, approved_action: sig.action } : s);
                }
                return prev2;
              });
            }
          } else {
            next[id] = secs - 1;
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const startCountdown = (sigs: PendingSignal[]) => {
    const newCountdowns: Record<string, number> = {};
    for (const sig of sigs) {
      if (sig.status !== 'pending') continue;
      if (cancelledRef.current.has(sig.signal_id) || approvedRef.current.has(sig.signal_id)) continue;
      if (sig.score >= AUTO_APPROVE_IMMEDIATE) {
        // Immediate auto-approve
        approvedRef.current.add(sig.signal_id);
        setTimeout(() => {
          fetch(`${API}/api/signals/${sig.signal_id}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: sig.action }),
          }).catch(() => {});
          setSignals(prev => prev.map(s => s.signal_id === sig.signal_id ? { ...s, status: 'approved' as const, approved_action: sig.action } : s));
        }, 500);
      } else if (sig.score >= AUTO_APPROVE_COUNTDOWN) {
        newCountdowns[sig.signal_id] = COUNTDOWN_SECONDS;
      }
    }
    if (Object.keys(newCountdowns).length > 0) {
      setCountdowns(prev => ({ ...prev, ...newCountdowns }));
    }
  };

  const cancelAutoApprove = (signalId: string) => {
    cancelledRef.current.add(signalId);
    setCountdowns(prev => {
      const next = { ...prev };
      delete next[signalId];
      return next;
    });
  };

  const scan = async () => {
    setScanning(true);
    try {
      const r = await fetch(`${API}/api/scan`, { method: 'POST' });
      const d = await r.json();
      const sigs: PendingSignal[] = d.signals || [];
      setSignals(sigs);
      setMarketOpen(d.market_open ?? null);
      setSkippedTickers(d.skipped_tickers || []);
      startCountdown(sigs);
    } catch (e) {
      console.error(e);
    } finally {
      setScanning(false);
    }
  };

  const approve = async (sig: PendingSignal) => {
    setLoadingId(sig.signal_id);
    setSignals(prev => prev.map(s =>
      s.signal_id === sig.signal_id ? { ...s, status: 'approved', approved_action: sig.action } : s
    ));
    try {
      await fetch(`${API}/api/signals/${sig.signal_id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: sig.action }),
      });
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingId(null);
    }
  };

  const reject = async (sig: PendingSignal) => {
    setSignals(prev => prev.map(s => s.signal_id === sig.signal_id ? { ...s, status: 'rejected' } : s));
    try {
      await fetch(`${API}/api/signals/${sig.signal_id}/reject`, { method: 'POST' });
    } catch {}
  };

  const pending   = signals.filter(s => s.status === 'pending');
  const executing = signals.filter(s => s.status === 'approved' && !s.result);
  const done      = signals.filter(s => s.status === 'approved' && s.result && !s.result.blocked);
  const blocked   = signals.filter(s => s.status === 'approved' && s.result?.blocked);
  const skipped   = signals.filter(s => s.status === 'rejected');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-xl font-bold text-white">AI Signal Queue</h2>
          <p className="text-sm text-slate-400">
            {pending.length > 0
              ? `${pending.length} trade idea${pending.length > 1 ? 's' : ''} — AI scored and ranked`
              : 'Scan to find the best opportunities across 20 tickers'}
          </p>
        </div>
        <button onClick={scan} disabled={scanning}
          className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold transition-all shadow-lg shrink-0">
          {scanning
            ? <><svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/></svg>Scanning…</>
            : '🔍 Scan All Tickers'}
        </button>
      </div>

      {/* Market status */}
      {marketOpen !== null && (
        <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg ${marketOpen ? 'bg-emerald-900/40 text-emerald-400 border border-emerald-700' : 'bg-yellow-900/40 text-yellow-400 border border-yellow-700'}`}>
          <span>{marketOpen ? '🟢 Market OPEN — orders fill immediately' : '🟡 Market CLOSED — paper fill simulated'}</span>
          {skippedTickers.length > 0 && <span className="ml-auto text-slate-400">Skipped (open position): {skippedTickers.join(', ')}</span>}
        </div>
      )}

      {/* Empty state */}
      {signals.length === 0 && !scanning && (
        <div className="text-center py-10 border border-dashed border-slate-700 rounded-xl text-slate-500">
          <div className="text-4xl mb-2">📡</div>
          <p className="font-medium text-slate-400">No signals yet</p>
          <p className="text-sm mt-1">Click <strong>Scan All Tickers</strong> — AI scores 5 factors per ticker in ~30 sec.</p>
        </div>
      )}

      {/* ── PENDING ── */}
      {pending.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Waiting for your approval</p>
          {pending.map(sig => {
            const isBuy = sig.action === 'buy';
            const expanded = expandedId === sig.signal_id;
            const busy = loadingId === sig.signal_id;
            return (
              <div key={sig.signal_id} className={`rounded-xl border overflow-hidden transition-all duration-300 ${
                countdowns[sig.signal_id] !== undefined
                  ? isBuy ? 'border-emerald-400 shadow-lg shadow-emerald-900/40 bg-gradient-to-r from-emerald-950/50 to-slate-900'
                           : 'border-red-400 shadow-lg shadow-red-900/40 bg-gradient-to-r from-red-950/50 to-slate-900'
                  : isBuy ? 'border-emerald-700/60 bg-gradient-to-r from-emerald-950/40 to-slate-900'
                           : 'border-red-700/60 bg-gradient-to-r from-red-950/40 to-slate-900'
              }`}>
                <div className="flex items-center gap-3 p-4">
                  {/* Direction */}
                  <div className={`text-xs font-bold px-2.5 py-1.5 rounded-lg shrink-0 ${isBuy ? 'bg-emerald-600 text-white' : 'bg-red-600 text-white'}`}>
                    {isBuy ? '▲ BUY' : '▼ SELL'}
                  </div>

                  {/* Ticker info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-white text-lg leading-none">{sig.ticker}</span>
                      <span className="text-slate-300 font-mono text-sm">${sig.price.toFixed(2)}</span>
                      {urgencyBadge(sig.urgency)}
                      {sig.breaking && <span className="text-xs px-2 py-0.5 rounded-full bg-red-700 text-white font-bold">⚡ BREAKING</span>}
                      {sig.price_momentum_pct !== undefined && Math.abs(sig.price_momentum_pct) > 0.2 && (
                        <span className={`text-xs font-mono font-bold ${sig.price_momentum_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {sig.price_momentum_pct > 0 ? '+' : ''}{sig.price_momentum_pct.toFixed(1)}% today
                        </span>
                      )}
                    </div>
                    <p className="text-slate-400 text-xs mt-0.5 truncate">{sig.headline}</p>
                    {sig.ai_reasoning && (
                      <p className="text-slate-300 text-xs mt-1 italic line-clamp-2">💡 {sig.ai_reasoning}</p>
                    )}
                  </div>

                  {/* Grade */}
                  <div className="text-center hidden sm:block shrink-0">
                    <div className={`text-sm font-bold w-10 h-10 rounded-lg flex items-center justify-center border ${gradeColor(sig.grade)}`}>
                      {sig.grade}
                    </div>
                    <div className="text-xs text-slate-500 mt-0.5">{(sig.score * 100).toFixed(0)}%</div>
                  </div>

                  {/* Risk numbers */}
                  {sig.risk?.position_size_usd > 0 && (
                    <div className="text-right hidden md:block shrink-0 text-xs space-y-0.5">
                      <div className="text-white font-semibold">${sig.risk.position_size_usd.toLocaleString()}</div>
                      <div className="text-slate-400">{sig.risk.qty.toFixed(2)} shares</div>
                      <div className="text-emerald-400">TP ${sig.risk.take_profit.toFixed(2)}</div>
                      <div className="text-red-400">SL ${sig.risk.stop_loss.toFixed(2)}</div>
                      {sig.risk.atr && <div className="text-slate-500">ATR ${sig.risk.atr.toFixed(2)}</div>}
                    </div>
                  )}

                  {/* Auto-approve countdown badge */}
                  {countdowns[sig.signal_id] !== undefined && (
                    <div className="flex flex-col items-center shrink-0">
                      <div className={`relative w-10 h-10 rounded-full flex items-center justify-center font-bold text-sm border-2 ${
                        isBuy ? 'border-emerald-500 text-emerald-300 bg-emerald-950/50' : 'border-red-500 text-red-300 bg-red-950/50'
                      }`}>
                        {countdowns[sig.signal_id]}
                      </div>
                      <button
                        onClick={() => cancelAutoApprove(sig.signal_id)}
                        className="text-[10px] text-slate-500 hover:text-slate-300 mt-0.5 transition-colors"
                        title="Cancel auto-approve"
                      >
                        cancel
                      </button>
                    </div>
                  )}

                  {/* Action buttons */}
                  <div className="flex gap-1.5 shrink-0">
                    <button onClick={() => approve(sig)} disabled={busy}
                      className={`px-4 py-2 text-sm font-bold rounded-lg text-white disabled:opacity-40 transition-all ${
                        isBuy ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'
                      }`}>
                      {busy ? '…' : isBuy ? '▲ Execute BUY' : '▼ Execute SELL'}
                    </button>
                    <button onClick={() => reject(sig)} disabled={busy}
                      className="px-3 py-2 text-sm rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 disabled:opacity-40">
                      Skip
                    </button>
                    <button onClick={() => setExpandedId(expanded ? null : sig.signal_id)}
                      className="px-2 py-2 text-slate-400 hover:text-white transition-colors">
                      {expanded ? '▲' : '▼'}
                    </button>
                  </div>
                </div>

                {/* Expanded detail */}
                {expanded && (
                  <div className="border-t border-slate-700/40 p-4 space-y-4 bg-slate-900/40">
                    {/* 5-factor breakdown */}
                    {sig.factors && (
                      <div className="space-y-2">
                        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">AI Score Breakdown</p>
                        <FactorBar label="News Sentiment" value={sig.factors.news_sentiment} color="bg-blue-500" />
                        <FactorBar label="Price Momentum" value={sig.factors.price_momentum} color={sig.factors.price_momentum > 0.5 ? 'bg-emerald-500' : 'bg-yellow-500'} />
                        <FactorBar label="Track Record" value={sig.factors.history} color="bg-purple-500" />
                        <FactorBar label="Urgency" value={sig.factors.urgency} color="bg-red-500" />
                        <FactorBar label="Volume Surge" value={sig.factors.volume} color="bg-orange-500" />
                      </div>
                    )}

                    {/* Risk grid */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
                      <div className="bg-slate-800 rounded-lg p-3">
                        <div className="text-slate-400 text-xs">Invest</div>
                        <div className="text-white font-bold">${(sig.risk?.position_size_usd || 0).toLocaleString()}</div>
                      </div>
                      <div className="bg-slate-800 rounded-lg p-3">
                        <div className="text-slate-400 text-xs">Shares</div>
                        <div className="text-white font-bold">{(sig.risk?.qty || 0).toFixed(4)}</div>
                      </div>
                      <div className="bg-slate-800 rounded-lg p-3">
                        <div className="text-slate-400 text-xs">Stop Loss</div>
                        <div className="text-red-400 font-bold">${(sig.risk?.stop_loss || 0).toFixed(2)}</div>
                        {sig.atr && <div className="text-slate-500 text-xs">1.5× ATR (${sig.atr.toFixed(2)})</div>}
                      </div>
                      <div className="bg-slate-800 rounded-lg p-3">
                        <div className="text-slate-400 text-xs">Take Profit</div>
                        <div className="text-emerald-400 font-bold">${(sig.risk?.take_profit || 0).toFixed(2)}</div>
                        {sig.atr && <div className="text-slate-500 text-xs">2.5× ATR (${sig.atr.toFixed(2)})</div>}
                      </div>
                    </div>

                    <div className="bg-slate-800/60 rounded-lg p-3 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-slate-400 text-xs">Catalyst:</span>
                        <span className="text-white text-xs capitalize font-medium">{sig.catalyst}</span>
                        {sig.vol_ratio && sig.vol_ratio > 1 && (
                          <span className="text-orange-400 text-xs">· Vol {sig.vol_ratio.toFixed(1)}× avg</span>
                        )}
                      </div>
                      <p className="text-slate-400 text-xs leading-relaxed">{sig.headline}</p>
                      {sig.ai_reasoning && (
                        <div className="border-t border-slate-700 pt-2">
                          <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">AI Decision</p>
                          <p className="text-slate-200 text-xs leading-relaxed">{sig.ai_reasoning}</p>
                        </div>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => approve(sig)} disabled={busy}
                        className={`flex-1 py-2.5 font-bold rounded-lg text-white disabled:opacity-40 ${isBuy ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-red-600 hover:bg-red-500'}`}>
                        {isBuy ? '▲' : '▼'} Execute {sig.action.toUpperCase()} @ ${sig.price.toFixed(2)}
                      </button>
                      <button onClick={() => reject(sig)} disabled={busy}
                        className="px-4 py-2.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 disabled:opacity-40">
                        Skip
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── EXECUTING ── */}
      {executing.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Executing…</p>
          {executing.map(sig => (
            <div key={sig.signal_id} className="flex items-center gap-3 px-4 py-3 rounded-xl border border-indigo-600/40 bg-indigo-950/20">
              <svg className="animate-spin h-4 w-4 text-indigo-400 shrink-0" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
              </svg>
              <div className="flex-1">
                <span className="text-indigo-300 font-semibold text-sm">
                  {(sig.approved_action || sig.action).toUpperCase()} {sig.ticker} @ ${sig.price.toFixed(2)}
                </span>
                <span className="text-indigo-400/70 text-xs ml-2">News → Risk → Mentor gate running…</span>
              </div>
              <div className="text-xs text-indigo-300 shrink-0 text-right">
                <div>${(sig.risk?.position_size_usd || 0).toLocaleString()} invested</div>
                <div>{(sig.risk?.qty || 0).toFixed(2)} shares</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── RECENT TRADES ── */}
      {done.length > 0 && (
        <div className="space-y-2">
          <button
            onClick={() => setTradesCollapsed(c => !c)}
            className="flex items-center gap-2 text-xs font-semibold text-slate-500 uppercase tracking-wider hover:text-slate-300 transition-colors w-full text-left"
          >
            <span>Recent Trades ({done.length})</span>
            <span className="ml-auto">{tradesCollapsed ? '▶' : '▼'}</span>
          </button>

          {!tradesCollapsed && done.map(sig => {
            const r = sig.result!;
            const isWin = r.outcome === 'WIN';
            const isLoss = r.outcome === 'LOSS';
            const isOpen = !r.outcome || r.outcome === 'OPEN';
            const action = sig.approved_action || sig.action;
            const invested = sig.risk?.position_size_usd || 0;
            const qty = r.quantity ?? sig.risk?.qty ?? 0;
            const entry = r.entry_price ?? sig.price;
            const exit = r.exit_price;
            const pnlDollars = isOpen ? sig.live_pnl : r.pnl_dollars;
            const pnlPct = isOpen ? sig.live_pnl_pct : r.pnl_pct;
            const currentPx = sig.current_price;
            const riskPct = sig.risk?.stop_loss && sig.price
              ? ((sig.price - sig.risk.stop_loss) / sig.price * 100).toFixed(1)
              : '2.0';

            return (
              <div key={sig.signal_id} className={`rounded-xl border p-4 ${
                isOpen ? 'border-blue-600/30 bg-blue-950/10'
                : isWin ? 'border-emerald-600/40 bg-emerald-950/15'
                : 'border-red-600/30 bg-red-950/10'
              }`}>
                {/* Top row */}
                <div className="flex items-center gap-3 flex-wrap">
                  <span className={`text-base font-black shrink-0 ${
                    isOpen ? 'text-blue-400' : isWin ? 'text-emerald-400' : 'text-red-400'
                  }`}>
                    {isOpen ? '📋 OPEN' : isWin ? '✅ WIN' : '❌ LOSS'}
                  </span>
                  <div>
                    <span className="text-white font-bold text-base">{sig.ticker}</span>
                    <span className={`ml-2 text-xs font-bold px-2 py-0.5 rounded ${
                      action === 'buy' ? 'bg-emerald-800 text-emerald-200' : 'bg-red-800 text-red-200'
                    }`}>{action.toUpperCase()}</span>
                  </div>
                  {isOpen && sig.live_pnl !== undefined && (
                    <div className="ml-2">
                      <LivePnlBadge pnl={sig.live_pnl} pct={sig.live_pnl_pct || 0} />
                    </div>
                  )}
                  {!isOpen && pnlDollars !== undefined && pnlDollars !== null && (
                    <div className="ml-auto text-right shrink-0">
                      <div className={`text-lg font-bold font-mono ${isWin ? 'text-emerald-400' : 'text-red-400'}`}>
                        {pnlDollars >= 0 ? '+' : ''}${Math.abs(pnlDollars).toFixed(2)}
                      </div>
                      {pnlPct !== undefined && pnlPct !== null && (
                        <div className={`text-xs font-mono ${isWin ? 'text-emerald-500' : 'text-red-500'}`}>
                          {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Detail row */}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-slate-400 font-mono">
                  <span>Entry <span className="text-slate-200">${entry.toFixed(2)}</span>
                    {exit ? <> → Exit <span className="text-slate-200">${exit.toFixed(2)}</span></> : null}
                    {isOpen && currentPx ? <> → Now <span className={`${sig.live_pnl && sig.live_pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>${currentPx.toFixed(2)}</span></> : null}
                    {isOpen && !exit && !currentPx ? ' → OPEN' : null}
                  </span>
                  <span>Invest <span className="text-slate-200">${invested.toLocaleString()}</span></span>
                  <span>Risk <span className="text-red-400">{riskPct}%</span></span>
                  {qty > 0 && <span>{qty.toFixed(2)} sh</span>}
                  {sig.risk?.stop_loss && <span>SL <span className="text-red-400">${sig.risk.stop_loss.toFixed(2)}</span></span>}
                  {sig.risk?.take_profit && <span>TP <span className="text-emerald-400">${sig.risk.take_profit.toFixed(2)}</span></span>}
                  {sig.atr && <span>ATR <span className="text-slate-300">${sig.atr.toFixed(2)}</span></span>}
                </div>

                {/* Lesson */}
                {r.lesson && (
                  <div className="mt-2 flex items-start gap-2 text-xs bg-slate-800/40 rounded-lg p-2">
                    <span className="text-amber-400 shrink-0">🎓</span>
                    <span className="text-slate-300 italic">{r.lesson}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── BLOCKED ── */}
      {blocked.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Blocked by Mentor</p>
          {blocked.map(sig => (
            <div key={sig.signal_id} className="flex items-center gap-3 px-4 py-3 rounded-xl border border-orange-700/30 bg-orange-950/10">
              <span className="text-orange-400 shrink-0">🚫</span>
              <div className="flex-1 min-w-0">
                <span className="text-orange-300 font-semibold text-sm">{sig.ticker} {(sig.approved_action || sig.action).toUpperCase()}</span>
                {sig.result?.reason && (
                  <p className="text-orange-400/70 text-xs mt-0.5 line-clamp-2">{sig.result.reason}</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── SKIPPED ── */}
      {skipped.length > 0 && (
        <div className="flex flex-wrap gap-2">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider w-full">Skipped</p>
          {skipped.map(sig => (
            <span key={sig.signal_id} className="text-xs px-3 py-1 rounded-full bg-slate-800 text-slate-500">
              {sig.ticker}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
