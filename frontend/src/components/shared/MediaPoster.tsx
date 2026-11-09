import { useState } from 'react'
import { cn } from '@/lib/utils'

interface Props {
  src?: string | null
  alt: string
  className?: string
  fallbackLetter?: string
}

export function MediaPoster({ src, alt, className, fallbackLetter }: Props) {
  const [failed, setFailed] = useState(false)
  const letter = fallbackLetter ?? alt[0]?.toUpperCase() ?? '?'

  if (!src || failed) {
    return (
      <div
        className={cn(
          'flex items-center justify-center bg-muted text-muted-foreground font-semibold select-none',
          className,
        )}
        aria-label={alt}
      >
        {letter}
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={alt}
      className={cn('object-cover', className)}
      onError={() => setFailed(true)}
    />
  )
}
