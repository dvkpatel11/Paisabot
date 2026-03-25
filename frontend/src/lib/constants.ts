export const ROUTES = {
  // ETF Strategies
  dashboard:  '/dashboard',
  factors:    '/factors',
  rotation:   '/rotation',
  execution:  '/execution',
  portfolio:  '/portfolio',
  backtest:   '/backtest',
  // Equity Research
  screener:       '/stocks',
  security:       '/stocks/securities',
  stockFactors:   '/stocks/factors',
  stockBacktest:  '/stocks/backtest',
  // Market Intelligence
  news:  '/intel/news',
  data:  '/intel/data',
  macro: '/intel/macro',
  // System
  pipelines: '/pipelines',
  alerts:    '/alerts',
  config:    '/config',
} as const

export const NAV_SECTIONS = [
  {
    label: 'ETF STRATEGIES',
    items: [
      { name: 'Dashboard',  path: ROUTES.dashboard,  icon: 'BarChart3' },
      { name: 'Factors',    path: ROUTES.factors,     icon: 'Layers' },
      { name: 'Rotation',   path: ROUTES.rotation,    icon: 'RefreshCw' },
      { name: 'Execution',  path: ROUTES.execution,   icon: 'Zap' },
      { name: 'Portfolio',  path: ROUTES.portfolio,   icon: 'PieChart' },
      { name: 'Backtest',   path: ROUTES.backtest,    icon: 'FlaskConical' },
    ],
  },
  {
    label: 'EQUITY RESEARCH',
    items: [
      { name: 'Screener',   path: ROUTES.screener,      icon: 'Filter' },
      { name: 'Securities', path: ROUTES.security,       icon: 'CandlestickChart' },
      { name: 'Factors',    path: ROUTES.stockFactors,   icon: 'BarChart2' },
      { name: 'Backtest',   path: ROUTES.stockBacktest,  icon: 'TestTube2' },
    ],
  },
  {
    label: 'MARKET INTELLIGENCE',
    items: [
      { name: 'News & Sentiment', path: ROUTES.news,  icon: 'Newspaper' },
      { name: 'Data Center',      path: ROUTES.data,   icon: 'Database' },
      { name: 'Macro Calendar',   path: ROUTES.macro,  icon: 'Globe' },
    ],
  },
  {
    label: 'SYSTEM',
    items: [
      { name: 'Pipelines', path: ROUTES.pipelines, icon: 'Network' },
      { name: 'Alerts',    path: ROUTES.alerts,     icon: 'AlertTriangle' },
      { name: 'Config',    path: ROUTES.config,     icon: 'Settings' },
    ],
  },
] as const

export const HEALTH_MODULES = ['data', 'factors', 'signals', 'portfolio', 'risk', 'execution', 'monitoring'] as const

export const REGIMES = ['trending', 'rotation', 'risk_off', 'consolidation'] as const
export const MODES = ['research', 'simulation', 'live'] as const
export const ASSET_CLASSES = ['etf', 'stock'] as const
