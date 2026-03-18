import React from 'react'

interface ProbabilityGaugeProps {
  score: number        // 0-1
  grade: string
  label?: string
  size?: number
}

function getGradeColor(grade: string): string {
  switch (grade) {
    case 'A+': return '#22c55e'
    case 'A':  return '#22c55e'
    case 'B':  return '#60a5fa'
    case 'C':  return '#eab308'
    case 'D':  return '#f97316'
    case 'F':  return '#ef4444'
    default:   return '#94a3b8'
  }
}

export default function ProbabilityGauge({
  score,
  grade,
  label = 'Win Probability',
  size = 120,
}: ProbabilityGaugeProps) {
  const pct = Math.max(0, Math.min(1, score))
  const color = getGradeColor(grade)

  // Semicircle arc parameters
  const r = (size / 2) - 10
  const cx = size / 2
  const cy = size / 2 + 10
  const strokeWidth = 10

  // Arc math: full semicircle = 180 degrees (π radians)
  const circumference = Math.PI * r   // half circumference
  const offset = circumference * (1 - pct)

  // Arc from 180° to 0° (left to right)
  const startX = cx - r
  const startY = cy
  const endX = cx + r
  const endY = cy

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size / 2 + 20} viewBox={`0 0 ${size} ${size / 2 + 20}`}>
        {/* Track */}
        <path
          d={`M ${startX} ${startY} A ${r} ${r} 0 0 1 ${endX} ${endY}`}
          fill="none"
          stroke="#252d40"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
        {/* Progress */}
        <path
          d={`M ${startX} ${startY} A ${r} ${r} 0 0 1 ${endX} ${endY}`}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={`${circumference}`}
          strokeDashoffset={`${offset}`}
          style={{ transition: 'stroke-dashoffset 0.5s ease' }}
        />
        {/* Score text */}
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          fill={color}
          fontSize="18"
          fontWeight="bold"
          fontFamily="JetBrains Mono, monospace"
        >
          {(pct * 100).toFixed(1)}%
        </text>
        {/* Grade */}
        <text
          x={cx}
          y={cy + 14}
          textAnchor="middle"
          fill={color}
          fontSize="12"
          fontWeight="600"
        >
          Grade {grade}
        </text>
      </svg>
      <span className="text-xs text-gray-400">{label}</span>
    </div>
  )
}
