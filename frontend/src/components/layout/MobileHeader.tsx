import { Menu } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface Props {
  onMenuClick: () => void
}

export function MobileHeader({ onMenuClick }: Props) {
  return (
    <header className="md:hidden flex items-center gap-3 px-4 py-3 border-b border-border bg-background">
      <Button variant="ghost" size="icon" onClick={onMenuClick} aria-label="Open menu">
        <Menu size={20} />
      </Button>
      <span className="text-sm font-semibold tracking-wide uppercase">LogSAGA</span>
    </header>
  )
}
