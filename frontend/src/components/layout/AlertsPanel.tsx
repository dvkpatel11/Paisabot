import { X } from 'lucide-react'
import { useAppStore } from '@/stores/app'
import { cn } from '@/lib/utils'

export function AlertsPanel() {
  const { alerts, alertsPanelOpen, toggleAlertsPanel } = useAppStore()

  if (!alertsPanelOpen) return null

  return (
    <div className="fixed top-topbar right-0 bottom-0 w-[360px] z-[300] bg-surface border-l border-border shadow-[0_8px_32px_rgba(0,0,0,0.7)] flex flex-col">
      <div className="flex items-center justify-between px-5 py-4 border-b border-border font-semibold">
        <span>Risk Alerts</span>
        <button onClick={toggleAlertsPanel} className="text-secondary hover:text-primary transition-colors">
          <X size={16} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
        {alerts.length === 0 ? (
          <div className="text-center text-muted py-8 text-sm">No active alerts</div>
        ) : (
          alerts.map((alert, i) => (
            <div
              key={i}
              className={cn(
                'p-3 rounded border-l-[3px] bg-elevated',
                alert.severity === 'critical' ? 'border-loss' : alert.severity === 'warning' ? 'border-warn' : 'border-accent'
              )}
            >
              <div className="text-2xs font-bold uppercase tracking-wider text-secondary">{alert.alert_type}</div>
              <div className="text-sm mt-0.5 text-primary">{alert.message}</div>
              <div className="text-2xs text-muted mono mt-1">{alert.timestamp}</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
