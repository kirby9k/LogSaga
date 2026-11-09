import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'

interface Props {
  percent?: number | null
  className?: string
  showLabel?: boolean
}

export function PercentBar({ percent, className, showLabel = true }: Props) {
  const pct = Math.min(100, Math.max(0, percent ?? 0))
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <Progress value={pct} className="flex-1 h-1.5" />
      {showLabel && (
        <span className="text-xs text-muted-foreground w-8 text-right tabular-nums">
          {Math.round(pct)}%
        </span>
      )}
    </div>
  )
}
