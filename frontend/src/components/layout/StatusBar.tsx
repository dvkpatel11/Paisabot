import { useAppStore } from '@/stores/app'
import { cn, regimeLabel } from '@/lib/utils'

export function StatusBar() {
  const { wsConnected, wsLatency, lastTick, mode, regime } = useAppStore()

  return (
    <footer className="fixed bottom-0 left-0 right-0 h-statusbar z-[100] bg-surface border-t border-border flex items-center justify-between px-4 mono text-2xs text-muted">
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1">
          <span className={cn('w-1.5 h-1.5 rounded-full', wsConnected ? 'bg-profit shadow-[0_0_4px_theme(colors.profit)]' : 'bg-loss')} />
          <span>{wsConnected ? 'WS CONNECTED' : 'WS DISCONNECTED'}</span>
        </div>
        <div className="flex items-center gap-1">
          <span>LATENCY</span>
          <span>{wsLatency != null ? `${wsLatency}ms` : '--'}</span>
        </div>
        <div className="flex items-center gap-1">
          <span>LAST TICK</span>
          <span>{lastTick ?? '--'}</span>
        </div>
      </div>
      <div className="flex items-center gap-4">
        <span>{mode.toUpperCase()}</span>
        <span>&middot;</span>
        <span>{regimeLabel(regime)}</span>
        <span>&middot;</span>
        <span>v0.1.0</span>
      </div>
    </footer>
  )
}
