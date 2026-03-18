import React, { useState } from 'react'
import { AlertTriangle, CheckCircle, Zap } from 'lucide-react'
import axios from 'axios'

interface ModeToggleProps {
  currentMode: string
  onModeChange?: (mode: string) => void
}

export default function ModeToggle({ currentMode, onModeChange }: ModeToggleProps) {
  const [showConfirm, setShowConfirm] = useState(false)
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const isPaper = currentMode === 'paper'

  const handleToggle = async () => {
    setError('')
    setSuccess('')
    setLoading(true)

    try {
      const confirmKey = isPaper ? 'SWITCH_TO_LIVE' : 'SWITCH_TO_PAPER'
      const resp = await axios.post('/api/toggle-mode', {
        confirm: confirmKey,
        reason,
      })
      setSuccess(resp.data.message)
      setShowConfirm(false)
      setReason('')
      onModeChange?.(resp.data.mode)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to switch mode'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-accent-yellow" />
          <span className="text-sm font-semibold text-gray-300">Trading Mode</span>
        </div>
        <div className={`badge ${isPaper ? 'badge-blue' : 'badge-red'}`}>
          {isPaper ? 'PAPER' : 'LIVE'}
        </div>
      </div>

      {success && (
        <div className="flex items-center gap-2 p-2 rounded-lg bg-accent-green/10 text-accent-green text-sm mb-3">
          <CheckCircle className="w-4 h-4 shrink-0" />
          {success}
        </div>
      )}

      {!showConfirm ? (
        <button
          className={`w-full btn ${isPaper ? 'btn-danger' : 'btn-primary'}`}
          onClick={() => setShowConfirm(true)}
        >
          {isPaper ? 'Switch to LIVE' : 'Switch to PAPER'}
        </button>
      ) : (
        <div className="space-y-3">
          {isPaper && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-accent-red/10 border border-accent-red/30">
              <AlertTriangle className="w-4 h-4 text-accent-red shrink-0 mt-0.5" />
              <p className="text-xs text-accent-red">
                Switching to LIVE mode will use REAL MONEY. All trades will be executed against real markets.
              </p>
            </div>
          )}
          <textarea
            className="w-full bg-surface-2 border border-surface-3 rounded-lg p-2 text-sm text-gray-200 placeholder-gray-500 resize-none focus:outline-none focus:border-brand"
            rows={2}
            placeholder="Reason for switching (required)..."
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
          {error && <p className="text-xs text-accent-red">{error}</p>}
          <div className="flex gap-2">
            <button
              className="flex-1 btn-ghost btn text-sm"
              onClick={() => { setShowConfirm(false); setError(''); setReason('') }}
            >
              Cancel
            </button>
            <button
              className={`flex-1 btn ${isPaper ? 'btn-danger' : 'btn-primary'} text-sm`}
              onClick={handleToggle}
              disabled={loading || reason.length < 10}
            >
              {loading ? 'Switching...' : 'Confirm'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
