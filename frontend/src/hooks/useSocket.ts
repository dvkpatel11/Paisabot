/* ═══════════════════════════════════════════════════════════════════════════
   useSocket.ts — React hook that connects Socket.IO to the Zustand store
   Mount once at the app root; all events auto-update global state
   ═══════════════════════════════════════════════════════════════════════════ */

import { useEffect, useRef } from 'react'
import { getSocket } from '@/ws/socket'
import { useAppStore } from '@/stores/app'

export function useSocketBridge() {
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const store = useAppStore

  useEffect(() => {
    const socket = getSocket()

    socket.on('connect', () => {
      store.getState().setWsConnected(true)
    })

    socket.on('disconnect', () => {
      store.getState().setWsConnected(false)
    })

    // Latency ping every 10s
    pingRef.current = setInterval(() => {
      if (socket.connected) {
        const start = performance.now()
        socket.emit('ping')
        socket.once('pong', () => {
          store.getState().setWsLatency(Math.round(performance.now() - start))
        })
      }
    }, 10000)

    // ── Server events → store ───────────────────────────────────────
    socket.on('price_update', (data) => {
      store.getState().setLastTick(`${data.symbol} ${data.close}`)
    })

    socket.on('regime_change', (data) => {
      store.getState().setRegime(data.regime, data.confidence)
    })

    socket.on('portfolio', (data) => {
      store.getState().setPortfolio(data.nav, data.pnl, data.positions)
    })

    socket.on('risk_alert', (alert) => {
      store.getState().addAlert(alert)
    })

    socket.on('system_health', (data) => {
      store.getState().setHealth(data.module, data.status)
    })

    socket.on('config_change', (data) => {
      if (data.category === 'system' && data.key === 'operational_mode') {
        store.getState().setMode(data.value as 'research' | 'simulation' | 'live')
      }
    })

    socket.on('kill_switch', (data) => {
      store.getState().setKillSwitch(data.switch, data.active)
    })

    return () => {
      if (pingRef.current) clearInterval(pingRef.current)
      socket.removeAllListeners()
    }
  }, [])
}
