import { Badge } from '@/components/ui/badge'
import { STATUS_LABELS } from '@/lib/constants'

interface Props {
  status: string
  completionReason?: string | null
}

const VARIANT: Record<string, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  completed: 'default',
  partial: 'secondary',
  open: 'outline',
  ignored: 'outline',
}

export function StatusBadge({ status, completionReason }: Props) {
  const label = STATUS_LABELS[status] ?? status
  const variant = VARIANT[status] ?? 'outline'

  return (
    <span className="inline-flex items-center gap-1">
      <Badge variant={variant}>{label}</Badge>
      {completionReason === 'kodi_marked' && (
        <Badge variant="outline" className="text-xs">marked</Badge>
      )}
    </span>
  )
}
