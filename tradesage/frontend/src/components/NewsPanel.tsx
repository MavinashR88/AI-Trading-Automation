import React from 'react'
import { Newspaper, TrendingUp, TrendingDown, Minus, AlertCircle } from 'lucide-react'

interface NewsItem {
  event_id?: string
  ticker: string
  headline: string
  source: string
  sentiment_score: number
  urgency: string
  catalyst: string
  age_minutes?: number
  timestamp: string
}

interface NewsPanelProps {
  news: NewsItem[]
  title?: string
}

function SentimentBar({ score }: { score: number }) {
  const pct = ((score + 1) / 2) * 100
  const color = score > 0.2 ? '#22c55e' : score < -0.2 ? '#ef4444' : '#eab308'
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="flex-1 bg-surface-3 rounded-full h-1.5 relative">
        {/* Midpoint marker */}
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-gray-500" />
        <div
          className="h-1.5 rounded-full transition-all"
          style={{
            width: `${Math.abs(score) * 50}%`,
            marginLeft: score >= 0 ? '50%' : `${50 - Math.abs(score) * 50}%`,
            backgroundColor: color,
          }}
        />
      </div>
      <span className="mono" style={{ color }}>{score.toFixed(2)}</span>
    </div>
  )
}

function urgencyBadge(urgency: string) {
  switch (urgency) {
    case 'immediate': return <span className="badge badge-red">IMMEDIATE</span>
    case 'override_cancel': return <span className="badge badge-red">CANCEL ALL</span>
    default: return <span className="badge badge-yellow">MONITOR</span>
  }
}

export default function NewsPanel({ news, title = 'News Feed' }: NewsPanelProps) {
  if (!news.length) {
    return (
      <div className="card text-center py-8">
        <Newspaper className="w-8 h-8 text-gray-500 mx-auto mb-2" />
        <p className="text-sm text-gray-400">No news items yet. Scanning hourly...</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-300">{title}</h3>
      {news.map((item, i) => (
        <div key={item.event_id || i} className="card space-y-2">
          {/* Ticker + urgency + time */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="badge badge-blue">{item.ticker}</span>
              {urgencyBadge(item.urgency)}
              <span className="text-xs text-gray-500">{item.source}</span>
            </div>
            <span className="text-xs text-gray-500 shrink-0">
              {item.age_minutes !== undefined ? `${item.age_minutes}m ago` : new Date(item.timestamp).toLocaleTimeString()}
            </span>
          </div>

          {/* Headline */}
          <p className="text-sm text-gray-200 leading-tight">{item.headline}</p>

          {/* Sentiment */}
          <SentimentBar score={item.sentiment_score} />

          {/* Catalyst */}
          {item.catalyst && item.catalyst !== 'No catalyst' && (
            <div className="text-xs text-brand-glow">
              Key catalyst: {item.catalyst}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
