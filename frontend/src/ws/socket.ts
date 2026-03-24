/* ═══════════════════════════════════════════════════════════════════════════
   socket.ts — Socket.IO client for real-time events from Flask-SocketIO
   Namespace: /dashboard — maps all 12 server→client events
   ═══════════════════════════════════════════════════════════════════════════ */

import { io, Socket } from 'socket.io-client'
import type {
  WsPriceUpdate, WsFactorScores, WsSignal, WsPortfolio,
  WsRiskAlert, WsTrade, WsRegimeChange, WsSystemHealth,
  WsConfigChange, WsKillSwitch,
} from '@/api/types'

// ── Event name → payload type map ───────────────────────────────────────
export interface ServerEvents {
  connected:      { status: string }
  price_update:   WsPriceUpdate
  factor_scores:  WsFactorScores
  signals:        WsSignal
  portfolio:      WsPortfolio
  risk_alert:     WsRiskAlert
  trade:          WsTrade
  regime_change:  WsRegimeChange
  system_health:  WsSystemHealth
  config_change:  WsConfigChange
  kill_switch:    WsKillSwitch
  pong:           { status: string }
  subscribed:     { channels: string[] }
}

export interface ClientEvents {
  ping:      Record<string, never>
  subscribe: { channels: string[] }
}

type TypedSocket = Socket<ServerEvents, ClientEvents>

let socket: TypedSocket | null = null

export function getSocket(): TypedSocket {
  if (!socket) {
    socket = io('/dashboard', {
      transports: ['websocket'],
      reconnectionAttempts: 10,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 16000,
    }) as TypedSocket
  }
  return socket
}

export function disconnectSocket(): void {
  if (socket) {
    socket.disconnect()
    socket = null
  }
}
