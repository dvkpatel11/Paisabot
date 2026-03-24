/* ═══════════════════════════════════════════════════════════════════════════
   client.ts — Typed fetch wrapper for the Paisabot Flask REST API
   All calls go through Vite proxy → http://localhost:5000/api/*
   ═══════════════════════════════════════════════════════════════════════════ */

const BASE = '/api'

class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`)
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  }
  if (body) opts.body = JSON.stringify(body)

  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new ApiError(res.status, err)
  }
  return res.json() as Promise<T>
}

const get  = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body)
const patch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, body)
const del  = <T>(path: string) => request<T>('DELETE', path)

// ── Typed API namespace ─────────────────────────────────────────────────
import type {
  HealthResponse, ScoresResponse, SignalsResponse, RegimeResponse,
  PortfolioResponse, RiskResponse, BacktestConfig, BacktestResult,
  ConfigResponse, PipelineStatusResponse, Account, ETFUniverse,
  StockUniverse, Trade, Position, SentimentRaw, PerformanceMetric,
  AssetClass,
} from './types'

export const api = {
  // ── Health ──────────────────────────────────────────────────────────
  health: () => get<HealthResponse>('/health'),

  // ── Broker / Account ────────────────────────────────────────────────
  broker: {
    account:  () => get<Record<string, unknown>>('/broker/account'),
    orders:   () => get<unknown[]>('/broker/orders'),
    cancel:   (id: string) => del<{ status: string }>(`/broker/orders/${id}`),
  },

  accounts: {
    list:        (ac?: AssetClass) => get<Account[]>(`/accounts${ac ? `?asset_class=${ac}` : ''}`),
    create:      (data: Partial<Account>) => post<Account>('/accounts', data),
    get:         (ac: AssetClass) => get<Account>(`/accounts/${ac}`),
    update:      (ac: AssetClass, data: Partial<Account>) => patch<Account>(`/accounts/${ac}`, data),
    performance: (ac: AssetClass, days = 90) => get<PerformanceMetric[]>(`/accounts/${ac}/performance?days=${days}`),
    positions:   (ac: AssetClass) => get<Position[]>(`/accounts/${ac}/positions`),
    trades:      (ac: AssetClass, limit = 50) => get<Trade[]>(`/accounts/${ac}/trades?limit=${limit}`),
  },

  // ── Positions ───────────────────────────────────────────────────────
  positions: {
    updateStops: (symbol: string, stops: { stop_price?: number; take_profit_price?: number }) =>
      patch<Position>(`/positions/${symbol}/stops`, stops),
  },

  portfolio: (ac: AssetClass = 'etf') =>
    get<PortfolioResponse>(`/portfolio?asset_class=${ac}`),

  // ── Factor Scores & Signals ─────────────────────────────────────────
  scores: (ac: AssetClass = 'etf', previewWeights?: Record<string, number>) => {
    let url = `/scores?asset_class=${ac}`
    if (previewWeights) url += `&preview_weights=${JSON.stringify(previewWeights)}`
    return get<ScoresResponse>(url)
  },

  signals: (ac: AssetClass = 'etf') =>
    get<SignalsResponse>(`/signals?asset_class=${ac}`),

  regime: () => get<RegimeResponse>('/regime'),

  factors: (symbol: string, days = 30, ac: AssetClass = 'etf') =>
    get<Record<string, number[]>>(`/factors/${symbol}?days=${days}&asset_class=${ac}`),

  // ── Trades ──────────────────────────────────────────────────────────
  trades: {
    list: (opts: { limit?: number; symbol?: string; asset_class?: AssetClass } = {}) => {
      const p = new URLSearchParams()
      if (opts.limit) p.set('limit', String(opts.limit))
      if (opts.symbol) p.set('symbol', opts.symbol)
      if (opts.asset_class) p.set('asset_class', opts.asset_class)
      return get<Trade[]>(`/trades?${p}`)
    },
    manual: (data: { symbol: string; side: string; order_type: string; notional: number; broker: string; limit_price?: number }) =>
      post<Trade>('/trades/manual', data),
  },

  // ── Risk ────────────────────────────────────────────────────────────
  risk: (ac: AssetClass = 'etf') => get<RiskResponse>(`/risk?asset_class=${ac}`),

  // ── Config ──────────────────────────────────────────────────────────
  config: {
    getAll:    () => get<ConfigResponse>('/config'),
    get:       (cat: string) => get<ConfigResponse>(`/config/${cat}`),
    update:    (cat: string, data: Record<string, unknown>) => patch<ConfigResponse>(`/config/${cat}`, data),
    audit:     (limit = 100) => get<unknown[]>(`/config/audit?limit=${limit}`),
  },

  // ── Kill Switches ──────────────────────────────────────────────────
  control: {
    toggle:         (sw: string, active: boolean) => patch<{ status: string }>(`/control/${sw}`, { active }),
    forceLiquidate: (confirm: string) => post<{ status: string }>('/control/force_liquidate', { confirm }),
    forceLiquidateAC: (ac: AssetClass) => post<{ status: string }>(`/control/force_liquidate/${ac}`, { confirm: true }),
  },

  // ── ETF Universe ───────────────────────────────────────────────────
  universe: {
    list:       (activeSet?: boolean) => get<ETFUniverse[]>(`/universe${activeSet ? '?active_set=true' : ''}`),
    create:     (data: Partial<ETFUniverse>) => post<ETFUniverse>('/universe', data),
    update:     (symbol: string, data: Partial<ETFUniverse>) => patch<ETFUniverse>(`/universe/${symbol}`, data),
    toggleActive: (symbol: string, active: boolean, reason?: string) =>
      patch<ETFUniverse>(`/universe/${symbol}/active-set`, { in_active_set: active, reason }),
    remove:     (symbol: string, hard = false) => del<{ status: string }>(`/universe/${symbol}${hard ? '?hard=1' : ''}`),
  },

  // ── Stock Universe ─────────────────────────────────────────────────
  stocks: {
    list:         (opts: { active_only?: boolean; sector?: string } = {}) => {
      const p = new URLSearchParams()
      if (opts.active_only) p.set('active_only', 'true')
      if (opts.sector) p.set('sector', opts.sector)
      return get<StockUniverse[]>(`/stock-universe?${p}`)
    },
    create:       (data: Partial<StockUniverse>) => post<StockUniverse>('/stock-universe', data),
    update:       (symbol: string, data: Partial<StockUniverse>) => patch<StockUniverse>(`/stock-universe/${symbol}`, data),
    toggleActive: (symbol: string, active: boolean, reason?: string) =>
      patch<StockUniverse>(`/stock-universe/${symbol}/active-set`, { active, reason }),
    remove:       (symbol: string, hard = false) => del<{ status: string }>(`/stock-universe/${symbol}${hard ? '?hard=1' : ''}`),
    fundamentals: (symbol: string) => get<Record<string, unknown>>(`/stocks/${symbol}/fundamentals`),
    earnings:     (symbol: string) => get<Record<string, unknown>>(`/stocks/${symbol}/earnings`),
    dividends:    (symbol: string) => get<Record<string, unknown>>(`/stocks/${symbol}/dividends`),
    splits:       (symbol: string) => get<unknown[]>(`/stocks/${symbol}/splits`),
    options:      (symbol: string, expiry?: string) =>
      get<unknown[]>(`/stocks/${symbol}/options${expiry ? `?expiration=${expiry}` : ''}`),
    optionExpiries: (symbol: string) => get<string[]>(`/stocks/${symbol}/options/expirations`),
    refreshFundamentals: (symbols?: string[]) =>
      post<{ status: string }>('/data/refresh-fundamentals', { symbols }),
  },

  // ── Backtest ───────────────────────────────────────────────────────
  backtest: {
    run:     (config: BacktestConfig) => post<BacktestResult>('/backtest/run', config),
    results: (ac: AssetClass = 'etf') => get<BacktestResult>(`/backtest/results?asset_class=${ac}`),
  },

  // ── Research ───────────────────────────────────────────────────────
  research: {
    score:   (data: { symbols: string[]; portfolio_value?: number; regime?: string; custom_weights?: Record<string, number> }) =>
      post<unknown>('/research/score', data),
    latest:  () => get<unknown>('/research/latest'),
    symbols: () => get<string[]>('/research/symbols'),
  },

  // ── Simulation ─────────────────────────────────────────────────────
  simulation: {
    createSession: (capital = 100000) => post<unknown>('/simulation/session', { initial_capital: capital }),
    execute:       (data: { session_id?: string; orders: Array<{ symbol: string; side: string; notional: number; ref_price: number }> }) =>
      post<unknown>('/simulation/execute', data),
    state:         (sessionId?: string) => get<unknown>(`/simulation/state${sessionId ? `?session_id=${sessionId}` : ''}`),
    equity:        (sessionId?: string) => get<unknown>(`/simulation/equity${sessionId ? `?session_id=${sessionId}` : ''}`),
    mark:          (sessionId?: string) => post<unknown>('/simulation/mark', { session_id: sessionId }),
  },

  // ── Data Layer ─────────────────────────────────────────────────────
  data: {
    backfill:     (opts: { symbols?: string[]; days?: number; sync?: boolean } = {}) =>
      post<{ status: string }>('/data/backfill', { days: 756, ...opts }),
    compute:      (sync = false) => post<{ status: string }>('/data/compute', { sync }),
    computeAC:    (ac: AssetClass, sync = false) => post<{ status: string }>(`/data/compute/${ac}`, { sync }),
    status:       () => get<Record<string, unknown>>('/data/status'),
    wsStart:      () => post<{ status: string }>('/data/websocket/start'),
    wsStop:       () => post<{ status: string }>('/data/websocket/stop'),
    wsStatus:     () => get<{ running: boolean }>('/data/websocket/status'),
    recordPerf:   () => post<{ status: string }>('/data/record-performance'),
  },

  // ── Pipeline ───────────────────────────────────────────────────────
  pipeline: {
    run:     (sync = false) => post<{ status: string }>('/pipeline/run', { sync }),
    runAC:   (ac: AssetClass, sync = false) => post<{ status: string }>(`/pipeline/run/${ac}`, { sync }),
    status:  () => get<PipelineStatusResponse>('/pipelines/status'),
  },

  // ── Market Data ────────────────────────────────────────────────────
  market: {
    vix:         () => get<{ level: number; sparkline: number[] }>('/market/vix'),
    sentiment:   (symbol?: string, limit = 100) => {
      const p = new URLSearchParams({ limit: String(limit) })
      if (symbol) p.set('symbol', symbol)
      return get<SentimentRaw[]>(`/sentiment/feed?${p}`)
    },
    correlation: () => get<{ symbols: string[]; matrix: number[][] }>('/rotation/correlation'),
  },

  // ── Services ───────────────────────────────────────────────────────
  services: {
    list:    () => get<Record<string, unknown>>('/services'),
    tooltip: (key: string) => get<{ tooltip: string }>(`/services/${key}/tooltip`),
  },

  // ── Auth ────────────────────────────────────────────────────────────
  auth: {
    login:  (username: string, password: string) =>
      request<{ status: string; redirect?: string }>('POST', '/../login', { username, password }),
    logout: () => get<{ status: string }>('/../logout'),
  },
}

export { ApiError }
