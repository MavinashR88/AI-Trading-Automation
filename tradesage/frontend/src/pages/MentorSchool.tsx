import React, { useEffect, useState } from 'react'
import axios from 'axios'
import {
  BookOpen, Upload, Loader2, Network, RefreshCw,
  AlertTriangle, CheckCircle2, Clock, TrendingUp, TrendingDown, Zap
} from 'lucide-react'
import MentorFeed from '../components/MentorFeed'
import ReviewPanel from '../components/ReviewPanel'
import GraphExplorer from '../components/GraphExplorer'

interface BookSuggestion {
  id: number
  book_title: string
  book_author: string
  knowledge_gap: string
  gap_category: string
  relevant_concept: string
  urgency: 'high' | 'medium' | 'low'
  status: 'SUGGESTED' | 'UPLOADED'
  created_at: string
}

interface WeeklyReport {
  week_ending: string
  total_trades: number
  wins: number
  losses: number
  win_rate: number
  total_pnl: number
  grade: string
  rule_improvements: string[]
  winning_patterns: string[]
  losing_patterns: string[]
  key_insight: string
  most_used_trader: string
  neglected_trader: string
  created_at: string
}

type Tab = 'books' | 'graph' | 'reviews' | 'reports'

export default function MentorSchool() {
  const [lessons, setLessons] = useState<unknown[]>([])
  const [reviews, setReviews] = useState<unknown[]>([])
  const [graphStats, setGraphStats] = useState<{ nodes: number; relationships: number }>({ nodes: 0, relationships: 0 })
  const [readingList, setReadingList] = useState<BookSuggestion[]>([])
  const [weeklyReports, setWeeklyReports] = useState<WeeklyReport[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploadingBook, setUploadingBook] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [explorerTicker, setExplorerTicker] = useState('AAPL')
  const [tab, setTab] = useState<Tab>('books')
  const [runningAnalysis, setRunningAnalysis] = useState(false)

  const load = async () => {
    try {
      const [lessonsResp, reviewsResp, statsResp, booksResp, reportsResp] = await Promise.all([
        axios.get('/api/lessons?limit=20'),
        axios.get('/api/reviews?limit=20'),
        axios.get('/api/graph/stats'),
        axios.get('/api/mentor/reading-list'),
        axios.get('/api/mentor/weekly-reports?limit=4'),
      ])
      setLessons(lessonsResp.data.lessons || [])
      setReviews(reviewsResp.data.reviews || [])
      setGraphStats(statsResp.data)
      setReadingList(booksResp.data.books || [])
      setWeeklyReports(reportsResp.data.reports || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>, bookTitle?: string) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    if (bookTitle) setUploadingBook(bookTitle)
    setUploadMsg('')
    const form = new FormData()
    form.append('file', file)
    try {
      const resp = await axios.post('/api/upload-book', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setUploadMsg(`✓ ${resp.data.filename} ingested (${resp.data.chunks_ingested} chunks)`)
      // If uploading a specific suggested book, mark it as learned
      if (bookTitle && resp.data.chunks_ingested > 5) {
        await axios.post(`/api/mentor/book-learned/${encodeURIComponent(bookTitle)}`)
        setReadingList(prev => prev.filter(b => b.book_title !== bookTitle))
      }
    } catch {
      setUploadMsg('Upload failed — PDF only.')
    } finally {
      setUploading(false)
      setUploadingBook(null)
    }
  }

  const handleRunAnalysis = async () => {
    setRunningAnalysis(true)
    try {
      await axios.post('/api/mentor/run-weekly-analysis')
      setTimeout(() => {
        load()
        setRunningAnalysis(false)
      }, 5000)
    } catch {
      setRunningAnalysis(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-8 h-8 text-brand animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-3 bg-surface-1 shrink-0">
        <div>
          <h1 className="text-lg font-bold text-white">Mentor School</h1>
          <p className="text-xs text-gray-500 mt-0.5">Composite wisdom of the world's greatest traders</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-center">
            <div className="text-sm font-bold mono text-brand-glow">{graphStats.nodes.toLocaleString()}</div>
            <div className="text-[10px] text-gray-500">Nodes</div>
          </div>
          <div className="w-px h-6 bg-surface-3" />
          <div className="text-center">
            <div className="text-sm font-bold mono text-accent-purple">{graphStats.relationships.toLocaleString()}</div>
            <div className="text-[10px] text-gray-500">Relations</div>
          </div>
          <div className="w-px h-6 bg-surface-3" />
          <button onClick={load} className="p-1.5 rounded-lg text-gray-500 hover:text-gray-300 hover:bg-surface-3 transition-colors">
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Trader composites */}
      <div className="px-6 py-3 border-b border-surface-3 shrink-0">
        <div className="flex gap-2 overflow-x-auto pb-1">
          {TRADERS.map(t => (
            <div key={t.name} className="shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-2 rounded-lg border border-surface-3">
              <span className="text-sm">{t.emoji}</span>
              <div>
                <div className="text-xs font-semibold text-white whitespace-nowrap">{t.name}</div>
                <div className="text-[10px] text-gray-500">{t.style}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-surface-3 shrink-0 bg-surface-1">
        <TabBtn active={tab === 'books'} onClick={() => setTab('books')} label="Reading List" badge={readingList.length || undefined} />
        <TabBtn active={tab === 'graph'} onClick={() => setTab('graph')} label="Knowledge Graph" />
        <TabBtn active={tab === 'reviews'} onClick={() => setTab('reviews')} label="Reviews & Lessons" badge={(reviews.length > 0 || lessons.length > 0) ? undefined : undefined} />
        <TabBtn active={tab === 'reports'} onClick={() => setTab('reports')} label="Weekly Reports" badge={weeklyReports.length || undefined} />
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'books' && (
          <ReadingListTab
            readingList={readingList}
            uploading={uploading}
            uploadingBook={uploadingBook}
            uploadMsg={uploadMsg}
            onUpload={handleUpload}
          />
        )}
        {tab === 'graph' && (
          <div className="p-6">
            <div className="flex items-center gap-3 mb-4">
              <input
                className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-1.5 text-white mono text-sm focus:outline-none focus:border-brand uppercase placeholder-gray-600 w-28"
                value={explorerTicker}
                onChange={e => setExplorerTicker(e.target.value.toUpperCase())}
                placeholder="Ticker"
              />
              <span className="text-xs text-gray-500">Enter a ticker to explore its knowledge graph context</span>
            </div>
            <GraphExplorer ticker={explorerTicker} />
          </div>
        )}
        {tab === 'reviews' && (
          <div className="p-6 grid grid-cols-1 xl:grid-cols-2 gap-6">
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3">Pre-Trade Review Notes</h3>
              <ReviewPanel reviews={reviews as Parameters<typeof ReviewPanel>[0]['reviews']} />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-gray-300 mb-3">Post-Trade Lessons</h3>
              <MentorFeed lessons={lessons as Parameters<typeof MentorFeed>[0]['lessons']} />
            </div>
          </div>
        )}
        {tab === 'reports' && (
          <WeeklyReportsTab reports={weeklyReports} running={runningAnalysis} onRun={handleRunAnalysis} />
        )}
      </div>
    </div>
  )
}

// ── Tab button ────────────────────────────────────────────────────

function TabBtn({ active, onClick, label, badge }: {
  active: boolean; onClick: () => void; label: string; badge?: number
}) {
  return (
    <button
      onClick={onClick}
      className={`relative px-5 py-3 text-xs font-medium transition-colors ${
        active ? 'text-white border-b-2 border-brand' : 'text-gray-500 hover:text-gray-300'
      }`}
    >
      {label}
      {badge !== undefined && badge > 0 && (
        <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-brand/20 text-brand-glow text-[10px] font-bold">
          {badge}
        </span>
      )}
    </button>
  )
}

// ── Reading List Tab ──────────────────────────────────────────────

function ReadingListTab({ readingList, uploading, uploadingBook, uploadMsg, onUpload }: {
  readingList: BookSuggestion[]
  uploading: boolean
  uploadingBook: string | null
  uploadMsg: string
  onUpload: (e: React.ChangeEvent<HTMLInputElement>, bookTitle?: string) => void
}) {
  const urgencyColor = (u: string) =>
    u === 'high' ? 'text-accent-red bg-accent-red/10 border-accent-red/30' :
    u === 'medium' ? 'text-accent-yellow bg-accent-yellow/10 border-accent-yellow/30' :
    'text-gray-400 bg-surface-3 border-surface-3'

  const statusIcon = (s: string) =>
    s === 'UPLOADED' ? <CheckCircle2 className="w-3.5 h-3.5 text-accent-green" /> :
    <Clock className="w-3.5 h-3.5 text-accent-yellow" />

  return (
    <div className="p-6 space-y-6">
      {/* Upload any PDF */}
      <div className="bg-surface-2 rounded-xl border border-surface-3 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Upload className="w-4 h-4 text-brand-glow" />
          <h3 className="text-sm font-semibold text-gray-300">Upload Any Trading Book (PDF)</h3>
        </div>
        <p className="text-xs text-gray-500 mb-3">
          The mentor will study and incorporate new books into trade reviews automatically.
        </p>
        <div className="flex items-center gap-3">
          <label className="btn btn-primary cursor-pointer">
            {uploading && !uploadingBook ? <Loader2 className="w-4 h-4 animate-spin" /> : <BookOpen className="w-4 h-4" />}
            {uploading && !uploadingBook ? 'Uploading…' : 'Choose PDF'}
            <input type="file" accept=".pdf" className="hidden" onChange={e => onUpload(e)} disabled={uploading} />
          </label>
          {uploadMsg && (
            <span className={`text-xs ${uploadMsg.includes('failed') ? 'text-accent-red' : 'text-accent-green'}`}>
              {uploadMsg}
            </span>
          )}
        </div>
      </div>

      {/* Reading list */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <BookOpen className="w-4 h-4 text-accent-yellow" />
          <h3 className="text-sm font-semibold text-gray-300">Mentor Reading List</h3>
          <span className="text-xs text-gray-600">— suggested by AI after your losses</span>
        </div>

        {readingList.length === 0 ? (
          <div className="text-center py-12 text-gray-600">
            <BookOpen className="w-10 h-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm">No books suggested yet.</p>
            <p className="text-xs mt-1">After losses, the mentor will suggest books to fill knowledge gaps.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {readingList.map(book => (
              <div
                key={book.id}
                className="bg-surface-2 rounded-xl border border-surface-3 p-4 fade-in"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      {statusIcon(book.status)}
                      <span className="font-bold text-white text-sm">{book.book_title}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold uppercase ${urgencyColor(book.urgency)}`}>
                        {book.urgency}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 mb-2">by {book.book_author}</div>
                    <div className="space-y-1">
                      <div className="flex gap-1.5 text-xs">
                        <span className="text-gray-600 shrink-0">Gap:</span>
                        <span className="text-gray-300">{book.knowledge_gap}</span>
                      </div>
                      {book.relevant_concept && (
                        <div className="flex gap-1.5 text-xs">
                          <span className="text-gray-600 shrink-0">Focus:</span>
                          <span className="text-accent-yellow/80">{book.relevant_concept}</span>
                        </div>
                      )}
                      {book.gap_category && (
                        <span className="inline-block text-[10px] px-2 py-0.5 rounded-full bg-surface-3 text-gray-500 mt-1 capitalize">
                          {book.gap_category.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="shrink-0">
                    {book.status === 'SUGGESTED' ? (
                      <label className={`btn btn-sm cursor-pointer ${uploading && uploadingBook === book.book_title ? 'opacity-40' : ''}`}>
                        {uploading && uploadingBook === book.book_title
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Upload className="w-3.5 h-3.5" />
                        }
                        Upload PDF
                        <input
                          type="file"
                          accept=".pdf"
                          className="hidden"
                          onChange={e => onUpload(e, book.book_title)}
                          disabled={uploading}
                        />
                      </label>
                    ) : (
                      <div className="flex items-center gap-1.5 text-accent-green text-xs font-medium">
                        <CheckCircle2 className="w-4 h-4" />
                        Uploaded
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Weekly Reports Tab ────────────────────────────────────────────

function WeeklyReportsTab({ reports, running, onRun }: {
  reports: WeeklyReport[]
  running: boolean
  onRun: () => void
}) {
  const gradeColor = (g: string) => {
    if (g === 'A+') return 'text-accent-purple'
    if (g.startsWith('A')) return 'text-accent-green'
    if (g.startsWith('B')) return 'text-brand-glow'
    if (g.startsWith('C')) return 'text-accent-yellow'
    if (g.startsWith('D')) return 'text-orange-400'
    return 'text-accent-red'
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-300">Weekly Self-Analysis Reports</h3>
          <p className="text-xs text-gray-600 mt-0.5">AI grading of trading performance, patterns, and rule improvements</p>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className="btn btn-ghost btn-sm"
        >
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
          {running ? 'Analysing…' : 'Run Now'}
        </button>
      </div>

      {reports.length === 0 ? (
        <div className="text-center py-12 text-gray-600">
          <AlertTriangle className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">No weekly reports yet.</p>
          <p className="text-xs mt-1">Reports run automatically on Sundays, or click "Run Now" above.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {reports.map(report => (
            <div key={report.week_ending} className="bg-surface-2 rounded-xl border border-surface-3 p-5">
              {/* Header row */}
              <div className="flex items-center justify-between mb-4">
                <div>
                  <div className="text-xs text-gray-500 mb-0.5">Week ending</div>
                  <div className="text-sm font-bold text-white">{report.week_ending}</div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-center">
                    <div className="text-[10px] text-gray-500">Trades</div>
                    <div className="text-sm font-bold mono text-white">{report.total_trades}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] text-gray-500">Win Rate</div>
                    <div className={`text-sm font-bold mono ${report.win_rate >= 0.5 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {(report.win_rate * 100).toFixed(0)}%
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] text-gray-500">P&L</div>
                    <div className={`text-sm font-bold mono ${report.total_pnl >= 0 ? 'text-accent-green' : 'text-accent-red'}`}>
                      {report.total_pnl >= 0 ? '+' : ''}${report.total_pnl.toFixed(0)}
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-[10px] text-gray-500">Grade</div>
                    <div className={`text-2xl font-black ${gradeColor(report.grade)}`}>{report.grade}</div>
                  </div>
                </div>
              </div>

              {/* Key insight */}
              {report.key_insight && (
                <div className="bg-surface-3 rounded-lg p-3 mb-3 text-xs text-gray-300 leading-relaxed border-l-2 border-brand">
                  {report.key_insight}
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
                {/* Winning patterns */}
                {report.winning_patterns?.length > 0 && (
                  <div>
                    <div className="flex items-center gap-1.5 text-accent-green mb-1.5 font-semibold">
                      <TrendingUp className="w-3.5 h-3.5" /> Winning Patterns
                    </div>
                    <ul className="space-y-1">
                      {report.winning_patterns.map((p, i) => (
                        <li key={i} className="text-gray-400 flex gap-1.5">
                          <span className="text-accent-green/60 shrink-0">•</span>{p}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Losing patterns */}
                {report.losing_patterns?.length > 0 && (
                  <div>
                    <div className="flex items-center gap-1.5 text-accent-red mb-1.5 font-semibold">
                      <TrendingDown className="w-3.5 h-3.5" /> Losing Patterns
                    </div>
                    <ul className="space-y-1">
                      {report.losing_patterns.map((p, i) => (
                        <li key={i} className="text-gray-400 flex gap-1.5">
                          <span className="text-accent-red/60 shrink-0">•</span>{p}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Rule improvements */}
                {report.rule_improvements?.length > 0 && (
                  <div>
                    <div className="flex items-center gap-1.5 text-brand-glow mb-1.5 font-semibold">
                      <Zap className="w-3.5 h-3.5" /> Rule Improvements
                    </div>
                    <ul className="space-y-1">
                      {report.rule_improvements.map((r, i) => (
                        <li key={i} className="text-gray-400 flex gap-1.5">
                          <span className="text-brand-glow/60 shrink-0">→</span>{r}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* Traders */}
              {(report.most_used_trader || report.neglected_trader) && (
                <div className="flex gap-4 mt-3 pt-3 border-t border-surface-3 text-xs">
                  {report.most_used_trader && (
                    <span className="text-gray-500">Most relied on: <span className="text-gray-300 font-medium">{report.most_used_trader}</span></span>
                  )}
                  {report.neglected_trader && (
                    <span className="text-gray-500">Neglected: <span className="text-accent-yellow font-medium">{report.neglected_trader}</span></span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const TRADERS = [
  { name: 'W. Buffett', style: 'Value', emoji: '🏛️' },
  { name: 'G. Soros', style: 'Macro', emoji: '🌍' },
  { name: 'P.T. Jones', style: 'Risk First', emoji: '🛡️' },
  { name: 'Ray Dalio', style: 'All-Weather', emoji: '⚖️' },
  { name: 'P. Lynch', style: 'Know It', emoji: '🔍' },
  { name: 'J. Simons', style: 'Quant', emoji: '📊' },
  { name: 'Druckenmiller', style: 'Conviction', emoji: '🎯' },
  { name: 'J. Livermore', style: 'Trend', emoji: '📈' },
  { name: 'C. Icahn', style: 'Contrarian', emoji: '⚔️' },
  { name: 'H. Marks', style: 'Cycles', emoji: '🔄' },
]
