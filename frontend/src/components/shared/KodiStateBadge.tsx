import { Badge } from '@/components/ui/badge'
import { KODI_STATE_LABELS } from '@/lib/constants'

interface Props {
  state: string
  compact?: boolean
}

const VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  watched: 'default',
  in_progress: 'secondary',
  unwatched: 'outline',
  removed: 'destructive',
}

export function KodiStateBadge({ state, compact = false }: Props) {
  const label = KODI_STATE_LABELS[state] ?? state
  const variant = VARIANT[state] ?? 'outline'

  if (compact && state === 'unwatched') return null

  return (
    <Badge variant={variant} className={compact ? 'text-xs px-1.5 py-0' : undefined}>
      {label}
    </Badge>
  )
}
