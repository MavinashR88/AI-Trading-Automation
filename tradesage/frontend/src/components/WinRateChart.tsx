import React from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts'

interface WinRateSnapshot {
  snapshot_id?: number
  snapshot_date?: string
  rolling_100_win_rate: number
  total_trades: number
  consecutive_wins?: number
}

interface WinRateChartProps {
  history: WinRateSnapshot[]
  currentWinRate?: number
}

const CustomTooltip = ({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ value: number }>
  label?: string
}) => {
  if (!active || !payload?.length) return null
  return (
    <div className="card text-xs p-2 space-y-0.5">
      <p className="text-gray-300">{label}</p>
      <p className="text-brand-glow font-bold">{(payload[0].value * 100).toFixed(1)}% win rate</p>
    </div>
  )
}

export default function WinRateChart({ history, currentWinRate }: WinRateChartProps) {
  const data = history.slice().reverse().map((snap, i) => ({
    index: i + 1,
    win_rate: snap.rolling_100_win_rate,
    label: snap.snapshot_date
      ? new Date(snap.snapshot_date).toLocaleDateString()
      : `#${i + 1}`,
  }))

  const current = currentWinRate ?? (data[data.length - 1]?.win_rate || 0)

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-300">Rolling Win Rate (last 100 trades)</h3>
        <div className="flex items-center gap-2">
          <div className="text-2xl font-black mono text-brand-glow">
            {(current * 100).toFixed(1)}%
          </div>
        </div>
      </div>

      {data.length < 2 ? (
        <div className="text-center py-6 text-sm text-gray-400">
          Not enough trade history yet (need at least 2 closed trades).
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={data} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#252d40" />
            <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 10 }} hide={data.length > 20} />
            <YAxis
              domain={[0, 1]}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              tickFormatter={v => `${(v * 100).toFixed(0)}%`}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={0.5} stroke="#94a3b8" strokeDasharray="4 4" label={{ value: '50%', fill: '#94a3b8', fontSize: 10 }} />
            <ReferenceLine y={0.65} stroke="#22c55e" strokeDasharray="2 4" opacity={0.5} />
            <Line
              type="monotone"
              dataKey="win_rate"
              stroke="#60a5fa"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, fill: '#60a5fa' }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
