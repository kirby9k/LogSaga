import { createFileRoute, Link } from '@tanstack/react-router'
import { useSessions } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PercentBar } from '@/components/shared/PercentBar'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { formatRuntime, formatEpisode } from '@/lib/format'

export const Route = createFileRoute('/partial')({
  component: PartialPage,
})

function PartialPage() {
  // Filter: partial status, exclude items Kodi now considers watched (kodi_state = 'watched')
  const { data, isLoading, isError } = useSessions({ status: 'partial', limit: 100 })

  // Client-side: drop items where kodi_state is 'watched'
  const items = data?.items.filter(
    ({ item }) => item.kodi_state !== 'watched'
  ) ?? []

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Started, Not Finished"
        subtitle={data ? `${items.length} items` : undefined}
      />

      {isLoading && <LoadingSkeleton count={5} variant="row" />}
      {isError && <ErrorState />}
      {!isLoading && !isError && items.length === 0 && (
        <EmptyState message="No partially-watched items." />
      )}

      {items.length > 0 && (
        <div className="space-y-3">
          {items.map(({ session, item }) => (
            <Link
              key={session.id}
              to="/items/$itemId"
              params={{ itemId: String(item.id) }}
              className="flex gap-4 p-3 rounded-lg border border-border bg-card hover:bg-accent/50 transition-colors"
            >
              <div className="w-14 h-20 shrink-0 overflow-hidden rounded">
                <MediaPoster
                  src={item.thumbnail_url ?? item.poster_url}
                  alt={item.title}
                  className="w-full h-full"
                />
              </div>
              <div className="flex-1 min-w-0 py-1">
                {item.show_title && (
                  <p className="text-xs text-muted-foreground mb-0.5">{item.show_title}</p>
                )}
                <p className="font-medium text-sm line-clamp-1">
                  {item.season != null && item.episode != null
                    ? `${formatEpisode(item.season, item.episode)} · ${item.title}`
                    : item.title}
                </p>
                <PercentBar percent={session.percent} className="mt-2 max-w-xs" />
                <p className="text-xs text-muted-foreground mt-1">
                  Stopped at {formatRuntime(session.max_position_s)}
                  {item.runtime_s != null && ` of ${formatRuntime(item.runtime_s)}`}
                  {' · '}
                  <TimeAgo iso={session.last_activity_at} />
                </p>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
