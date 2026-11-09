import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { formatRelative, formatDate } from '@/lib/format'

interface Props {
  iso?: string | null
  className?: string
}

export function TimeAgo({ iso, className }: Props) {
  if (!iso) return <span className={className}>—</span>
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className={className}>{formatRelative(iso)}</span>
      </TooltipTrigger>
      <TooltipContent>{formatDate(iso)}</TooltipContent>
    </Tooltip>
  )
}
