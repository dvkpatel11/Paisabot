import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import numeral from 'numeral'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmtCurrency(v: number | null | undefined): string {
  if (v == null) return '--'
  return numeral(v).format('$0,0.00')
}

export function fmtCompact(v: number | null | undefined): string {
  if (v == null) return '--'
  return numeral(v).format('$0.0a').toUpperCase()
}

export function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '--'
  return `${(v * 100).toFixed(decimals)}%`
}

export function fmtScore(v: number | null | undefined): string {
  if (v == null) return '--'
  return v.toFixed(3)
}

export function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '--'
  return numeral(v).format(decimals === 0 ? '0,0' : `0,0.${'0'.repeat(decimals)}`)
}

export function fmtBps(v: number | null | undefined): string {
  if (v == null) return '--'
  return `${v.toFixed(1)} bps`
}

export function pnlClass(v: number | null | undefined): string {
  if (v == null || v === 0) return 'flat'
  return v > 0 ? 'pos' : 'neg'
}

export function pnlSign(v: number | null | undefined): string {
  if (v == null) return '--'
  const s = v > 0 ? '+' : ''
  return `${s}${fmtCurrency(v)}`
}

export function scoreColor(score: number): string {
  if (score >= 0.65) return '#00c87a'
  if (score >= 0.35) return '#f0a800'
  return '#e82d6b'
}

export function regimeColor(regime: string): string {
  const map: Record<string, string> = {
    trending: 'text-regime-trending',
    rotation: 'text-regime-rotation',
    risk_off: 'text-regime-risk-off',
    consolidation: 'text-regime-consolidation',
  }
  return map[regime] ?? 'text-secondary'
}

export function regimeLabel(regime: string): string {
  const map: Record<string, string> = {
    trending: 'TRENDING',
    rotation: 'ROTATION',
    risk_off: 'RISK OFF',
    consolidation: 'CONSOLIDATION',
  }
  return map[regime] ?? regime.toUpperCase()
}
