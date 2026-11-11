import { createFileRoute, Link } from '@tanstack/react-router'
import { isToday, isYesterday, format, parseISO } from 'date-fns'
import { useRecentInfinite } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Button } from '@/components/ui/button'
import { formatEpisode } from '@/lib/format'

/** Format a date string into a human-readable group label. */
function dateGroupLabel(iso: string): string {
  try {
    const d = parseISO(iso)
    if (isToday(d)) return 'Today'
    if (isYesterday(d)) return 'Yesterday'
    // Within the last 6 days → "Monday, Nov 3"
    const daysAgo = (Date.now() - d.getTime()) / 86_400_000
    if (daysAgo < 7) return format(d, 'EEEE, MMM d')
    return format(d, 'MMM d, yyyy')
  } catch {
    return 'Unknown'
  }
}

/** Group items by calendar date of their last_activity_at. */
function groupByDate<T extends { session: { last_activity_at: string } }>(
  items: T[],
): Array<{ label: string; items: T[] }> {
  const groups: { label: string; items: T[] }[] = []
  const seen: Record<string, number> = {}

  for (const item of items) {
    const d = parseISO(item.session.last_activity_at)
    const key = format(d, 'yyyy-MM-dd')
    const label = dateGroupLabel(item.session.last_activity_at)
    if (key in seen) {
      groups[seen[key]].items.push(item)
    } else {
      seen[key] = groups.length
      groups.push({ label, items: [item] })
    }
  }

  return groups
}

type Filter = 'all' | 'movie' | 'episode'

export const Route = createFileRoute('/')({
  validateSearch: (s: Record<string, unknown>) => ({
    type: (s.type as Filter) ?? 'all',
  }),
  component: RecentlyPlayed,
})

function RecentlyPlayed() {
  const navigate = Route.useNavigate()
  const { type: filter } = Route.useSearch()

  const setFilter = (f: Filter) => navigate({ search: () => ({ type: f }) })

  const {
    data,
    isLoading,
    isError,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useRecentInfinite({
    type: filter === 'all' ? null : filter,
  })

  // Flatten all pages into a single item list for date grouping
  const allItems = data?.pages.flatMap(p => p.items) ?? []
  const total = data?.pages[0]?.total ?? 0

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Recently Played"
        subtitle={data ? `${total} sessions` : undefined}
        actions={
          <div className="flex gap-1">
            {(['all', 'movie', 'episode'] as Filter[]).map(f => (
              <Button
                key={f}
                size="sm"
                variant={filter === f ? 'default' : 'ghost'}
                onClick={() => setFilter(f)}
                className="capitalize"
              >
                {f === 'episode' ? 'TV' : f === 'all' ? 'All' : 'Movies'}
              </Button>
            ))}
          </div>
        }
      />

      {isLoading && <LoadingSkeleton count={12} variant="card" />}
      {isError && <ErrorState />}
      {!isLoading && allItems.length === 0 && <EmptyState message="No watch history yet." />}

      {allItems.length > 0 && (
        <>
          {groupByDate(allItems).map(group => (
            <section key={group.label} className="mb-6">
              <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
                {group.label}
              </h2>
              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 xl:grid-cols-10 gap-3">
                {group.items.map(({ session, item }) => (
                  <Link
                    key={session.id}
                    to="/items/$itemId"
                    params={{ itemId: String(item.id) }}
                    className="group block space-y-1"
                  >
                    <div className="relative overflow-hidden rounded-md aspect-[2/3] bg-muted">
                      <MediaPoster
                        src={item.poster_url}
                        alt={item.title}
                        className="w-full h-full"
                        fallbackLetter={item.title[0]}
                      />
                      <div className="absolute top-1 right-1">
                        <StatusBadge status={session.status} completionReason={session.completion_reason} />
                      </div>
                    </div>
                    <div>
                      <p className="text-xs font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                        {item.show_title ? (
                          <>
                            {item.show_id ? (
                              <Link
                                to="/shows/$showId"
                                params={{ showId: String(item.show_id) }}
                                className="text-muted-foreground block truncate hover:text-foreground"
                                onClick={e => e.stopPropagation()}
                              >
                                {item.show_title}
                              </Link>
                            ) : (
                              <span className="text-muted-foreground block truncate">{item.show_title}</span>
                            )}
                            {item.season != null && item.episode != null
                              ? `${formatEpisode(item.season, item.episode)} · ${item.title}`
                              : item.title}
                          </>
                        ) : item.title}
                      </p>
                      <TimeAgo iso={session.last_activity_at} className="text-xs text-muted-foreground" />
                    </div>
                  </Link>
                ))}
              </div>
            </section>
          ))}

          {hasNextPage && (
            <div className="mt-4 flex justify-center">
              <Button
                variant="outline"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage ? 'Loading…' : 'Load More'}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
