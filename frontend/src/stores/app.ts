/* ═══════════════════════════════════════════════════════════════════════════
   app.ts — Global Zustand store for real-time application state
   Fed by WebSocket events + initial API hydration
   ═══════════════════════════════════════════════════════════════════════════ */

import { create } from 'zustand'
import type {
  Regime, OperationalMode, HealthStatus, Position,
  WsRiskAlert, SignalType,
} from '@/api/types'

interface AppState {
  // Connection
  wsConnected: boolean
  wsLatency: number | null
  lastTick: string | null

  // Regime
  regime: Regime
  regimeConfidence: number

  // Mode
  mode: OperationalMode

  // Kill switches
  killSwitches: Record<string, boolean>

  // System health per module
  health: Record<string, HealthStatus>

  // Live portfolio summary
  portfolioValue: number | null
  totalPnl: number | null
  positions: Position[]

  // Alerts
  alerts: WsRiskAlert[]
  alertsPanelOpen: boolean

  // Signals summary
  signalCounts: { long: number; neutral: number; avoid: number }

  // Actions
  setWsConnected: (v: boolean) => void
  setWsLatency: (v: number) => void
  setLastTick: (v: string) => void
  setRegime: (regime: Regime, confidence: number) => void
  setMode: (mode: OperationalMode) => void
  setKillSwitch: (key: string, active: boolean) => void
  setHealth: (module: string, status: HealthStatus) => void
  setPortfolio: (value: number, pnl: number, positions: Position[]) => void
  addAlert: (alert: WsRiskAlert) => void
  toggleAlertsPanel: () => void
  setSignalCounts: (counts: Record<SignalType, number>) => void
}

export const useAppStore = create<AppState>((set) => ({
  wsConnected: false,
  wsLatency: null,
  lastTick: null,
  regime: 'consolidation',
  regimeConfidence: 0,
  mode: 'research',
  killSwitches: {},
  health: {},
  portfolioValue: null,
  totalPnl: null,
  positions: [],
  alerts: [],
  alertsPanelOpen: false,
  signalCounts: { long: 0, neutral: 0, avoid: 0 },

  setWsConnected: (v) => set({ wsConnected: v }),
  setWsLatency: (v) => set({ wsLatency: v }),
  setLastTick: (v) => set({ lastTick: v }),
  setRegime: (regime, confidence) => set({ regime, regimeConfidence: confidence }),
  setMode: (mode) => set({ mode }),
  setKillSwitch: (key, active) => set((s) => ({ killSwitches: { ...s.killSwitches, [key]: active } })),
  setHealth: (module, status) => set((s) => ({ health: { ...s.health, [module]: status } })),
  setPortfolio: (value, pnl, positions) => set({ portfolioValue: value, totalPnl: pnl, positions }),
  addAlert: (alert) => set((s) => ({ alerts: [alert, ...s.alerts].slice(0, 100) })),
  toggleAlertsPanel: () => set((s) => ({ alertsPanelOpen: !s.alertsPanelOpen })),
  setSignalCounts: (counts) => set({ signalCounts: { long: counts.long ?? 0, neutral: counts.neutral ?? 0, avoid: counts.avoid ?? 0 } }),
}))
