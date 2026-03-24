import { cn, pnlClass, pnlSign, fmtPct } from '@/lib/utils'

interface PnlTextProps {
  value: number | null | undefined
  format?: 'currency' | 'percent'
  className?: string
}

export function PnlText({ value, format = 'currency', className }: PnlTextProps) {
  const text = format === 'percent' ? fmtPct(value) : pnlSign(value)
  return <span className={cn('mono', pnlClass(value), className)}>{text}</span>
}
