import { AlertCircle } from 'lucide-react'

interface Props {
  message?: string
}

export function ErrorState({ message = 'Something went wrong.' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-3 text-muted-foreground">
      <AlertCircle size={36} />
      <p className="text-sm">{message}</p>
    </div>
  )
}
