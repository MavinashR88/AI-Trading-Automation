import React, { useEffect, useState, useCallback } from 'react'
import axios from 'axios'
import {
  FlaskConical, RefreshCw, Play, Loader2, CheckCircle2, XCircle,
  Clock, Zap, TrendingUp, AlertTriangle, ChevronRight, BarChart2,
  Shield, BookOpen, Cpu, Rocket, Activity
} from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────

interface DiscoveredStock {
  id: string
  ticker: string
  company_name: string
  sector: string
  discovery_reason: string
  discovery_score: number
  volume_ratio: number
  price: number
  status: string
  discovered_at: string
}

interface Algorithm {
  id: string
  ticker: string
  name: string
  strategy_type: string
  status: string
  paper_trades_done: number
  paper_trades_required: number
  backtest_win_rate: number
  backtest_sharpe: number
  backtest_max_drawdown_pct: number
  backtest_profit_factor: number
  scenarios_passed: number   // repurposed: stores n_trades from full backtest
  paper_win_rate: number
  paper_pnl_pct: number
  sim_retry_count: number
  retire_reason: string
  created_at: string
  deployed_at?: string
  // from data_json
  entry_rules_code?: string
  exit_rules_code?: string
  narrative?: string
}

interface PipelineEvent {
  id: number
  event_type: string
  ticker?: string
  algorithm_id?: string
  stage: string
  status: string
  detail: string
  created_at: string
}

interface PipelineStatus {
  running: boolean
  last_run?: string
  next_run?: string
  stages_completed: string[]
  current_stage?: string
  stocks_discovered_total: number
  algorithms_live: number
  algorithms_paper: number
}

// ── Stage pipeline map ─────────────────────────────────────────────

const STAGES = [
  { key: 'DISCOVERED',    label: 'Discovered',      icon: <Activity className="w-3.5 h-3.5" />,   color: 'text-gray-400 bg-surface-3' },
  { key: 'RESEARCHED',    label: 'Researched',       icon: <BookOpen className="w-3.5 h-3.5" />,   color: 'text-blue-400 bg-blue-900/30' },
  { key: 'ALGO_BUILT',    label: 'Algo Built',       icon: <Cpu className="w-3.5 h-3.5" />,        color: 'text-purple-400 bg-purple-900/30' },
  { key: 'SIMULATED',     label: 'Simulated',        icon: <BarChart2 className="w-3.5 h-3.5" />,  color: 'text-yellow-400 bg-yellow-900/30' },
  { key: 'VALIDATING',    label: 'Validating',       icon: <Shield className="w-3.5 h-3.5" />,     color: 'text-orange-400 bg-orange-900/30' },
  { key: 'LIVE',          label: 'Live',             icon: <Rocket className="w-3.5 h-3.5" />,     color: 'text-accent-green bg-accent-green/15' },
  { key: 'REJECTED',      label: 'Rejected',         icon: <XCircle className="w-3.5 h-3.5" />,    color: 'text-accent-red bg-accent-red/15' },
]

const REASON_LABELS: Record<string, string> = {
  volume_spike: '📈 Volume Spike',
  earnings_surprise: '💰 Earnings Surprise',
  ipo: '🆕 IPO',
  options_flow: '🎯 Options Flow',
  sector_rotation: '🔄 Sector Rotation',
  short_squeeze: '🚀 Short Squeeze',
}

const ALGO_STATUS_COLOR: Record<string, string> = {
  DRAFT: 'text-gray-400 bg-surface-3',
  SIMULATED: 'text-yellow-400 bg-yellow-900/30',
  PAPER_TRADING: 'text-blue-400 bg-blue-900/30',
  LIVE: 'text-accent-green bg-accent-green/15',
  RETIRED: 'text-gray-500 bg-surface-3',
}

// ── Component ──────────────────────────────────────────────────────

