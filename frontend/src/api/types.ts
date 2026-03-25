/* ═══════════════════════════════════════════════════════════════════════════
   types.ts — TypeScript types mirroring all 13 Paisabot backend data models
   Generated from app/models/*.py — keep in sync with backend schema
   ═══════════════════════════════════════════════════════════════════════════ */

// ── Enums / Literals ────────────────────────────────────────────────────
export type AssetClass = 'etf' | 'stock'
export type OperationalMode = 'research' | 'simulation' | 'live'
export type Regime = 'trending' | 'rotation' | 'risk_off' | 'consolidation'
export type SignalType = 'long' | 'neutral' | 'avoid'
export type TradeSide = 'buy' | 'sell'
export type OrderType = 'market' | 'limit'
export type TradeStatus = 'pending' | 'submitted' | 'filled' | 'cancelled' | 'rejected'
export type PositionStatus = 'open' | 'closed'
export type HealthStatus = 'ok' | 'stale' | 'error' | 'unknown'
export type KillSwitch = 'trading' | 'rebalance' | 'all' | 'force_liquidate' | 'sentiment' | 'maintenance'

// ── Account (accounts table) ────────────────────────────────────────────
export interface Account {
  id: number
  name: string
  asset_class: AssetClass
  initial_capital: number
  cash_balance: number
  portfolio_value: number
  total_pnl: number
  realized_pnl: number
  unrealized_pnl: number
  high_watermark: number
  current_drawdown: number
  broker: string
  operational_mode: OperationalMode
  is_active: boolean
  max_positions: number
  max_position_pct: number
  max_sector_pct: number
  vol_target: number
  created_at: string
  updated_at: string
}

// ── ETF Universe (etf_universe table) ───────────────────────────────────
export interface ETFUniverse {
  id: number
  symbol: string
  name: string
  sector: string
  aum_bn: number | null
  avg_daily_vol_m: number | null
  spread_est_bps: number | null
  liquidity_score: number | null
  inception_date: string | null
  options_market: boolean
  mt5_symbol: string | null
  is_active: boolean
  in_active_set: boolean
  active_set_reason: string | null
  active_set_changed_at: string | null
  notes: string | null
  your_rating: number | null
  tags: string | null
  last_signal_type: SignalType | null
  last_composite_score: number | null
  last_signal_at: string | null
  perf_1w: number | null
  perf_1m: number | null
  perf_3m: number | null
  correlation_to_spy: number | null
  added_at: string | null
  created_at: string
  updated_at: string
}

// ── Stock Universe (stock_universe table) ────────────────────────────────
export interface StockUniverse {
  id: number
  symbol: string
  name: string
  sector: string
  industry: string | null
  market_cap_bn: number | null
  avg_daily_vol_m: number | null
  spread_est_bps: number | null
  liquidity_score: number | null
  float_shares_m: number | null
  short_interest_pct: number | null
  beta: number | null
  options_market: boolean
  pe_ratio: number | null
  forward_pe: number | null
  pb_ratio: number | null
  ps_ratio: number | null
  roe: number | null
  debt_to_equity: number | null
  revenue_growth_yoy: number | null
  earnings_growth_yoy: number | null
  dividend_yield: number | null
  profit_margin: number | null
  next_earnings_date: string | null
  last_earnings_date: string | null
  last_earnings_surprise: number | null
  earnings_surprise_3q_avg: number | null
  is_active: boolean
  in_active_set: boolean
  active_set_reason: string | null
  active_set_changed_at: string | null
  notes: string | null
  your_rating: number | null
  tags: string | null
  last_signal_type: SignalType | null
  last_composite_score: number | null
  last_signal_at: string | null
  perf_1w: number | null
  perf_1m: number | null
  perf_3m: number | null
  correlation_to_spy: number | null
  fundamentals_updated_at: string | null
  added_at: string | null
  created_at: string
  updated_at: string
}

// ── Factor Scores (factor_scores table) ─────────────────────────────────
export interface FactorScore {
  id: number
  symbol: string
  calc_time: string
  trend_score: number | null
  volatility_score: number | null
  sentiment_score: number | null
  dispersion_score: number | null
  correlation_score: number | null
  breadth_score: number | null
  liquidity_score: number | null
  slippage_score: number | null
  composite_score: number | null
  fundamentals_score: number | null
  earnings_score: number | null
  asset_class: AssetClass
}

// ── Signal (signals table) ──────────────────────────────────────────────
export interface Signal {
  id: number
  symbol: string
  signal_time: string
  composite_score: number
  trend_score: number | null
  volatility_score: number | null
  sentiment_score: number | null
  breadth_score: number | null
  dispersion_score: number | null
  liquidity_score: number | null
  regime: Regime | null
  regime_confidence: number | null
  signal_type: SignalType
  block_reason: string | null
  asset_class: AssetClass
  account_id: number | null
}

// ── Position (positions table) ──────────────────────────────────────────
export interface Position {
  id: number
  symbol: string
  broker: string
  broker_ref: string | null
  direction: string
  entry_price: number
  current_price: number | null
  quantity: number | null
  notional: number | null
  weight: number | null
  high_watermark: number | null
  unrealized_pnl: number | null
  realized_pnl: number
  sector: string | null
  status: PositionStatus
  opened_at: string
  closed_at: string | null
  close_reason: string | null
  stop_price: number | null
  take_profit_price: number | null
  asset_class: AssetClass
  account_id: number | null
}

