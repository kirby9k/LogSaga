import {
  BarChart2,
  Clock,
  Clapperboard,
  Eye,
  List,
  Play,
  PlusCircle,
  Search,
  Settings2,
  Timer,
  Tv,
} from 'lucide-react'
import { NavLink } from './NavLink'
import { useRecentlyViewed } from '@/api/queries'
import { Link } from '@tanstack/react-router'

interface SidebarProps {
  onClose?: () => void
  onSearchOpen?: () => void
}

const NAV = [
  { to: '/',               icon: <Clock size={16} />,        label: 'Recently Played' },
  { to: '/continue',       icon: <Play size={16} />,         label: 'Continue Watching' },
  { to: '/partial',        icon: <Timer size={16} />,        label: 'Started, Not Done' },
  { to: '/history',        icon: <List size={16} />,         label: 'History' },
  { to: '/shows',          icon: <Tv size={16} />,           label: 'TV Shows' },
  { to: '/movies',         icon: <Clapperboard size={16} />, label: 'Movies' },
  { to: '/recently-added', icon: <PlusCircle size={16} />,   label: 'Recently Added' },
  { to: '/recently-viewed', icon: <Eye size={16} />,         label: 'Recently Viewed' },
  { to: '/stats',          icon: <BarChart2 size={16} />,    label: 'Stats' },
  { to: '/settings',       icon: <Settings2 size={16} />,    label: 'Settings' },
]

export function Sidebar({ onClose, onSearchOpen }: SidebarProps) {
  const { data: recentViewed } = useRecentlyViewed(3)
  const recentItems = recentViewed ?? []

  return (
    <nav className="w-56 h-full bg-sidebar border-r border-sidebar-border flex flex-col">
      {/* Logo / app name */}
      <div className="px-4 py-5 border-b border-sidebar-border">
        <span className="text-sm font-semibold text-sidebar-foreground tracking-wide uppercase">
          LogSAGA
        </span>
      </div>

      {/* Search button */}
      <div className="px-2 pt-3 pb-1">
        <button
          onClick={onSearchOpen}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 rounded-md transition-colors"
        >
          <Search size={16} />
          <span className="flex-1 text-left">Search</span>
          <kbd className="hidden sm:inline text-xs bg-muted px-1 rounded opacity-70">⌃K</kbd>
        </button>
      </div>

      {/* Nav links */}
      <div className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} icon={n.icon} label={n.label} onClick={onClose} />
        ))}

        {/* Recently viewed sub-links (last 3 TMDB pages browsed) */}
        {recentItems.length > 0 && (
          <div className="pl-6 space-y-0.5 mt-0.5">
            {recentItems.map(rv => (
              <Link
                key={rv.id}
                to={(rv.media_type === 'movie' ? `/tmdb/movie/${rv.tmdb_id}` : `/tmdb/tv/${rv.tmdb_id}`) as never}
                onClick={onClose}
                className="block truncate text-xs text-muted-foreground hover:text-foreground py-0.5 px-2 rounded hover:bg-accent/40 transition-colors"
                title={rv.title}
              >
                {rv.title}
              </Link>
            ))}
          </div>
        )}
      </div>
    </nav>
  )
}
