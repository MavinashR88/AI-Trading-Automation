import { useState, useEffect, useRef, useCallback } from 'react'

export interface WSEvent {
  type: string
  data: unknown
  timestamp: string
}

export function useWebSocket(url: string) {
  const [connected, setConnected] = useState(false)
  const [lastEvent, setLastEvent] = useState<WSEvent | null>(null)
  const [eventHistory, setEventHistory] = useState<WSEvent[]>([])
  const ws = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectDelay = useRef(1000)

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    try {
      ws.current = new WebSocket(url)

      ws.current.onopen = () => {
        setConnected(true)
        reconnectDelay.current = 1000
        console.log('[WS] Connected')
      }

      ws.current.onmessage = (evt) => {
        try {
          const parsed: WSEvent = JSON.parse(evt.data)
          if (parsed.type === 'heartbeat' || parsed.type === 'pong') return
          setLastEvent(parsed)
          setEventHistory(prev => [parsed, ...prev].slice(0, 200))
        } catch (e) {
          console.warn('[WS] Could not parse message:', evt.data)
        }
      }

      ws.current.onclose = () => {
        setConnected(false)
        console.log('[WS] Disconnected. Reconnecting in', reconnectDelay.current, 'ms')
        reconnectTimer.current = setTimeout(() => {
          reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30_000)
          connect()
        }, reconnectDelay.current)
      }

      ws.current.onerror = (err) => {
        console.error('[WS] Error:', err)
        ws.current?.close()
      }
    } catch (e) {
      console.error('[WS] Connect error:', e)
    }
  }, [url])

  useEffect(() => {
    connect()

    // Heartbeat ping every 25s
    const pingInterval = setInterval(() => {
      if (ws.current?.readyState === WebSocket.OPEN) {
        ws.current.send('ping')
      }
    }, 25_000)

    return () => {
      clearInterval(pingInterval)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      ws.current?.close()
    }
  }, [connect])

  const filterEvents = useCallback(
    (type: string) => eventHistory.filter(e => e.type === type),
    [eventHistory]
  )

  return { connected, lastEvent, eventHistory, filterEvents }
}
