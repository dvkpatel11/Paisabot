import { scoreColor, fmtScore } from '@/lib/utils'

interface ScoreBarProps {
  score: number | null | undefined
  showLabel?: boolean
}

export function ScoreBar({ score, showLabel = true }: ScoreBarProps) {
  if (score == null) return <span className="text-muted mono text-2xs">--</span>

  return (
    <div className="score-bar">
      <div className="score-track">
        <div
          className="score-fill"
          style={{ width: `${score * 100}%`, backgroundColor: scoreColor(score) }}
        />
      </div>
      {showLabel && <span className="mono text-2xs text-secondary w-8 text-right">{fmtScore(score)}</span>}
    </div>
  )
}
