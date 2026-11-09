import { Inbox } from 'lucide-react'

interface Props {
  message?: string
}

export function EmptyState({ message = 'Nothing here yet.' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-3 text-muted-foreground">
      <Inbox size={36} />
      <p className="text-sm">{message}</p>
    </div>
  )
}
