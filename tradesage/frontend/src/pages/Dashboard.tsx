import React, { useEffect, useState, useCallback } from 'react'
import axios from 'axios'
import {
  TrendingUp, TrendingDown, DollarSign, Activity,
  Plus, Loader2, RefreshCw, Zap, Briefcase, Clock,
  BookOpen, AlertTriangle, XCircle, CheckCircle2
} from 'lucide-react'
import { WSEvent } from '../hooks/useWebSocket'
import ModeToggle from '../components/ModeToggle'
import TradeDetailCard from '../components/TradeDetailCard'
import ReviewPanel from '../components/ReviewPanel'
import MentorFeed from '../components/MentorFeed'
import SignalQueue from '../components/SignalQueue'
import CostMonitor, { CostMonitorBadge } from '../components/CostMonitor'

interface DashboardProps {
  wsEvent: WSEvent | null
}

type RightTab = 'positions' | 'trades' | 'intel'

export default function Dashboard({ wsEvent }: DashboardProps) {
  const [portfolio, setPortfolio] = useState<Record<string, unknown>>({})
  const [trades, setTrades] = useState<unknown[]>([])
  const [lessons, setLessons] = useState<unknown[]>([])
  const [reviews, setReviews] = useState<unknown[]>([])
  const [winRate, setWinRate] = useState<Record<string, unknown>>({})
  const [prices, setPrices] = useState<Record<string, unknown>>({})
  const [alpacaOrders, setAlpacaOrders] = useState<Record<string, unknown>[]>([])
  const [mode, setMode] = useState('paper')
  const [loading, setLoading] = useState(true)
  const [newTicker, setNewTicker] = useState('')
  const [watchList, setWatchList] = useState<string[]>([])
  const [cancellingOrders, setCancellingOrders] = useState(false)
  const [showCostMonitor, setShowCostMonitor] = useState(false)
  const [rightTab, setRightTab] = useState<RightTab>('positions')
  const [marketStatus, setMarketStatus] = useState<{ is_open: boolean; next_open: string | null; next_close: string | null } | null>(null)

  const loadAll = useCallback(async () => {
    try {
      const [portResp, tradesResp, lessonsResp, reviewsResp, wrResp, pricesResp, ordersResp, mktResp] = await Promise.all([
        axios.get('/api/portfolio'),
        axios.get('/api/trades?limit=20'),
        axios.get('/api/lessons?limit=8'),
        axios.get('/api/reviews?limit=5'),
        axios.get('/api/win-rate'),
        axios.get('/api/prices'),
        axios.get('/api/alpaca/orders?limit=15'),
        axios.get('/api/market-status'),
      ])
      setPortfolio(portResp.data)
      setTrades(tradesResp.data.trades || [])
      setLessons(lessonsResp.data.lessons || [])
      setReviews(reviewsResp.data.reviews || [])
      setWinRate(wrResp.data)
      setPrices(pricesResp.data.prices || {})
      setAlpacaOrders(ordersResp.data.orders || [])
      setWatchList((portResp.data as Record<string, unknown>).watch_list as string[] || [])
      setMode(((portResp.data as Record<string, unknown>).mode as string) || 'paper')
      setMarketStatus(mktResp.data)
    } catch (e) {
      console.error('Dashboard load failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAll()
    const interval = setInterval(loadAll, 30_000)
    return () => clearInterval(interval)
  }, [loadAll])

  useEffect(() => {
    if (!wsEvent) return
    if (wsEvent.type === 'review_note') {
      setReviews(prev => [wsEvent.data as unknown, ...prev].slice(0, 10))
      setRightTab('intel')
    }
    if (wsEvent.type === 'lesson') {
      setLessons(prev => [wsEvent.data as unknown, ...prev].slice(0, 10))
    }
    if (wsEvent.type === 'trade_fill') {
      loadAll()
      setRightTab('positions')
    }
    if (wsEvent.type === 'mode_changed') {
      setMode((wsEvent.data as Record<string, string>).mode)
    }
  }, [wsEvent, loadAll])

  const handleCancelAllOrders = async () => {
    if (!confirm('Cancel ALL pending Alpaca orders?')) return
    setCancellingOrders(true)
    try {
      await axios.delete('/api/alpaca/orders/cancel-all')
      setTimeout(loadAll, 2000)
    } catch (e) {
      console.error('Cancel orders failed:', e)
    } finally {
      setCancellingOrders(false)
    }
  }

  const handleAddTicker = async () => {
    if (!newTicker.trim()) return
    try {
      const resp = await axios.post('/api/add-ticker', { ticker: newTicker.trim() })
      setWatchList((resp.data as Record<string, string[]>).watch_list || [])
      setNewTicker('')
    } catch (e) {
      console.error('Add ticker failed:', e)
    }
  }

  const alpacaData = (portfolio as Record<string, Record<string, unknown>>).alpaca || {}
  const dbSummary = (portfolio as Record<string, Record<string, unknown>>).db_summary || {}

  const equity = (alpacaData.equity as number) || (dbSummary.portfolio_value as number) || 100000
  const cash = (alpacaData.cash as number) || 0
  const buyingPower = (alpacaData.buying_power as number) || 0
  const dayPnl = (alpacaData.day_pnl as number) || 0
  const alpacaPositions = (alpacaData.positions as Record<string, unknown>[]) || []
  const realisedPnl = (dbSummary.realised_pnl as number) || 0
  const currentWR = ((winRate as Record<string, Record<string, number>>).current?.win_rate || 0)
  const totalTrades = ((winRate as Record<string, Record<string, number>>).current?.total_trades || 0)
  const pendingOrders = alpacaOrders.filter(o => ['accepted', 'pending_new', 'new'].includes(o.status as string))

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 text-brand animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* ── Top Bar ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-4 px-5 py-3 border-b border-surface-3 bg-surface-1 shrink-0">
        {/* Stats — spread across full bar */}
        <div className="flex-1 grid grid-cols-5 gap-2">
          <StatPill
            label="Equity"
            value={`$${equity.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
          />
          <StatPill
            label="Buying Power"
            value={`$${buyingPower.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
          />
          <StatPill
            label="Today P&L"
            value={`${dayPnl >= 0 ? '+' : ''}$${Math.abs(dayPnl).toFixed(2)}`}
            color={dayPnl >= 0 ? 'green' : 'red'}
          />
          <StatPill
            label="Win Rate"
            value={`${(currentWR * 100).toFixed(0)}%`}
            sub={`${totalTrades} trades`}
            color={currentWR >= 0.5 ? 'green' : totalTrades === 0 ? undefined : 'red'}
          />
          <StatPill
            label="Realised P&L"
            value={`${realisedPnl >= 0 ? '+' : ''}$${realisedPnl.toFixed(2)}`}
            color={realisedPnl >= 0 ? 'green' : 'red'}
          />
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 shrink-0">
          <MarketStatusBadge status={marketStatus} />
          <CostMonitorBadge onClick={() => setShowCostMonitor(true)} />
          <button className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-surface-3 transition-colors" onClick={loadAll} title="Refresh">
            <RefreshCw className="w-4 h-4" />
          </button>
          <span className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-bold border ${
            mode === 'paper'
              ? 'bg-brand/10 border-brand/30 text-brand-glow'
              : 'bg-accent-red/10 border-accent-red/40 text-accent-red'
          }`}>
            <Zap className="w-3 h-3" />
            {mode.toUpperCase()}
          </span>
        </div>
      </div>

      {showCostMonitor && <CostMonitor onClose={() => setShowCostMonitor(false)} />}

      {/* ── Main Layout ─────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* LEFT — Signal Queue (primary action area) */}
        <div className="flex-1 overflow-y-auto p-4">
          <SignalQueue wsEvent={wsEvent} />
        </div>

        {/* RIGHT — Tabbed panel */}
        <div className="w-80 xl:w-96 border-l border-surface-3 flex flex-col shrink-0 overflow-hidden bg-surface-1">

          {/* Tab headers */}
          <div className="flex border-b border-surface-3 shrink-0">
            <TabBtn active={rightTab === 'positions'} onClick={() => setRightTab('positions')} icon={<Briefcase className="w-3.5 h-3.5" />} label="Positions" badge={alpacaPositions.length || undefined} />
            <TabBtn active={rightTab === 'trades'} onClick={() => setRightTab('trades')} icon={<Clock className="w-3.5 h-3.5" />} label="Trades" badge={totalTrades || undefined} />
            <TabBtn active={rightTab === 'intel'} onClick={() => setRightTab('intel')} icon={<BookOpen className="w-3.5 h-3.5" />} label="Intel" badge={reviews.length > 0 ? reviews.length : undefined} />
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto">
            {rightTab === 'positions' && (
              <PositionsTab
                positions={alpacaPositions}
                orders={alpacaOrders}
                pendingOrders={pendingOrders}
                cancellingOrders={cancellingOrders}
                onCancelAll={handleCancelAllOrders}
                watchList={watchList}
                prices={prices}
                newTicker={newTicker}
                onNewTickerChange={setNewTicker}
                onAddTicker={handleAddTicker}
                mode={mode}
                onModeChange={setMode}
                cash={cash}
              />
            )}
            {rightTab === 'trades' && (
              <TradesTab trades={trades} winRate={winRate} realisedPnl={realisedPnl} />
            )}
            {rightTab === 'intel' && (
              <IntelTab reviews={reviews} lessons={lessons} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Top bar stat pill ────────────────────────────────────────────

function StatPill({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: 'green' | 'red'
}) {
  const valueColor = color === 'green' ? 'text-accent-green' : color === 'red' ? 'text-accent-red' : 'text-white'
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-base font-bold mono ${valueColor}`}>{value}</span>
      {sub && <span className="text-xs text-gray-600">{sub}</span>}
    </div>
  )
}

// ── Market status badge ───────────────────────────────────────────

function MarketStatusBadge({ status }: {
  status: { is_open: boolean; next_open: string | null; next_close: string | null } | null
}) {
  // Fallback: derive from current time (NYSE hours Mon–Fri 9:30–16:00 ET)
  const isOpenFallback = (() => {
    const now = new Date()
    const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
    const day = et.getDay()
    const h = et.getHours()
    const m = et.getMinutes()
    const mins = h * 60 + m
    return day >= 1 && day <= 5 && mins >= 570 && mins < 960 // 9:30–16:00
  })()

  const isOpen = status ? status.is_open : isOpenFallback

  const formatTime = (iso: string | null) => {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
  }

  const sub = status
    ? (isOpen
        ? (status.next_close ? `Closes ${formatTime(status.next_close)}` : '')
        : (status.next_open ? `Opens ${formatTime(status.next_open)}` : ''))
    : ''

  return (
    <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-semibold ${
      isOpen
        ? 'bg-accent-green/10 border-accent-green/30 text-accent-green'
        : 'bg-surface-3 border-surface-3 text-gray-500'
    }`} title={sub || undefined}>
      <div className={`w-1.5 h-1.5 rounded-full ${isOpen ? 'bg-accent-green live-dot' : 'bg-gray-600'}`} />
      {isOpen ? 'Market Open' : 'Market Closed'}
      {sub && <span className="hidden xl:inline text-[10px] font-normal opacity-70 ml-0.5">· {sub}</span>}
    </div>
  )
}

// ── Tab button ───────────────────────────────────────────────────

function TabBtn({ active, onClick, icon, label, badge }: {
  active: boolean; onClick: () => void; icon: React.ReactNode; label: string; badge?: number
}) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition-colors relative ${
        active
          ? 'text-white border-b-2 border-brand'
          : 'text-gray-500 hover:text-gray-300'
      }`}
    >
      {icon}{label}
      {badge !== undefined && badge > 0 && (
        <span className="absolute top-1.5 right-2 w-4 h-4 rounded-full bg-brand text-white text-[9px] flex items-center justify-center font-bold">
          {badge > 9 ? '9+' : badge}
        </span>
      )}
    </button>
  )
}

// ── Positions Tab ────────────────────────────────────────────────

function PositionsTab({ positions, orders, pendingOrders, cancellingOrders, onCancelAll,
  watchList, prices, newTicker, onNewTickerChange, onAddTicker, mode, onModeChange, cash }: {
  positions: Record<string, unknown>[]
  orders: Record<string, unknown>[]
  pendingOrders: Record<string, unknown>[]
  cancellingOrders: boolean
  onCancelAll: () => void
  watchList: string[]
  prices: Record<string, unknown>
  newTicker: string
  onNewTickerChange: (v: string) => void
  onAddTicker: () => void
  mode: string
  onModeChange: (m: string) => void
  cash: number
}) {
  return (
    <div className="p-4 space-y-5">
      {/* Mode toggle */}
      <ModeToggle currentMode={mode} onModeChange={onModeChange} />

      {/* Live positions */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Live Positions</h4>
          <span className="text-xs text-gray-600 mono">Cash ${cash.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
        </div>
        {positions.length === 0 ? (
          <p className="text-xs text-gray-600 py-3 text-center">No open positions</p>
        ) : (
          <div className="space-y-2">
            {positions.map(pos => {
              const p = pos as Record<string, unknown>
              const upnl = p.unrealized_pnl as number
              const pct = p.unrealized_pnl_pct as number
              const isShort = (p.qty as number) < 0
              return (
                <div key={p.ticker as string} className="bg-surface-2 rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="font-bold mono text-white text-sm">{p.ticker as string}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${isShort ? 'bg-accent-red/20 text-accent-red' : 'bg-accent-green/20 text-accent-green'}`}>
                        {isShort ? 'SHORT' : 'LONG'} ×{Math.abs(p.qty as number).toFixed(0)}
                      </span>
                    </div>
                    <span className={`text-xs font-bold mono ${upnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {upnl >= 0 ? '+' : ''}${upnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs text-gray-500">
                    <span>Mkt ${(p.market_value as number).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    <span className={upnl >= 0 ? 'text-accent-green/70' : 'text-accent-red/70'}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</span>
                  </div>
                  {/* P&L bar */}
                  <div className="mt-1.5 h-1 rounded-full bg-surface-3 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${upnl >= 0 ? 'bg-accent-green/60' : 'bg-accent-red/60'}`}
                      style={{ width: `${Math.min(100, Math.abs(pct) * 10)}%` }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Pending orders */}
      {pendingOrders.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide flex items-center gap-1.5">
              <AlertTriangle className="w-3 h-3 text-accent-yellow" />
              Pending Orders ({pendingOrders.length})
            </h4>
            <button
              onClick={onCancelAll}
              disabled={cancellingOrders}
              className="text-[10px] px-2 py-0.5 rounded bg-accent-red/15 text-accent-red border border-accent-red/30 hover:bg-accent-red/25 transition-colors disabled:opacity-40"
            >
              {cancellingOrders ? 'Cancelling…' : 'Cancel All'}
            </button>
          </div>
          <div className="space-y-1">
            {orders.slice(0, 6).map(o => {
              const side = o.side as string
              const status = o.status as string
              const filled = status === 'filled'
              return (
                <div key={o.id as string} className="flex items-center justify-between text-xs py-1 border-b border-surface-3 last:border-0">
                  <div className="flex items-center gap-2">
                    <span className={`font-bold ${side === 'buy' ? 'text-accent-green' : 'text-accent-red'}`}>{side.toUpperCase()}</span>
                    <span className="mono text-gray-300">{o.ticker as string}</span>
                    <span className="text-gray-600">×{(o.filled_qty as number || o.qty as number || 0).toFixed(0)}</span>
                  </div>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    filled ? 'bg-accent-green/15 text-accent-green' :
                    status === 'canceled' ? 'bg-surface-3 text-gray-500' :
                    'bg-accent-yellow/15 text-accent-yellow'
                  }`}>{status}</span>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Watch list */}
      <section>
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Watch List</h4>
        <div className="flex gap-1.5 mb-2">
          <input
            className="flex-1 bg-surface-2 border border-surface-3 rounded-lg px-2.5 py-1.5 text-white mono text-xs focus:outline-none focus:border-brand uppercase placeholder-gray-600"
            value={newTicker}
            onChange={e => onNewTickerChange(e.target.value.toUpperCase())}
            placeholder="Add ticker…"
            onKeyDown={e => e.key === 'Enter' && onAddTicker()}
          />
          <button onClick={onAddTicker} className="px-2.5 py-1.5 rounded-lg bg-brand hover:bg-brand-dim text-white transition-colors">
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-1">
          {watchList.map(ticker => {
            const priceData = (prices as Record<string, Record<string, number>>)[ticker]
            const p = priceData?.last || 0
            return (
              <div key={ticker} className="flex items-center justify-between text-xs py-0.5">
                <span className="mono font-semibold text-gray-300">{ticker}</span>
                <span className="mono text-gray-500">{p > 0 ? `$${p.toFixed(2)}` : '—'}</span>
              </div>
            )
          })}
        </div>
      </section>
    </div>
  )
}

// ── Trades Tab ───────────────────────────────────────────────────

function TradesTab({ trades, winRate, realisedPnl }: {
  trades: unknown[]
  winRate: Record<string, unknown>
  realisedPnl: number
}) {
  const wr = (winRate as Record<string, Record<string, number>>).current
  return (
    <div className="p-4 space-y-4">
      {/* Stats row */}
      {(wr?.total_trades || 0) > 0 && (
        <div className="grid grid-cols-3 gap-2">
          <MiniStat label="Win Rate" value={`${(wr.win_rate * 100).toFixed(0)}%`} color={wr.win_rate >= 0.5 ? 'green' : 'red'} />
          <MiniStat label="Total" value={`${wr.total_trades}`} />
          <MiniStat label="P&L" value={`${realisedPnl >= 0 ? '+' : ''}$${realisedPnl.toFixed(0)}`} color={realisedPnl >= 0 ? 'green' : 'red'} />
        </div>
      )}

      {trades.length === 0 ? (
        <div className="text-center py-8">
          <Activity className="w-8 h-8 text-gray-700 mx-auto mb-2" />
          <p className="text-xs text-gray-600">No trades yet.</p>
          <p className="text-xs text-gray-700 mt-1">Scan tickers and approve a signal to start.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {(trades as Array<Record<string, unknown>>).map(trade => (
            <CompactTradeRow key={trade.trade_id as string} trade={trade} />
          ))}
        </div>
      )}
    </div>
  )
}

function MiniStat({ label, value, color }: { label: string; value: string; color?: 'green' | 'red' }) {
  return (
    <div className="bg-surface-2 rounded-lg p-2 text-center">
      <div className="text-[10px] text-gray-600 mb-0.5">{label}</div>
      <div className={`text-sm font-bold mono ${color === 'green' ? 'text-accent-green' : color === 'red' ? 'text-accent-red' : 'text-white'}`}>{value}</div>
    </div>
  )
}

function CompactTradeRow({ trade }: { trade: Record<string, unknown> }) {
  const outcome = trade.outcome as string
  const pnl = trade.pnl_dollars as number
  const pnlPct = trade.pnl_pct as number
  const isOpen = outcome === 'OPEN'

  return (
    <div className="bg-surface-2 rounded-lg p-2.5 flex items-center gap-3">
      {/* Outcome icon */}
      <div className="shrink-0">
        {isOpen ? (
          <div className="w-6 h-6 rounded-full bg-brand/20 flex items-center justify-center">
            <Activity className="w-3 h-3 text-brand-glow" />
          </div>
        ) : outcome === 'WIN' ? (
          <div className="w-6 h-6 rounded-full bg-accent-green/20 flex items-center justify-center">
            <CheckCircle2 className="w-3 h-3 text-accent-green" />
          </div>
        ) : (
          <div className="w-6 h-6 rounded-full bg-accent-red/20 flex items-center justify-center">
            <XCircle className="w-3 h-3 text-accent-red" />
          </div>
        )}
      </div>
      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-bold mono text-white text-xs">{trade.ticker as string}</span>
          <span className={`text-[10px] font-bold ${(trade.side as string) === 'buy' ? 'text-accent-green' : 'text-accent-red'}`}>
            {(trade.side as string)?.toUpperCase()}
          </span>
        </div>
        <div className="text-[10px] text-gray-600 mono">
          ${(trade.entry_price as number || 0).toFixed(2)}
          {!isOpen && trade.exit_price ? ` → $${(trade.exit_price as number).toFixed(2)}` : ''}
        </div>
      </div>
      {/* P&L */}
      {!isOpen && pnl !== null && pnl !== undefined ? (
        <div className="text-right shrink-0">
          <div className={`text-xs font-bold mono ${pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
            {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
          </div>
          <div className={`text-[10px] ${pnl >= 0 ? 'text-accent-green/60' : 'text-accent-red/60'}`}>
            {(pnlPct || 0) >= 0 ? '+' : ''}{(pnlPct || 0).toFixed(1)}%
          </div>
        </div>
      ) : (
        <span className="text-[10px] text-brand-glow bg-brand/10 px-1.5 py-0.5 rounded font-bold">OPEN</span>
      )}
    </div>
  )
}

// ── Intel Tab ────────────────────────────────────────────────────

function IntelTab({ reviews, lessons }: {
  reviews: unknown[]
  lessons: unknown[]
}) {
  return (
    <div className="p-4 space-y-5">
      {reviews.length > 0 && (
        <section>
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Latest Mentor Review</h4>
          <ReviewPanel reviews={reviews as Parameters<typeof ReviewPanel>[0]['reviews']} latestOnly />
        </section>
      )}
      <section>
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Recent Lessons</h4>
        {lessons.length === 0 ? (
          <p className="text-xs text-gray-600 text-center py-4">Lessons appear after trades close.</p>
        ) : (
          <MentorFeed lessons={lessons as Parameters<typeof MentorFeed>[0]['lessons']} />
        )}
      </section>
    </div>
  )
}
