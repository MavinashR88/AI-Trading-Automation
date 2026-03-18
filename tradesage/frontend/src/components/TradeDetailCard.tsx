import React, { useState } from 'react'
import {
  TrendingUp, TrendingDown, Shield, BookOpen,
  ChevronDown, ChevronUp, DollarSign, Target
} from 'lucide-react'
import ProbabilityGauge from './ProbabilityGauge'

interface ProbabilityScore {
  composite_score: number
  composite_pct: string
  signal_grade: string
  ci_lower: number
  ci_upper: number
  expected_return: number
  proj_100_expected: number
  proj_100_best: number
  proj_100_worst: number
  proj_100_double_trades: number
  news_score: number
  risk_score: number
  mentor_score: number
  historical_win_rate: number
}

interface ReviewNote {
  decision: string
  trader_voice: string
  reasoning: string
  news_alignment: string
  news_catalyst: string
  confidence_score: number
  book_reference: string
}

interface Trade {
  trade_id: string
  ticker: string
  market_type: string
  side: string
  entry_price: number
  exit_price?: number
  pnl_pct?: number
  pnl_dollars?: number
  outcome?: string
  hold_minutes?: number
  mode: string
  probability_score_json?: ProbabilityScore
  review_note_json?: ReviewNote
  created_at: string
}

interface TradeDetailCardProps {
  trade: Trade
}

function formatCurrency(n: number) {
  return n >= 0
    ? `+$${n.toFixed(2)}`
    : `-$${Math.abs(n).toFixed(2)}`
}

