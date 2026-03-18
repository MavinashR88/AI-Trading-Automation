import React from 'react'
import { BookOpen, TrendingUp, TrendingDown, Star } from 'lucide-react'

interface Lesson {
  lesson_id: string
  trade_id: string
  ticker: string
  outcome: string
  trader_principle: string
  principle_quote: string
  what_happened: string
  correction: string
  confidence_adjustment: number
  consecutive_wins: number
  win_rate: number
  pnl_pct: number
  book_reference: string
  timestamp: string
}

interface MentorFeedProps {
  lessons: Lesson[]
}

export default function MentorFeed({ lessons }: MentorFeedProps) {
  if (!lessons.length) {
    return (
      <div className="card text-center py-8">
        <BookOpen className="w-8 h-8 text-gray-500 mx-auto mb-2" />
        <p className="text-sm text-gray-400">No mentor lessons yet. Lessons appear after each trade closes.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {lessons.map(lesson => (
        <div key={lesson.lesson_id} className="card space-y-2">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {lesson.outcome === 'WIN'
                ? <TrendingUp className="w-4 h-4 text-accent-green" />
                : <TrendingDown className="w-4 h-4 text-accent-red" />
              }
              <span className="font-semibold text-white text-sm">{lesson.ticker}</span>
              <span className={`badge ${lesson.outcome === 'WIN' ? 'badge-green' : 'badge-red'}`}>
                {lesson.outcome}
              </span>
              <span className={`mono text-sm font-semibold ${
                lesson.pnl_pct >= 0 ? 'text-accent-green' : 'text-accent-red'
              }`}>
                {lesson.pnl_pct >= 0 ? '+' : ''}{lesson.pnl_pct.toFixed(2)}%
              </span>
            </div>
            <span className="text-xs text-gray-500">
              {new Date(lesson.timestamp).toLocaleTimeString()}
            </span>
          </div>

          {/* Trader */}
          <div className="text-xs text-accent-purple font-semibold flex items-center gap-1">
            <Star className="w-3 h-3" />
            {lesson.trader_principle}
          </div>

          {/* Quote */}
          {lesson.principle_quote && (
            <blockquote className="border-l-2 border-accent-purple/50 pl-3 text-xs text-gray-300 italic">
              "{lesson.principle_quote}"
            </blockquote>
          )}

          {/* What happened */}
          <p className="text-xs text-gray-300">{lesson.what_happened}</p>

          {/* Correction */}
          <div className="card-dark text-xs">
            <span className="text-accent-yellow font-semibold">Next time: </span>
            <span className="text-gray-300">{lesson.correction}</span>
          </div>

          {/* Stats row */}
          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>Win rate: <span className="text-white mono">{(lesson.win_rate * 100).toFixed(1)}%</span></span>
            <span>Streak: <span className="text-accent-yellow mono">{lesson.consecutive_wins}</span></span>
            {lesson.confidence_adjustment !== 0 && (
              <span>
                Confidence adj:{' '}
                <span className={lesson.confidence_adjustment > 0 ? 'text-accent-green' : 'text-accent-red'}>
                  {lesson.confidence_adjustment > 0 ? '+' : ''}{(lesson.confidence_adjustment * 100).toFixed(0)}%
                </span>
              </span>
            )}
          </div>

          {/* Book ref */}
          {lesson.book_reference && (
            <div className="flex items-start gap-1 text-xs text-gray-500">
              <BookOpen className="w-3 h-3 shrink-0 mt-0.5 text-accent-purple" />
              <span className="italic">{lesson.book_reference}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
