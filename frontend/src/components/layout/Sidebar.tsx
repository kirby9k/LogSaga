import {
  BarChart2,
  Clock,
  Clapperboard,
  List,
  Play,
  PlusCircle,
  Settings2,
  Timer,
  Tv,
} from 'lucide-react'
import { NavLink } from './NavLink'

interface SidebarProps {
  onClose?: () => void
}

const NAV = [
  { to: '/',               icon: <Clock size={16} />,       label: 'Recently Played' },
  { to: '/continue',       icon: <Play size={16} />,        label: 'Continue Watching' },
  { to: '/partial',        icon: <Timer size={16} />,       label: 'Started, Not Done' },
  { to: '/history',        icon: <List size={16} />,        label: 'History' },
  { to: '/shows',          icon: <Tv size={16} />,          label: 'TV Shows' },
  { to: '/movies',         icon: <Clapperboard size={16} />, label: 'Movies' },
  { to: '/recently-added', icon: <PlusCircle size={16} />,  label: 'Recently Added' },
  { to: '/stats',          icon: <BarChart2 size={16} />,   label: 'Stats' },
  { to: '/settings',       icon: <Settings2 size={16} />,   label: 'Settings' },
]

export function Sidebar({ onClose }: SidebarProps) {
  return (
    <nav className="w-56 h-full bg-sidebar border-r border-sidebar-border flex flex-col">
      {/* Logo / app name */}
      <div className="px-4 py-5 border-b border-sidebar-border">
        <span className="text-sm font-semibold text-sidebar-foreground tracking-wide uppercase">
          LogSAGA
        </span>
      </div>

      {/* Nav links */}
      <div className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} icon={n.icon} label={n.label} onClick={onClose} />
        ))}
      </div>
    </nav>
  )
}
