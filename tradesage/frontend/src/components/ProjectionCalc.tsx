import React, { useState } from 'react'
import { DollarSign, Calculator } from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts'

interface ProjectionCalcProps {
  defaultWinRate?: number
  defaultAvgWin?: number
  defaultAvgLoss?: number
}

function projectGrowth(
  initial: number,
  winRate: number,
  avgWinPct: number,
  avgLossPct: number,
  trades: number
) {
  const expected = winRate * avgWinPct + (1 - winRate) * (-avgLossPct)
  const upper_r = winRate * avgWinPct * 1.3 + (1 - winRate) * (-avgLossPct * 0.7)
  const lower_r = winRate * avgWinPct * 0.7 + (1 - winRate) * (-avgLossPct * 1.3)

  const points = []
  let exp = initial, upper = initial, lower = initial
  for (let i = 0; i <= trades; i++) {
    points.push({
      trade: i,
      expected: Math.round(exp * 100) / 100,
      upper: Math.round(upper * 100) / 100,
      lower: Math.round(lower * 100) / 100,
    })
    exp *= 1 + expected
    upper *= 1 + upper_r
    lower *= 1 + lower_r
  }
  return points
}

export default function ProjectionCalc({
  defaultWinRate = 0.55,
  defaultAvgWin = 4,
  defaultAvgLoss = 2,
}: ProjectionCalcProps) {
  const [initial, setInitial] = useState(100)
  const [winRate, setWinRate] = useState(defaultWinRate * 100)
  const [avgWin, setAvgWin] = useState(defaultAvgWin)
  const [avgLoss, setAvgLoss] = useState(defaultAvgLoss)
  const [numTrades, setNumTrades] = useState(50)

  const data = projectGrowth(initial, winRate / 100, avgWin / 100, avgLoss / 100, numTrades)
  const final = data[data.length - 1]
  const expectedReturn = ((final.expected - initial) / initial * 100).toFixed(1)
  const bestReturn = ((final.upper - initial) / initial * 100).toFixed(1)
  const worstReturn = ((final.lower - initial) / initial * 100).toFixed(1)

  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-2 mb-1">
        <Calculator className="w-4 h-4 text-brand-glow" />
        <h3 className="text-sm font-semibold text-gray-300">$100 Projection Calculator</h3>
      </div>

      {/* Inputs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <InputField
          label="Starting ($)"
          value={initial}
          onChange={setInitial}
          min={10} max={100000} step={10}
        />
        <InputField
          label="Win Rate (%)"
          value={winRate}
          onChange={setWinRate}
          min={1} max={99} step={1}
        />
        <InputField
          label="Avg Win (%)"
          value={avgWin}
          onChange={setAvgWin}
          min={0.1} max={50} step={0.1}
        />
        <InputField
          label="Avg Loss (%)"
          value={avgLoss}
          onChange={setAvgLoss}
          min={0.1} max={50} step={0.1}
        />
      </div>

      <div className="flex items-center gap-3 text-xs">
        <label className="text-gray-400">Trades to simulate:</label>
        <input
          type="range"
          min={10} max={200} step={10}
          value={numTrades}
          onChange={e => setNumTrades(Number(e.target.value))}
          className="flex-1"
        />
        <span className="text-white mono w-8">{numTrades}</span>
      </div>

      {/* Results summary */}
      <div className="grid grid-cols-3 gap-3 text-center text-xs">
        <div className="card-dark">
          <div className="text-accent-green font-bold mono text-lg">${final.expected.toFixed(0)}</div>
          <div className="text-gray-400">Expected (+{expectedReturn}%)</div>
        </div>
        <div className="card-dark">
          <div className="text-green-300 font-bold mono text-lg">${final.upper.toFixed(0)}</div>
          <div className="text-gray-400">Best Case (+{bestReturn}%)</div>
        </div>
        <div className="card-dark">
          <div className="text-accent-red font-bold mono text-lg">${final.lower.toFixed(0)}</div>
          <div className="text-gray-400">Worst Case ({worstReturn}%)</div>
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="gradUp" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#22c55e" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#22c55e" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="gradDown" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.25} />
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#252d40" />
          <XAxis dataKey="trade" tick={{ fill: '#94a3b8', fontSize: 10 }} label={{ value: 'Trades', fill: '#94a3b8', fontSize: 10, position: 'insideBottom', offset: -2 }} />
          <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={v => `$${v}`} />
          <Tooltip
            formatter={(v: number) => [`$${v.toFixed(2)}`]}
            contentStyle={{ background: '#161b27', border: '1px solid #252d40', borderRadius: '8px', fontSize: '11px' }}
          />
          <ReferenceLine y={initial} stroke="#94a3b8" strokeDasharray="3 3" />
          <Area type="monotone" dataKey="upper" name="Best" stroke="#22c55e" strokeWidth={1.5} fill="url(#gradUp)" dot={false} />
          <Area type="monotone" dataKey="expected" name="Expected" stroke="#60a5fa" strokeWidth={2.5} fill="transparent" dot={false} />
          <Area type="monotone" dataKey="lower" name="Worst" stroke="#ef4444" strokeWidth={1.5} fill="url(#gradDown)" dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function InputField({
  label, value, onChange, min, max, step
}: {
  label: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step: number
}) {
  return (
    <div>
      <label className="text-gray-400 block mb-1">{label}</label>
      <input
        type="number"
        className="w-full bg-surface-2 border border-surface-3 rounded-lg px-2 py-1.5 text-white mono text-sm focus:outline-none focus:border-brand"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={e => onChange(Number(e.target.value))}
      />
    </div>
  )
}
