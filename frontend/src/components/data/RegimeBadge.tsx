import { cn, regimeLabel } from '@/lib/utils'
import type { Regime } from '@/api/types'

const regimeStyles: Record<string, string> = {
  trending:      'bg-regime-trending/10 text-regime-trending border-regime-trending/30',
  rotation:      'bg-regime-rotation/10 text-regime-rotation border-regime-rotation/30',
  risk_off:      'bg-regime-risk-off/10  text-regime-risk-off  border-regime-risk-off/30',
  consolidation: 'bg-regime-consolidation/10 text-regime-consolidation border-regime-consolidation/30',
}

interface RegimeBadgeProps {
  regime: Regime
  confidence?: number
}

export function RegimeBadge({ regime, confidence }: RegimeBadgeProps) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-2xs font-bold uppercase tracking-wider border',
      regimeStyles[regime] ?? 'bg-overlay text-muted border-border'
    )}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {regimeLabel(regime)}
      {confidence != null && <span className="mono opacity-70">{Math.round(confidence * 100)}%</span>}
    </span>
  )
}