// ── Trade (trades table) ────────────────────────────────────────────────
export interface Trade {
  id: number
  symbol: string
  broker: string
  broker_order_id: string | null
  side: TradeSide
  order_type: OrderType
  requested_notional: number | null
  filled_notional: number | null
  filled_quantity: number | null
  fill_price: number | null
  mid_at_submission: number | null
  slippage_bps: number | null
  estimated_slippage_bps: number | null
  status: TradeStatus
  operational_mode: OperationalMode
  trade_time: string
  fill_time: string | null
  signal_composite: number | null
  regime: Regime | null
  direction: string | null
  stop_distance_at_entry: number | null
  r_multiple: number | null
  asset_class: AssetClass
  account_id: number | null
}

// ── Price Bar (price_bars table) ────────────────────────────────────────
export interface PriceBar {
  id: number
  symbol: string
  timeframe: string
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  vwap: number | null
  trade_count: number | null
  source: string
  asset_class: AssetClass
}

// ── Quote (quotes table) ────────────────────────────────────────────────
export interface Quote {
  symbol: string
  timestamp: string
  bid: number
  ask: number
  mid: number
  spread_bps: number | null
  source: string
}

// ── Sentiment (sentiment_raw table) ─────────────────────────────────────
export interface SentimentRaw {
  id: number
  symbol: string
  headline: string
  source: string
  raw_score: number
  model: string
  timestamp: string
}

// ── Performance Metric (performance_metrics table) ──────────────────────
export interface PerformanceMetric {
  id: number
  date: string
  portfolio_value: number | null
  daily_return: number | null
  cumulative_return: number | null
  drawdown: number | null
  sharpe_30d: number | null
  volatility_30d: number | null
  var_95: number | null
  regime: string | null
  num_positions: number | null
  cash_pct: number | null
  asset_class: AssetClass
  account_id: number | null
}

// ── Options Chain (options_chains table) ─────────────────────────────────
export interface OptionsChain {
  id: number
  symbol: string
  expiry: string
  strike: number
  call_put: 'call' | 'put'
  iv: number | null
  volume: number | null
  oi: number | null
  delta: number | null
  timestamp: string
}

// ── System Config (system_config table) ─────────────────────────────────
export interface SystemConfig {
  id: number
  category: string
  key: string
  value: string
  value_type: string
  is_secret: boolean
  description: string | null
  updated_at: string
  updated_by: string
}

// ── API Response Shapes ─────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  components: Record<string, { status: string; detail?: string }>
  kill_switches: Record<string, boolean>
  operational_mode: OperationalMode
}

export interface ScoresResponse {
  scores: Array<{
    symbol: string
    composite_score: number
    trend_score: number
    volatility_score: number
    sentiment_score: number
    breadth_score: number
    liquidity_score: number
    signal_type: SignalType
    [key: string]: unknown
  }>
  weights: Record<string, number>
  asset_class: AssetClass
  computed_at: string
}

export interface SignalsResponse {
  long: Signal[]
  neutral: Signal[]
  avoid: Signal[]
  regime: Regime
  regime_confidence: number
  computed_at: string
}

export interface RegimeResponse {
  regime: Regime
  confidence: number
  history: Array<{ date: string; regime: Regime; confidence: number }>
}

export interface PortfolioResponse {
  positions: Position[]
  cash: number
  nav: number
  total_pnl: number
  sector_weights: Record<string, number>
}

export interface RiskResponse {
  kill_switches: Record<string, boolean>
  drawdown: number
  sharpe_30d: number | null
  volatility_30d: number | null
  var_95: number | null
  max_drawdown: number | null
}

export interface BacktestConfig {
  asset_class: AssetClass
  weights: Record<string, number>
  start_date: string
  end_date: string
  initial_capital: number
  rebalance_freq: string
  max_positions: number
  slippage_bps: number
}

export interface BacktestResult {
  dates: string[]
  equity_curve: number[]
  returns: number[]
  total_return: number
  sharpe: number
  max_drawdown: number
  volatility: number
  win_rate: number
  trades: number
}

export interface ConfigResponse {
  [category: string]: Array<{
    key: string
    value: string
    value_type: string
    is_secret: boolean
    description: string | null
  }>
}

export interface PipelineStatusResponse {
  modules: Record<string, {
    status: HealthStatus
    items_processed: number
    compute_time_ms: number
    last_activity: string
  }>
  queue_depths: Record<string, number>
}

// ── WebSocket Event Payloads ────────────────────────────────────────────

export interface WsPriceUpdate {
  symbol: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  timestamp: string
}

export interface WsFactorScores {
  symbol: string
  trend_score: number
  volatility_score: number
  sentiment_score: number
  breadth_score: number
  liquidity_score: number
  composite_score: number
  [key: string]: unknown
}

export interface WsSignal {
  symbol: string
  signal_type: SignalType
  composite_score: number
  regime: Regime
  regime_confidence: number
  block_reason: string | null
}

export interface WsPortfolio {
  positions: Position[]
  cash: number
  nav: number
  pnl: number
}

export interface WsRiskAlert {
  alert_type: string
  severity: string
  message: string
  timestamp: string
}

export interface WsTrade {
  symbol: string
  side: TradeSide
  fill_price: number
  slippage_bps: number
  status: TradeStatus
}

export interface WsRegimeChange {
  regime: Regime
  confidence: number
  timestamp: string
}

export interface WsSystemHealth {
  module: string
  status: HealthStatus
  items_processed: number
  compute_time_ms: number
  last_activity: string
}

export interface WsConfigChange {
  category: string
  key: string
  value: string
  updated_by: string
  updated_at: string
}

export interface WsKillSwitch {
  type: 'kill_switch'
  switch: string
  active: boolean
  timestamp: string
}
