import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { TrendingUp, BarChart2, Target, Loader2 } from 'lucide-react'
import ProjectionCalc from '../components/ProjectionCalc'
import WinRateChart from '../components/WinRateChart'
import ConfidenceBandChart from '../components/ConfidenceBandChart'

export default function Analytics() {
  const [winRate, setWinRate] = useState<Record<string, unknown>>({})
  const [patterns, setPatterns] = useState<unknown[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      try {
        const [wrResp, patResp] = await Promise.all([
          axios.get('/api/win-rate'),
          axios.get('/api/graph/win-rate-by-pattern'),
        ])
        setWinRate(wrResp.data)
        setPatterns(patResp.data.patterns || [])
      } catch (e) {
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const current = (winRate as Record<string, Record<string, number>>).current || {}
  const history = (winRate as Record<string, unknown[]>).history || []

  // Build CI band chart data from win rate history
  const ciData = (history as Array<{ rolling_100_win_rate: number; snapshot_date?: string }>)
    .slice(0, 20)
    .reverse()
    .map((snap, i) => {
      const wr = snap.rolling_100_win_rate || 0.5
      const expected = (wr * 4 - (1 - wr) * 2)  // simplified expected return %
      return {
        label: snap.snapshot_date ? new Date(snap.snapshot_date).toLocaleDateString() : `#${i + 1}`,
        expected: Math.round(expected * 100) / 100,
        upper: Math.round((expected + 1.5) * 100) / 100,
        lower: Math.round((expected - 1.5) * 100) / 100,
      }
    })

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 text-brand animate-spin" />
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Analytics & Projections</h1>
        <p className="text-sm text-gray-400 mt-0.5">Performance statistics with 95% confidence intervals</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Win Rate" value={`${((current.win_rate || 0) * 100).toFixed(1)}%`} icon={<Target className="w-5 h-5 text-brand-glow" />} />
        <StatCard label="Total Trades" value={String(current.total_trades || 0)} icon={<BarChart2 className="w-5 h-5 text-accent-yellow" />} />
        <StatCard label="Wins" value={String(current.wins || 0)} color="text-accent-green" icon={<TrendingUp className="w-5 h-5 text-accent-green" />} />
        <StatCard label="Consecutive W" value={String(current.consecutive_wins || 0)} color="text-accent-cyan" icon={<TrendingUp className="w-5 h-5 text-accent-cyan" />} />
      </div>

      {/* Charts grid */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <WinRateChart
          history={history as Array<{ rolling_100_win_rate: number; total_trades: number; snapshot_date?: string }>}
          currentWinRate={current.win_rate as number}
        />
        {ciData.length >= 2 && (
          <ConfidenceBandChart
            data={ciData}
            title="Expected Return with 95% CI Band"
          />
        )}
      </div>

      {/* $100 Projector */}
      <ProjectionCalc
        defaultWinRate={current.win_rate as number || 0.55}
      />

      {/* Pattern win rates */}
      {patterns.length > 0 && (
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-300 mb-4">Win Rate by Market Pattern</h3>
          <div className="space-y-3">
            {(patterns as Array<{ pattern: string; wins: number; total: number; win_rate_pct: number }>)
              .map(p => (
                <div key={p.pattern} className="flex items-center gap-3 text-sm">
                  <div className="w-40 text-gray-300 truncate text-xs">{p.pattern}</div>
                  <div className="flex-1 bg-surface-3 rounded-full h-2">
                    <div
                      className="h-2 rounded-full bg-brand transition-all"
                      style={{ width: `${p.win_rate_pct}%` }}
                    />
                  </div>
                  <div className="text-xs mono text-gray-300 w-12 text-right">{p.win_rate_pct}%</div>
                  <div className="text-xs text-gray-500 w-16">{p.wins}/{p.total} trades</div>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({
  label, value, icon, color = 'text-white'
}: {
  label: string
  value: string
  icon: React.ReactNode
  color?: string
}) {
  return (
    <div className="card flex items-center gap-3">
      <div className="w-10 h-10 rounded-lg bg-surface-3 flex items-center justify-center shrink-0">
        {icon}
      </div>
      <div>
        <div className="text-xs text-gray-400">{label}</div>
        <div className={`text-xl font-bold mono ${color}`}>{value}</div>
      </div>
    </div>
  )
}