export default function Lab() {
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [stocks, setStocks] = useState<DiscoveredStock[]>([])
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([])
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [triggering, setTriggering] = useState(false)
  const [activeTab, setActiveTab] = useState<'stocks' | 'algos' | 'log'>('stocks')

  const load = useCallback(async () => {
    try {
      const [statusResp, stocksResp, algosResp, eventsResp] = await Promise.all([
        axios.get('/api/pipeline/status').catch(() => ({ data: null })),
        axios.get('/api/pipeline/discovered').catch(() => ({ data: { stocks: [] } })),
        axios.get('/api/pipeline/algorithms').catch(() => ({ data: { algorithms: [] } })),
        axios.get('/api/pipeline/events?limit=50').catch(() => ({ data: { events: [] } })),
      ])
      if (statusResp.data) setStatus(statusResp.data)
      setStocks(stocksResp.data.stocks || [])
      setAlgorithms(algosResp.data.algorithms || [])
      setEvents(eventsResp.data.events || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 30_000)
    return () => clearInterval(interval)
  }, [load])

  const triggerPipeline = async () => {
    setTriggering(true)
    try {
      await axios.post('/api/pipeline/trigger')
      setTimeout(load, 2000)
    } catch (e) {
      console.error(e)
    } finally {
      setTimeout(() => setTriggering(false), 3000)
    }
  }

  const retireAlgorithm = async (algoId: string) => {
    try {
      await axios.post(`/api/pipeline/retire/${algoId}`)
      load()
    } catch (e) {
      console.error(e)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 text-brand animate-spin" />
      </div>
    )
  }

  const liveAlgos = algorithms.filter(a => a.status === 'LIVE')
  const paperAlgos = algorithms.filter(a => a.status === 'PAPER_TRADING')
  const draftAlgos = algorithms.filter(a => !['LIVE','PAPER_TRADING','RETIRED'].includes(a.status))

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-3 bg-surface-1 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-purple-600/30 border border-purple-600/40 flex items-center justify-center">
            <FlaskConical className="w-4 h-4 text-purple-400" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white">Pipeline Lab</h1>
            <p className="text-xs text-gray-500">Autonomous discovery → research → algorithm → validate → deploy</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-surface-3 transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={triggerPipeline}
            disabled={triggering || status?.running}
            className="btn btn-primary btn-sm"
          >
            {triggering || status?.running
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Running…</>
              : <><Play className="w-3.5 h-3.5" /> Run Pipeline</>
            }
          </button>
        </div>
      </div>

      {/* Pipeline status bar */}
      <div className="px-6 py-3 border-b border-surface-3 shrink-0">
        <div className="flex items-center gap-6">
          <PipelineStat label="Stocks Found" value={status?.stocks_discovered_total ?? stocks.length} />
          <PipelineStat label="Live Algos" value={liveAlgos.length} color="green" />
          <PipelineStat label="Paper Testing" value={paperAlgos.length} color="blue" />
          <PipelineStat label="In Progress" value={draftAlgos.length} />
          {status?.last_run && (
            <div className="ml-auto text-xs text-gray-600 flex items-center gap-1.5">
              <Clock className="w-3 h-3" />
              Last run {new Date(status.last_run).toLocaleTimeString()}
              {status.next_run && ` · Next ${new Date(status.next_run).toLocaleTimeString()}`}
            </div>
          )}
        </div>

        {/* Pipeline stage flow */}
        <div className="flex items-center gap-1 mt-3 overflow-x-auto pb-1">
          {STAGES.slice(0, -1).map((stage, i) => (
            <React.Fragment key={stage.key}>
              <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium shrink-0 ${
                status?.current_stage === stage.key
                  ? 'bg-brand/20 border border-brand/40 text-brand-glow'
                  : status?.stages_completed?.includes(stage.key)
                  ? 'bg-accent-green/10 border border-accent-green/30 text-accent-green'
                  : 'bg-surface-2 border border-surface-3 text-gray-500'
              }`}>
                {status?.stages_completed?.includes(stage.key)
                  ? <CheckCircle2 className="w-3 h-3" />
                  : status?.current_stage === stage.key
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : stage.icon}
                {stage.label}
              </div>
              {i < STAGES.length - 2 && <ChevronRight className="w-3 h-3 text-gray-700 shrink-0" />}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-surface-3 bg-surface-1 shrink-0">
        <TabBtn active={activeTab === 'stocks'} onClick={() => setActiveTab('stocks')} label={`Discovered (${stocks.length})`} />
        <TabBtn active={activeTab === 'algos'} onClick={() => setActiveTab('algos')} label={`Algorithms (${algorithms.length})`} />
        <TabBtn active={activeTab === 'log'} onClick={() => setActiveTab('log')} label={`Pipeline Log (${events.length})`} />
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'stocks' && <StocksTab stocks={stocks} />}
        {activeTab === 'algos' && <AlgorithmsTab algorithms={algorithms} onRetire={retireAlgorithm} />}
        {activeTab === 'log' && <LogTab events={events} />}
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────

function PipelineStat({ label, value, color }: { label: string; value: number; color?: 'green' | 'blue' }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`text-xl font-bold mono ${color === 'green' ? 'text-accent-green' : color === 'blue' ? 'text-brand-glow' : 'text-white'}`}>
        {value}
      </div>
    </div>
  )
}

function TabBtn({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`px-5 py-3 text-xs font-medium transition-colors ${
        active ? 'text-white border-b-2 border-brand' : 'text-gray-500 hover:text-gray-300'
      }`}
    >
      {label}
    </button>
  )
}

function StageChip({ status }: { status: string }) {
  const stage = STAGES.find(s => s.key === status) || STAGES[0]
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${stage.color}`}>
      {stage.icon}{stage.label}
    </span>
  )
}

function StocksTab({ stocks }: { stocks: DiscoveredStock[] }) {
  const [filterStatus, setFilterStatus] = useState<string>('all')

  const filtered = filterStatus === 'all' ? stocks : stocks.filter(s => s.status === filterStatus)

  return (
    <div className="space-y-4">
      {/* Filter pills */}
      <div className="flex gap-2 flex-wrap">
        {['all', 'DISCOVERED', 'RESEARCHED', 'ALGO_BUILT', 'SIMULATED', 'LIVE', 'REJECTED'].map(f => (
          <button
            key={f}
            onClick={() => setFilterStatus(f)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filterStatus === f ? 'bg-brand text-white' : 'bg-surface-2 text-gray-500 hover:text-gray-300 border border-surface-3'
            }`}
          >
            {f === 'all' ? 'All' : f.charAt(0) + f.slice(1).toLowerCase().replace('_', ' ')}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <EmptyState
          icon={<Activity className="w-10 h-10" />}
          title="No stocks discovered yet"
          subtitle='Run the pipeline to discover new opportunities'
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {filtered.map(stock => (
            <div key={stock.id || stock.ticker} className="bg-surface-2 rounded-xl border border-surface-3 p-4">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <div className="font-bold text-white mono text-base">{stock.ticker}</div>
                  <div className="text-xs text-gray-500 truncate">{stock.company_name || stock.sector}</div>
                </div>
                <StageChip status={stock.status} />
              </div>
              <div className="flex items-center gap-3 text-xs mb-3">
                <span className="text-gray-400">{REASON_LABELS[stock.discovery_reason] || stock.discovery_reason}</span>
                {stock.volume_ratio > 1 && (
                  <span className="text-orange-400 font-semibold">{stock.volume_ratio.toFixed(1)}× vol</span>
                )}
              </div>
              {/* Score bar */}
              <div className="flex items-center gap-2">
                <div className="flex-1 h-1.5 bg-surface-3 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${stock.discovery_score >= 70 ? 'bg-accent-green' : stock.discovery_score >= 50 ? 'bg-accent-yellow' : 'bg-gray-600'}`}
                    style={{ width: `${stock.discovery_score}%` }}
                  />
                </div>
                <span className="text-xs mono font-bold text-white">{stock.discovery_score.toFixed(0)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const STATUS_ORDER: Record<string, number> = {
  LIVE: 0, PAPER_TRADING: 1, SIMULATED: 2, DRAFT: 3, REJECTED: 4, RETIRED: 5,
}

function AlgorithmsTab({ algorithms, onRetire }: { algorithms: Algorithm[]; onRetire: (id: string) => void }) {
  const [showRejected, setShowRejected] = useState(false)

  if (algorithms.length === 0) {
    return (
      <EmptyState
        icon={<Cpu className="w-10 h-10" />}
        title="No algorithms yet"
        subtitle="Run the pipeline to generate trading algorithms from discovered stocks"
      />
    )
  }

  const sorted = [...algorithms].sort((a, b) =>
    (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
  )
  const active = sorted.filter(a => a.status !== 'REJECTED' && a.status !== 'RETIRED')
  const rejected = sorted.filter(a => a.status === 'REJECTED' || a.status === 'RETIRED')

  return (
    <div className="space-y-3">
      {active.map(algo => (
        <AlgoCard key={algo.id} algo={algo} onRetire={onRetire} />
      ))}

      {rejected.length > 0 && (
        <>
          <button
            onClick={() => setShowRejected(v => !v)}
            className="w-full text-xs text-gray-600 hover:text-gray-400 flex items-center gap-2 py-2 border border-dashed border-surface-3 rounded-lg px-3 transition-colors"
          >
            <XCircle className="w-3.5 h-3.5" />
            {showRejected ? 'Hide' : 'Show'} {rejected.length} rejected / retired algorithms
          </button>
          {showRejected && rejected.map(algo => (
            <AlgoCard key={algo.id} algo={algo} onRetire={onRetire} />
          ))}
        </>
      )}
    </div>
  )
}

function AlgoCard({ algo, onRetire }: { algo: Algorithm; onRetire: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const isLive = algo.status === 'LIVE'
  const isPaper = algo.status === 'PAPER_TRADING'
  const isDraft = algo.status === 'DRAFT'
  const isRejected = algo.status === 'REJECTED'
  const paperProgress = algo.paper_trades_required > 0
    ? Math.min(100, (algo.paper_trades_done / algo.paper_trades_required) * 100)
    : 0
  const GRAD_WR = 0.90  // graduation threshold

  return (
    <div className={`bg-surface-2 rounded-xl border p-4 transition-all ${
      isLive ? 'border-accent-green/40 bg-accent-green/5' :
      isPaper ? 'border-brand/40' :
      isRejected ? 'border-accent-red/20 opacity-60' :
      'border-surface-3'
    }`}>
      {/* Row 1: identity + status + expand */}
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5 flex-wrap">
            <span className="font-bold text-white mono text-sm">{algo.ticker}</span>
            <span className="text-xs text-gray-400 capitalize">{algo.strategy_type?.replace('_', ' ')}</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${ALGO_STATUS_COLOR[algo.status] || 'text-gray-400 bg-surface-3'}`}>
              {algo.status.replace('_', ' ')}
            </span>
            {isDraft && (algo.sim_retry_count ?? 0) > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-500 font-mono">
                retry {algo.sim_retry_count}/5
              </span>
            )}
          </div>
          <div className="text-xs text-gray-600 truncate">{algo.name}</div>
        </div>

        {/* Sim metrics row */}
        <div className="flex gap-4 shrink-0 text-center">
          {algo.backtest_win_rate > 0 && (
            <div>
              <div className="text-[10px] text-gray-500">Sim WR</div>
              <div className={`text-sm font-bold mono ${algo.backtest_win_rate >= 0.52 ? 'text-accent-green' : 'text-accent-red'}`}>
                {(algo.backtest_win_rate * 100).toFixed(0)}%
              </div>
            </div>
          )}
          {algo.backtest_sharpe !== 0 && algo.backtest_sharpe !== undefined && (
            <div>
              <div className="text-[10px] text-gray-500">Sharpe</div>
              <div className={`text-sm font-bold mono ${algo.backtest_sharpe >= 1 ? 'text-accent-green' : algo.backtest_sharpe >= 0 ? 'text-accent-yellow' : 'text-accent-red'}`}>
                {algo.backtest_sharpe.toFixed(2)}
              </div>
            </div>
          )}
          {algo.scenarios_passed > 0 && (
            <div>
              <div className="text-[10px] text-gray-500">Sim Trades</div>
              <div className="text-sm font-bold mono text-white">{algo.scenarios_passed}</div>
            </div>
          )}
          {(isPaper || isLive) && algo.paper_trades_done > 0 && (
            <div>
              <div className="text-[10px] text-gray-500">Paper WR</div>
              <div className={`text-sm font-bold mono ${algo.paper_win_rate >= GRAD_WR ? 'text-accent-green' : algo.paper_win_rate >= 0.6 ? 'text-accent-yellow' : 'text-accent-red'}`}>
                {(algo.paper_win_rate * 100).toFixed(0)}%
              </div>
            </div>
          )}
          {(isPaper || isLive) && algo.paper_trades_done > 0 && (
            <div>
              <div className="text-[10px] text-gray-500">Paper PnL</div>
              <div className={`text-sm font-bold mono ${algo.paper_pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                {algo.paper_pnl_pct >= 0 ? '+' : ''}{algo.paper_pnl_pct.toFixed(1)}%
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {isLive && (
            <button onClick={() => onRetire(algo.id)}
              className="text-xs px-2 py-1 rounded bg-accent-red/10 text-accent-red border border-accent-red/30 hover:bg-accent-red/20">
              Retire
            </button>
          )}
          <button onClick={() => setExpanded(e => !e)} className="p-1.5 text-gray-500 hover:text-gray-300">
            <ChevronRight className={`w-4 h-4 transition-transform ${expanded ? 'rotate-90' : ''}`} />
          </button>
        </div>
      </div>

      {/* Paper trading progress bar */}
      {isPaper && (
        <div className="mt-3">
          <div className="flex justify-between text-xs mb-1">
            <span className="text-gray-500">Paper Trading Progress</span>
            <span className="text-gray-400 mono">{algo.paper_trades_done}/{algo.paper_trades_required} trades</span>
          </div>
          <div className="h-2 bg-surface-3 rounded-full overflow-hidden">
            <div className="h-full bg-brand rounded-full transition-all" style={{ width: `${paperProgress}%` }} />
          </div>
          {/* WR vs graduation target */}
          {algo.paper_trades_done >= 10 && (
            <div className="flex items-center justify-between mt-1.5 text-xs">
              <span className="text-gray-600">Win rate toward 90% graduation</span>
              <div className="flex items-center gap-2">
                <div className="w-24 h-1 bg-surface-3 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${algo.paper_win_rate >= GRAD_WR ? 'bg-accent-green' : 'bg-accent-yellow'}`}
                    style={{ width: `${Math.min(100, (algo.paper_win_rate / GRAD_WR) * 100)}%` }}
                  />
                </div>
                <span className={`mono font-bold ${algo.paper_win_rate >= GRAD_WR ? 'text-accent-green' : 'text-accent-yellow'}`}>
                  {(algo.paper_win_rate * 100).toFixed(0)}% / 90%
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Rejection reason */}
      {isRejected && algo.retire_reason && (
        <div className="mt-2 text-xs text-accent-red/70 bg-accent-red/5 rounded px-2 py-1">
          ✕ {algo.retire_reason}
        </div>
      )}

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-surface-3 space-y-3">
          {/* Backtest metrics grid */}
          <div>
            <div className="text-xs text-gray-600 mb-2 font-medium">Simulation Results (5yr real data)</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
              <MetricCell label="Win Rate" value={algo.backtest_win_rate > 0 ? `${(algo.backtest_win_rate*100).toFixed(1)}%` : '—'} bad={algo.backtest_win_rate > 0 && algo.backtest_win_rate < 0.52} />
              <MetricCell label="Sharpe Ratio" value={algo.backtest_sharpe !== 0 ? algo.backtest_sharpe.toFixed(2) : '—'} bad={algo.backtest_sharpe < 0} />
              <MetricCell label="Profit Factor" value={algo.backtest_profit_factor > 0 ? algo.backtest_profit_factor.toFixed(2) : '—'} bad={algo.backtest_profit_factor > 0 && algo.backtest_profit_factor < 1.2} />
              <MetricCell label="Max Drawdown" value={algo.backtest_max_drawdown_pct > 0 ? `${algo.backtest_max_drawdown_pct.toFixed(1)}%` : '—'} bad={algo.backtest_max_drawdown_pct > 40} />
              <MetricCell label="Trades (backtest)" value={algo.scenarios_passed > 0 ? String(algo.scenarios_passed) : '0'} />
              <MetricCell label="Strategy Type" value={algo.strategy_type || '—'} />
              <MetricCell label="Created" value={new Date(algo.created_at).toLocaleDateString()} />
              {algo.sim_retry_count > 0 && <MetricCell label="Sim Retries" value={`${algo.sim_retry_count}/5`} bad={algo.sim_retry_count >= 4} />}
            </div>
          </div>

          {/* Paper trading detail */}
          {(isPaper || isLive) && algo.paper_trades_done > 0 && (
            <div>
              <div className="text-xs text-gray-600 mb-2 font-medium">Paper Trading Results</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
                <MetricCell label="Trades Done" value={`${algo.paper_trades_done}/${algo.paper_trades_required}`} />
                <MetricCell label="Paper Win Rate" value={`${(algo.paper_win_rate*100).toFixed(1)}%`} bad={algo.paper_win_rate < 0.9} good={algo.paper_win_rate >= 0.9} />
                <MetricCell label="Paper PnL" value={`${algo.paper_pnl_pct >= 0 ? '+' : ''}${algo.paper_pnl_pct.toFixed(2)}%`} bad={algo.paper_pnl_pct < 0} />
                <MetricCell label="To Graduate" value={algo.paper_win_rate >= 0.9 ? '✓ READY' : `${(algo.paper_win_rate*100).toFixed(0)}% / 90%`} good={algo.paper_win_rate >= 0.9} />
              </div>
            </div>
          )}

          {/* Entry/exit code preview */}
          {algo.entry_rules_code && (
            <div>
              <div className="text-xs text-gray-600 mb-1 font-medium">Entry Logic</div>
              <pre className="text-[10px] text-gray-400 bg-surface-3 rounded-lg p-3 overflow-x-auto max-h-32 leading-relaxed">
                {algo.entry_rules_code.trim()}
              </pre>
            </div>
          )}
          {algo.exit_rules_code && (
            <div>
              <div className="text-xs text-gray-600 mb-1 font-medium">Exit Logic</div>
              <pre className="text-[10px] text-gray-400 bg-surface-3 rounded-lg p-3 overflow-x-auto max-h-32 leading-relaxed">
                {algo.exit_rules_code.trim()}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MetricCell({ label, value, bad, good }: { label: string; value: string; bad?: boolean; good?: boolean }) {
  return (
    <div className="bg-surface-3 rounded-lg p-2">
      <div className="text-gray-600 mb-0.5 text-[10px]">{label}</div>
      <div className={`font-semibold mono text-xs ${good ? 'text-accent-green' : bad ? 'text-accent-red' : 'text-white'}`}>{value}</div>
    </div>
  )
}

function LogTab({ events }: { events: PipelineEvent[] }) {
  const statusIcon = (s: string) => {
    if (s === 'SUCCESS' || s === 'COMPLETE') return <CheckCircle2 className="w-3.5 h-3.5 text-accent-green shrink-0" />
    if (s === 'FAILED' || s === 'REJECTED') return <XCircle className="w-3.5 h-3.5 text-accent-red shrink-0" />
    if (s === 'RUNNING' || s === 'STARTED') return <Loader2 className="w-3.5 h-3.5 text-brand-glow animate-spin shrink-0" />
    return <Activity className="w-3.5 h-3.5 text-gray-500 shrink-0" />
  }

  if (events.length === 0) {
    return (
      <EmptyState
        icon={<Activity className="w-10 h-10" />}
        title="No pipeline events yet"
        subtitle="Events will appear here as the pipeline runs"
      />
    )
  }

  return (
    <div className="space-y-1">
      {events.map(ev => (
        <div key={ev.id} className="flex items-start gap-3 py-2 border-b border-surface-3 last:border-0">
          {statusIcon(ev.status)}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-semibold text-white">{ev.stage}</span>
              {ev.ticker && <span className="mono text-brand-glow">{ev.ticker}</span>}
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                ev.status === 'SUCCESS' || ev.status === 'COMPLETE' ? 'bg-accent-green/10 text-accent-green' :
                ev.status === 'FAILED' || ev.status === 'REJECTED' ? 'bg-accent-red/10 text-accent-red' :
                'bg-surface-3 text-gray-400'
              }`}>{ev.status}</span>
            </div>
            {ev.detail && <p className="text-xs text-gray-500 mt-0.5 truncate">{ev.detail}</p>}
          </div>
          <span className="text-[10px] text-gray-700 shrink-0 mono">
            {new Date(ev.created_at).toLocaleTimeString()}
          </span>
        </div>
      ))}
    </div>
  )
}

function EmptyState({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle: string }) {
  return (
    <div className="text-center py-16 text-gray-600">
      <div className="flex justify-center mb-3 opacity-20">{icon}</div>
      <p className="text-sm font-medium text-gray-500">{title}</p>
      <p className="text-xs mt-1">{subtitle}</p>
    </div>
  )
}
