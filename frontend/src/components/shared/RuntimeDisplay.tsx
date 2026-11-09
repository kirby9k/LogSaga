import { formatRuntime } from '@/lib/format'

interface Props {
  seconds?: number | null
  className?: string
}

export function RuntimeDisplay({ seconds, className }: Props) {
  return <span className={className}>{formatRuntime(seconds)}</span>
}