function formatPct(n: number) {
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

export default function TradeDetailCard({ trade }: TradeDetailCardProps) {
  const [expanded, setExpanded] = useState(false)

  const prob = trade.probability_score_json
  const review = trade.review_note_json
  const isWin = trade.outcome === 'WIN'
  const isLoss = trade.outcome === 'LOSS'
  const isOpen = !trade.outcome || trade.outcome === 'OPEN'

  return (
    <div className={`card border transition-all duration-200 ${
      isWin ? 'border-accent-green/30 glow-green' :
      isLoss ? 'border-accent-red/30 glow-red' :
      'border-surface-3'
    }`}>
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center font-bold text-sm ${
            trade.side === 'buy' ? 'bg-accent-green/20 text-accent-green' : 'bg-accent-red/20 text-accent-red'
          }`}>
            {trade.ticker.slice(0, 3)}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-white">{trade.ticker}</span>
              <span className={`badge ${trade.side === 'buy' ? 'badge-green' : 'badge-red'}`}>
                {trade.side.toUpperCase()}
              </span>
              <span className={`badge ${trade.mode === 'paper' ? 'badge-blue' : 'badge-red'}`}>
                {trade.mode.toUpperCase()}
              </span>
            </div>
            <div className="text-xs text-gray-400 mono mt-0.5">
              @ ${trade.entry_price.toFixed(4)} · {new Date(trade.created_at).toLocaleString()}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* P&L */}
          {!isOpen && trade.pnl_pct !== undefined && (
            <div className="text-right">
              <div className={`font-bold mono text-lg ${isWin ? 'text-accent-green' : 'text-accent-red'}`}>
                {formatPct(trade.pnl_pct)}
              </div>
              {trade.pnl_dollars !== undefined && (
                <div className={`text-xs mono ${isWin ? 'text-accent-green/70' : 'text-accent-red/70'}`}>
                  {formatCurrency(trade.pnl_dollars)}
                </div>
              )}
            </div>
          )}
          {isOpen && (
            <span className="badge badge-yellow">OPEN</span>
          )}
          {/* Grade */}
          {prob && (
            <div className={`text-2xl font-black grade-${prob.signal_grade.replace('+', 'Ap')}`}>
              {prob.signal_grade}
            </div>
          )}
          <button
            className="btn-ghost btn p-1.5"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-4 space-y-4">
          {/* Probability Score */}
          {prob && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="flex justify-center">
                <ProbabilityGauge
                  score={prob.composite_score}
                  grade={prob.signal_grade}
                  size={130}
                />
              </div>

              {/* Score breakdown */}
              <div className="space-y-2">
                <h4 className="text-xs font-semibold text-gray-400 uppercase">Score Breakdown</h4>
                {[
                  { label: 'News', value: prob.news_score, weight: '20%' },
                  { label: 'Risk', value: prob.risk_score, weight: '25%' },
                  { label: 'Mentor', value: prob.mentor_score, weight: '35%' },
                  { label: 'History', value: prob.historical_win_rate, weight: '20%' },
                ].map(item => (
                  <div key={item.label} className="flex items-center gap-2 text-xs">
                    <span className="text-gray-400 w-12">{item.label}</span>
                    <div className="flex-1 bg-surface-3 rounded-full h-1.5">
                      <div
                        className="h-1.5 rounded-full bg-brand"
                        style={{ width: `${item.value * 100}%` }}
                      />
                    </div>
                    <span className="text-gray-300 mono w-12 text-right">
                      {(item.value * 100).toFixed(0)}%
                    </span>
                    <span className="text-gray-500 w-8">{item.weight}</span>
                  </div>
                ))}
              </div>

              {/* $100 Projector */}
              <div className="card-dark space-y-2">
                <h4 className="text-xs font-semibold text-gray-400 uppercase flex items-center gap-1">
                  <DollarSign className="w-3 h-3" /> $100 Projector
                </h4>
                <div className="space-y-1.5 text-xs">
                  <Row label="Expected" value={`$${prob.proj_100_expected.toFixed(2)}`} color="text-brand-glow" />
                  <Row label="Best (95% CI)" value={`$${prob.proj_100_best.toFixed(2)}`} color="text-accent-green" />
                  <Row label="Worst (95% CI)" value={`$${prob.proj_100_worst.toFixed(2)}`} color="text-accent-red" />
                  <Row label="CI Range" value={`${prob.ci_lower.toFixed(2)}% → ${prob.ci_upper.toFixed(2)}%`} />
                  <Row
                    label="Trades to 2x"
                    value={prob.proj_100_double_trades === 9999 ? 'N/A' : `~${prob.proj_100_double_trades}`}
                    color="text-accent-yellow"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Mentor Review Note */}
          {review && (
            <div className="card-dark">
              <div className="flex items-center gap-2 mb-2">
                <Shield className="w-4 h-4 text-brand-glow" />
                <span className="text-xs font-semibold text-gray-300">Mentor Review</span>
                <span className={`badge ${
                  review.decision === 'APPROVED' ? 'badge-green' :
                  review.decision === 'BLOCKED' ? 'badge-red' :
                  'badge-yellow'
                }`}>{review.decision}</span>
                <span className={`badge ${
                  review.news_alignment === 'CONFIRMS' ? 'badge-green' :
                  review.news_alignment === 'CONTRADICTS' ? 'badge-red' :
                  'badge-yellow'
                }`}>{review.news_alignment}</span>
              </div>
              <p className="text-xs text-gray-300 mb-2">{review.reasoning}</p>
              <div className="flex items-start gap-1 text-xs text-gray-400">
                <BookOpen className="w-3 h-3 shrink-0 mt-0.5 text-accent-purple" />
                <span className="italic">{review.book_reference}</span>
              </div>
              {review.trader_voice && (
                <div className="mt-1 text-xs text-brand-glow">Voice: {review.trader_voice}</div>
              )}
            </div>
          )}

          {/* Trade vitals */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <StatBox label="Entry" value={`$${trade.entry_price.toFixed(4)}`} />
            {trade.exit_price && <StatBox label="Exit" value={`$${trade.exit_price.toFixed(4)}`} />}
            {trade.hold_minutes && <StatBox label="Hold" value={`${trade.hold_minutes}m`} />}
            <StatBox label="Market" value={trade.market_type} />
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ label, value, color = 'text-gray-200' }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-400">{label}</span>
      <span className={`mono font-medium ${color}`}>{value}</span>
    </div>
  )
}

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="card-dark text-center">
      <div className="text-gray-400 text-xs mb-0.5">{label}</div>
      <div className="text-gray-100 mono font-semibold">{value}</div>
    </div>
  )
}
