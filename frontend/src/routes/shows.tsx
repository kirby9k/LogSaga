import { useState, useEffect } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useShows } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PercentBar } from '@/components/shared/PercentBar'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Input } from '@/components/ui/input'
import { formatEpisode } from '@/lib/format'

export const Route = createFileRoute('/shows')({
  component: ShowsPage,
})

type FilterTab = 'history' | 'all' | 'unwatched'
type SortOption = 'title' | 'activity' | 'added'

const FILTER_TABS: { value: FilterTab; label: string }[] = [
  { value: 'history',   label: 'Watched' },
  { value: 'all',       label: 'All' },
  { value: 'unwatched', label: 'Unwatched' },
]

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'title',    label: 'A–Z' },
  { value: 'activity', label: 'Last Played' },
  { value: 'added',    label: 'Recently Added' },
]

function ShowsPage() {
  const navigate = useNavigate()
  const [inputQ, setInputQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [filter, setFilter] = useState<FilterTab>('all')
  const [sort, setSort] = useState<SortOption>('title')

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(inputQ), 300)
    return () => clearTimeout(t)
  }, [inputQ])

  const { data, isLoading, isError } = useShows({
    search: debouncedQ || undefined,
    filter,
    sort,
  })

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="TV Shows"
        subtitle={data ? `${data.length} shows` : undefined}
        actions={
          <Input
            placeholder="Search shows…"
            value={inputQ}
            onChange={e => setInputQ(e.target.value)}
            className="w-48"
          />
        }
      />

      {/* Filter tabs + sort */}
      <div className="flex items-center justify-between mb-4 border-b border-border">
        <div className="flex gap-1">
          {FILTER_TABS.map(tab => (
            <button
              key={tab.value}
              onClick={() => setFilter(tab.value)}
              className={[
                'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
                filter === tab.value
                  ? 'border-primary text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              ].join(' ')}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 pb-1">
          {SORT_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => setSort(opt.value)}
              className={[
                'px-3 py-1 text-xs rounded-md transition-colors',
                sort === opt.value
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted',
              ].join(' ')}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <LoadingSkeleton count={12} variant="card" />}
      {isError && <ErrorState />}
      {data && data.length === 0 && <EmptyState message="No TV shows found." />}

      {data && data.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {data.map(show => {
            const pct = show.total_episodes > 0
              ? (show.watched_episodes / show.total_episodes) * 100
              : 0
            return (
              <div
                key={show.show_id}
                className="group cursor-pointer space-y-2"
                onClick={() =>
                  navigate({
                    to: '/shows/$showId',
                    params: { showId: String(show.show_id) },
                  })
                }
              >
                <div className="relative overflow-hidden rounded-md aspect-[2/3] bg-muted">
                  <MediaPoster
                    src={show.poster_url}
                    alt={show.show_title}
                    className="w-full h-full"
                    fallbackLetter={show.show_title[0]}
                  />
                </div>
                <div>
                  <p className="text-sm font-medium line-clamp-1 group-hover:text-primary transition-colors">
                    {show.show_title}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {show.watched_episodes}/{show.total_episodes} eps
                  </p>
                  <PercentBar percent={pct} className="mt-1" showLabel={false} />
                  {show.next_unwatched && (
                    <p className="text-xs text-muted-foreground mt-0.5">
                      Next: {formatEpisode(show.next_unwatched.season, show.next_unwatched.episode)}
                    </p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
