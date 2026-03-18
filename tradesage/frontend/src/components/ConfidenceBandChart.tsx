import React from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine
} from 'recharts'

interface DataPoint {
  label: string
  expected: number
  upper: number
  lower: number
}

interface ConfidenceBandChartProps {
  data: DataPoint[]
  title?: string
}

const CustomTooltip = ({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}) => {
  if (!active || !payload?.length) return null
  return (
    <div className="card text-xs p-2 space-y-1">
      <p className="text-gray-300 font-semibold">{label}</p>
      {payload.map(p => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: {p.value >= 0 ? '+' : ''}{p.value.toFixed(2)}%
        </p>
      ))}
    </div>
  )
}

export default function ConfidenceBandChart({ data, title }: ConfidenceBandChartProps) {
  return (
    <div className="card">
      {title && <h3 className="text-sm font-semibold text-gray-300 mb-3">{title}</h3>}
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="gradUpper" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#22c55e" stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="gradLower" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#252d40" />
          <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 11 }} />
          <YAxis
            tick={{ fill: '#94a3b8', fontSize: 11 }}
            tickFormatter={v => `${v}%`}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ fontSize: '11px', color: '#94a3b8' }} />
          <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="3 3" />
          <Area
            type="monotone"
            dataKey="upper"
            name="Best Case (95% CI)"
            stroke="#22c55e"
            strokeWidth={1.5}
            fill="url(#gradUpper)"
            dot={false}
          />
          <Area
            type="monotone"
            dataKey="expected"
            name="Expected Return"
            stroke="#60a5fa"
            strokeWidth={2}
            fill="transparent"
            dot={false}
          />
          <Area
            type="monotone"
            dataKey="lower"
            name="Worst Case (95% CI)"
            stroke="#ef4444"
            strokeWidth={1.5}
            fill="url(#gradLower)"
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
