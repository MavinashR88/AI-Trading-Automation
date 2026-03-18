import React, { useEffect, useState, useCallback } from 'react'
import axios from 'axios'
import { Newspaper, RefreshCw, Loader2, TrendingUp, TrendingDown } from 'lucide-react'
import NewsPanel from '../components/NewsPanel'

export default function NewsRoom() {
  const [ticker, setTicker] = useState('AAPL')
  const [news, setNews] = useState<unknown[]>([])
  const [graphNews, setGraphNews] = useState<unknown[]>([])
  const [loading, setLoading] = useState(false)
  const [watchList] = useState(['AAPL', 'MSFT', 'NVDA', 'SPY', 'QQQ'])
  const [sentimentMap, setSentimentMap] = useState<Record<string, number>>({})

  const loadNews = useCallback(async (t: string) => {
    setLoading(true)
    try {
      const resp = await axios.get(`/api/news/${t}`)
      setNews(resp.data.news || [])
      setGraphNews(resp.data.graph_news || [])

      // Update sentiment map
      const allNews = resp.data.news || []
      if (allNews.length > 0) {
        const avgSentiment = allNews.reduce((sum: number, n: Record<string, number>) =>
          sum + (n.sentiment_score || 0), 0) / allNews.length
        setSentimentMap(prev => ({ ...prev, [t]: avgSentiment }))
      }
    } catch (e) {
      console.error('News load failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadNews(ticker)
  }, [ticker, loadNews])

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">News Room</h1>
          <p className="text-sm text-gray-400 mt-0.5">Hourly news scans with AI sentiment scoring and divergence detection</p>
        </div>
        <button className="btn-ghost btn" onClick={() => loadNews(ticker)} disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Ticker sentiment overview */}
      <div className="card">
        <h3 className="text-sm font-semibold text-gray-300 mb-3">Sentiment Overview</h3>
        <div className="flex flex-wrap gap-3">
          {watchList.map(t => {
            const s = sentimentMap[t]
            return (
              <button
                key={t}
                onClick={() => setTicker(t)}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-colors text-sm ${
                  ticker === t
                    ? 'border-brand bg-brand/10 text-white'
                    : 'border-surface-3 bg-surface-2 text-gray-300 hover:border-brand/50'
                }`}
              >
                <span className="font-mono font-semibold">{t}</span>
                {s !== undefined ? (
                  <span className={`text-xs mono ${s > 0.1 ? 'text-accent-green' : s < -0.1 ? 'text-accent-red' : 'text-accent-yellow'}`}>
                    {s >= 0 ? '+' : ''}{s.toFixed(2)}
                  </span>
                ) : (
                  <span className="text-xs text-gray-500">--</span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Custom ticker search */}
      <div className="flex gap-2">
        <input
          className="bg-surface-1 border border-surface-3 rounded-lg px-3 py-2 text-white mono text-sm focus:outline-none focus:border-brand uppercase w-32"
          value={ticker}
          onChange={e => setTicker(e.target.value.toUpperCase())}
          placeholder="AAPL"
          onKeyDown={e => e.key === 'Enter' && loadNews(ticker)}
        />
        <button className="btn-primary btn" onClick={() => loadNews(ticker)} disabled={loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Newspaper className="w-4 h-4" />}
          Scan News
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-brand animate-spin" />
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {/* SQLite news */}
          <div>
            <NewsPanel
              news={news as Parameters<typeof NewsPanel>[0]['news']}
              title={`${ticker} — Recent News (SQLite)`}
            />
          </div>

          {/* Graph news */}
          <div>
            <h3 className="text-sm font-semibold text-gray-300 mb-3">{ticker} — Graph News (Last 24h)</h3>
            {graphNews.length === 0 ? (
              <div className="card text-center py-6 text-sm text-gray-400">
                No graph news. News events are added to the graph automatically every hour.
              </div>
            ) : (
              <div className="space-y-3">
                {(graphNews as Array<{
                  headline: string
                  sentiment_score: number
                  urgency: string
                  catalyst: string
                  source: string
                  timestamp: string
                }>).map((item, i) => (
                  <div key={i} className="card space-y-1.5">
                    <div className="flex items-center gap-2">
                      {item.sentiment_score > 0.1
                        ? <TrendingUp className="w-4 h-4 text-accent-green" />
                        : item.sentiment_score < -0.1
                        ? <TrendingDown className="w-4 h-4 text-accent-red" />
                        : <Newspaper className="w-4 h-4 text-accent-yellow" />
                      }
                      <span className="text-xs text-gray-400">{item.source}</span>
                      <span className={`mono text-xs font-semibold ${
                        item.sentiment_score > 0 ? 'text-accent-green' : 'text-accent-red'
                      }`}>
                        {item.sentiment_score >= 0 ? '+' : ''}{item.sentiment_score.toFixed(2)}
                      </span>
                    </div>
                    <p className="text-sm text-gray-200">{item.headline}</p>
                    {item.catalyst && <p className="text-xs text-brand-glow">{item.catalyst}</p>}
                    <p className="text-xs text-gray-500">
                      {new Date(item.timestamp).toLocaleString()}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
