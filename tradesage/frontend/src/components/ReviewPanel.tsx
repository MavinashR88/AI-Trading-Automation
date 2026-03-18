import React from 'react'
import { Shield, BookOpen, AlertTriangle, CheckCircle, XCircle, Clock } from 'lucide-react'

interface ReviewNote {
  trade_id: string
  decision: string
  trader_voice: string
  reasoning: string
  news_alignment: string
  news_catalyst: string
  price_vs_news: string
  confidence_score: number
  book_reference: string
  timestamp: string
}

interface ReviewPanelProps {
  reviews: ReviewNote[]
  latestOnly?: boolean
}

function decisionIcon(decision: string) {
  switch (decision) {
    case 'APPROVED': return <CheckCircle className="w-4 h-4 text-accent-green" />
    case 'BLOCKED':  return <XCircle className="w-4 h-4 text-accent-red" />
    case 'DELAYED':  return <Clock className="w-4 h-4 text-accent-yellow" />
    default:         return <AlertTriangle className="w-4 h-4 text-accent-yellow" />
  }
}

function alignmentBadge(alignment: string) {
  const cls =
    alignment === 'CONFIRMS' ? 'badge-green' :
    alignment === 'CONTRADICTS' ? 'badge-red' :
    alignment === 'OVERRIDE' ? 'badge-red' : 'badge-yellow'
  return <span className={`badge ${cls}`}>{alignment}</span>
}

export default function ReviewPanel({ reviews, latestOnly = false }: ReviewPanelProps) {
  const displayed = latestOnly ? reviews.slice(0, 1) : reviews

  if (!displayed.length) {
    return (
      <div className="card text-center py-8">
        <Shield className="w-8 h-8 text-gray-500 mx-auto mb-2" />
        <p className="text-sm text-gray-400">No review notes yet. Trades will appear here before execution.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {displayed.map(note => (
        <div
          key={note.trade_id + note.timestamp}
          className={`card border-l-4 ${
            note.decision === 'APPROVED' ? 'border-l-accent-green' :
            note.decision === 'BLOCKED'  ? 'border-l-accent-red' :
            'border-l-accent-yellow'
          }`}
        >
          {/* Header */}
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              {decisionIcon(note.decision)}
              <span className="text-sm font-bold text-white">{note.decision}</span>
              {alignmentBadge(note.news_alignment)}
            </div>
            <span className="text-xs text-gray-500">
              {new Date(note.timestamp).toLocaleTimeString()}
            </span>
          </div>

          {/* Confidence */}
          <div className="flex items-center gap-2 mb-2">
            <div className="flex-1 bg-surface-3 rounded-full h-1.5">
              <div
                className="h-1.5 rounded-full bg-brand transition-all"
                style={{ width: `${note.confidence_score * 100}%` }}
              />
            </div>
            <span className="text-xs text-gray-400 mono">{(note.confidence_score * 100).toFixed(0)}%</span>
          </div>

          {/* Reasoning */}
          <p className="text-xs text-gray-300 leading-relaxed mb-2">{note.reasoning}</p>

          {/* Price vs News */}
          {note.price_vs_news && (
            <p className="text-xs text-gray-400 italic mb-2">{note.price_vs_news}</p>
          )}

          {/* Catalyst */}
          {note.news_catalyst && (
            <div className="text-xs text-brand-glow mb-1">
              Catalyst: {note.news_catalyst}
            </div>
          )}

          {/* Book reference */}
          <div className="flex items-start gap-1 text-xs text-gray-500">
            <BookOpen className="w-3 h-3 shrink-0 mt-0.5 text-accent-purple" />
            <span className="italic">{note.book_reference}</span>
          </div>
          {note.trader_voice && (
            <div className="text-xs text-accent-purple mt-1">via {note.trader_voice}</div>
          )}
        </div>
      ))}
    </div>
  )
}
