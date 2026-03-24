import { Bell, Settings, Search } from 'lucide-react'
import { useAppStore } from '@/stores/app'
import { cn, regimeLabel, regimeColor } from '@/lib/utils'

interface TopbarProps {
  onCommandOpen: () => void
}

export function Topbar({ onCommandOpen }: TopbarProps) {
  const { regime, regimeConfidence, mode, health, alerts, toggleAlertsPanel } = useAppStore()
  const unread = alerts.length

  const modeColors: Record<string, string> = {
    research:   'bg-cyan/10 text-cyan',
    simulation: 'bg-warn/10 text-warn',
    live:       'bg-profit/10 text-profit',
  }

  return (
    <header className="fixed top-0 left-0 right-0 z-[200] h-topbar bg-surface border-b border-border flex items-center justify-between px-5 gap-4">
      {/* Left: Brand + Clock */}
      <div className="flex items-center gap-5 min-w-[200px]">
        <a href="/" className="flex items-center gap-2 no-underline text-primary font-bold text-lg">
          <span className="text-xl text-accent">&#8383;</span>
          <span className="tracking-tight">Paisabot</span>
        </a>
        <div className="flex items-center gap-2">
          <span className="mono text-sm text-primary" id="clock-time">--:--:-- ET</span>
          <span className="text-2xs px-1.5 py-0.5 rounded-full font-semibold uppercase tracking-wider bg-overlay text-muted" id="clock-status">--</span>
        </div>
      </div>

      {/* Center: Search + Regime + Mode */}
      <div className="flex items-center gap-3 flex-1 justify-center">
        <button
          onClick={onCommandOpen}
          className="flex items-center gap-3 px-3 py-1.5 rounded border border-border bg-transparent text-muted mono text-2xs hover:border-accent hover:text-secondary transition-colors min-w-[200px] justify-between"
        >
          <span><Search size={12} className="inline mr-1.5" />Search...</span>
          <span className="opacity-50">Ctrl+K</span>
        </button>

        {/* Regime pill */}
        <div className={cn(
          'flex items-center gap-2 px-3 py-1 rounded-full border text-sm font-semibold uppercase tracking-widest bg-elevated transition-colors',
          regimeColor(regime), 'border-border'
        )}>
          <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse-dot" />
          <span>{regimeLabel(regime)}</span>
          <span className="mono text-2xs opacity-70">{Math.round(regimeConfidence * 100)}%</span>
        </div>

        {/* Mode badge */}
        <div className={cn('flex items-center gap-1 px-2.5 py-0.5 rounded-full text-2xs font-bold tracking-widest', modeColors[mode])}>
          <span className="w-1.5 h-1.5 rounded-full bg-current" />
          <span>{mode.toUpperCase()}</span>
        </div>
      </div>

      {/* Right: Health + Alerts + Settings */}
      <div className="flex items-center gap-3">
        {/* Health strip */}
        <div className="flex items-center gap-0.5">
          {Object.entries(health).map(([key, status]) => {
            const colors: Record<string, string> = {
              ok:    'bg-profit/10 text-profit',
              stale: 'bg-warn/10 text-warn',
              error: 'bg-loss/10 text-loss animate-pulse-dot',
            }
            return (
              <div
                key={key}
                className={cn('w-5 h-5 rounded-sm flex items-center justify-center text-[9px] font-bold mono', colors[status] ?? 'bg-overlay text-muted')}
                title={`${key}: ${status}`}
              >
                {key[0]?.toUpperCase()}
              </div>
            )
          })}
        </div>

        {/* Alert bell */}
        <button className="relative w-8 h-8 flex items-center justify-center rounded border border-border text-secondary hover:bg-elevated hover:text-primary transition-colors" onClick={toggleAlertsPanel}>
          <Bell size={14} />
          {unread > 0 && (
            <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-loss text-white text-[9px] font-bold flex items-center justify-center">
              {unread}
            </span>
          )}
        </button>

        {/* Config */}
        <a href="/config" className="w-8 h-8 flex items-center justify-center rounded border border-border text-secondary hover:bg-elevated hover:text-primary transition-colors" title="Configuration">
          <Settings size={14} />
        </a>
      </div>
    </header>
  )
}
