import { Link } from '@tanstack/react-router'
import { cn } from '@/lib/utils'

interface NavLinkProps {
  to: string
  icon: React.ReactNode
  label: string
  onClick?: () => void
}

export function NavLink({ to, icon, label, onClick }: NavLinkProps) {
  return (
    <Link
      to={to}
      onClick={onClick}
      className={cn(
        'flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium',
        'text-muted-foreground hover:text-foreground hover:bg-accent',
        'transition-colors',
      )}
      activeProps={{ className: 'bg-accent text-foreground' }}
    >
      <span className="w-4 h-4 shrink-0">{icon}</span>
      {label}
    </Link>
  )
}
