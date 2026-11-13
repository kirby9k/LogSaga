import type { ReactNode } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useRecent, useRecentlyAdded, useDashboardStats } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { formatEpisode } from '@/lib/format'
import { Clapperboard, Clock, Film, PlusCircle, Tv } from 'lucide-react'

export const Route = createFileRoute('/')({
  component: Dashboard,
})

function Dashboard() {
  const { data: recentData, isLoading: recentLoading } = useRecent({ limit: 10 })
  const { data: addedData, isLoading: addedLoading } = useRecentlyAdded({ limit: 10 })
  const { data: stats } = useDashboardStats()

  const recentItems = recentData?.items ?? []
  const addedItems = addedData?.items ?? []

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto space-y-10">

      {/* Library statistics */}
      <section>
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider mb-3">
          Library
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <StatCard icon={<Film size={16} />} label="Movies" value={stats?.total_movies} />
          <StatCard icon={<Tv size={16} />} label="TV Shows" value={stats?.total_shows} />
          <StatCard icon={<Tv size={16} />} label="Episodes" value={stats?.total_episodes} />
          <StatCard icon={<Clapperboard size={16} />} label="Movie Plays" value={stats?.movie_plays} />
          <StatCard icon={<Clock size={16} />} label="Episode Plays" value={stats?.episode_plays} />
        </div>
      </section>

      {/* Recently Added */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <PlusCircle size={16} className="text-muted-foreground" />
            Recently Added
          </h2>
          <Link to="/recently-added" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
            View all →
          </Link>
        </div>

        {addedLoading && <LoadingSkeleton count={10} variant="card" />}
        {!addedLoading && addedItems.length === 0 && (
          <p className="text-sm text-muted-foreground">No items added yet.</p>
        )}
        {addedItems.length > 0 && (
          <div className="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-2">
            {addedItems.map(item => (
              <Link
                key={item.id}
                to="/items/$itemId"
                params={{ itemId: String(item.id) }}
                className="group block"
              >
                <div className="aspect-[2/3] rounded overflow-hidden bg-muted mb-1">
                  <MediaPoster
                    src={item.poster_url}
                    alt={item.title}
                    className="w-full h-full group-hover:opacity-85 transition"
                    fallbackLetter={item.title[0]}
                  />
                </div>
                <p className="text-xs line-clamp-1 leading-snug text-muted-foreground group-hover:text-foreground transition-colors">
                  {item.title}
                </p>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* Recently Played */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="flex items-center gap-2 text-base font-semibold">
            <Clock size={16} className="text-muted-foreground" />
            Recently Played
          </h2>
          <Link to="/recent" search={{ type: 'all' }} className="text-xs text-muted-foreground hover:text-foreground transition-colors">
            View all →
          </Link>
        </div>

        {recentLoading && <LoadingSkeleton count={10} variant="card" />}
        {!recentLoading && recentItems.length === 0 && (
          <p className="text-sm text-muted-foreground">No watch history yet.</p>
        )}
        {recentItems.length > 0 && (
          <div className="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-2">
            {recentItems.map(({ session, item }) => (
              <Link
                key={session.id}
                to="/items/$itemId"
                params={{ itemId: String(item.id) }}
                className="group block"
              >
                <div className="relative aspect-[2/3] rounded overflow-hidden bg-muted mb-1">
                  <MediaPoster
                    src={item.poster_url}
                    alt={item.title}
                    className="w-full h-full group-hover:opacity-85 transition"
                    fallbackLetter={item.title[0]}
                  />
                  <div className="absolute top-1 right-1">
                    <StatusBadge status={session.status} completionReason={session.completion_reason} />
                  </div>
                </div>
                <p className="text-xs line-clamp-2 leading-snug text-muted-foreground group-hover:text-foreground transition-colors">
                  {item.show_title
                    ? `${item.season != null && item.episode != null ? formatEpisode(item.season, item.episode) + ' · ' : ''}${item.title}`
                    : item.title}
                </p>
              </Link>
            ))}
          </div>
        )}
      </section>

    </div>
  )
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: number | undefined
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground mb-1">
        {icon}
        <span className="text-xs">{label}</span>
      </div>
      <p className="text-xl font-bold">
        {value == null ? '—' : value.toLocaleString()}
      </p>
    </div>
  )
}
