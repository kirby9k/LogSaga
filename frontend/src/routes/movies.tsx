import { useState, useEffect } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMovies } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { RuntimeDisplay } from '@/components/shared/RuntimeDisplay'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { formatRelative } from '@/lib/format'
import type { components } from '@/api/types'

type MovieSummaryOut = components['schemas']['MovieSummaryOut']

export const Route = createFileRoute('/movies')({
  component: MoviesPage,
})

const LIMIT = 50
type FilterTab = 'history' | 'all' | 'unwatched'

const FILTER_TABS: { value: FilterTab; label: string }[] = [
  { value: 'history', label: 'Watched' },
  { value: 'all',     label: 'All' },
  { value: 'unwatched', label: 'Unwatched' },
]

function MoviesPage() {
  const navigate = useNavigate()
  const [inputQ, setInputQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [filter, setFilter] = useState<FilterTab>('all')
  const [sort, setSort] = useState<'title' | 'recent' | 'added'>('title')
  const [offset, setOffset] = useState(0)

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQ(inputQ)
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [inputQ])

  // Reset offset when filter/sort changes
  useEffect(() => { setOffset(0) }, [filter, sort])

  const { data, isLoading, isError } = useMovies({
    search: debouncedQ || null,
    filter,
    sort,
    limit: LIMIT,
    offset,
  })

  const totalPages = data ? Math.ceil(data.total / LIMIT) : 0
  const currentPage = Math.floor(offset / LIMIT)

  return (
    <div className="p-4 md:p-6">
      <PageHeader title="Movies" />

      {/* Filter tabs */}
      <div className="flex gap-1 mb-4 border-b border-border">
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

      {/* Toolbar */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <Input
          placeholder="Search movies…"
          value={inputQ}
          onChange={e => setInputQ(e.target.value)}
          className="sm:max-w-xs"
        />
        <div className="flex gap-2">
          <Button
            variant={sort === 'title' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setSort('title')}
          >
            A–Z
          </Button>
          <Button
            variant={sort === 'recent' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setSort('recent')}
          >
            Recent
          </Button>
          <Button
            variant={sort === 'added' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setSort('added')}
          >
            Added
          </Button>
        </div>
        {data && (
          <span className="text-sm text-muted-foreground self-center">
            {data.total} {data.total === 1 ? 'movie' : 'movies'}
          </span>
        )}
      </div>

      {isLoading && <LoadingSkeleton count={12} variant="card" />}
      {isError && <ErrorState message="Failed to load movies." />}
      {!isLoading && !isError && data?.items.length === 0 && (
        <EmptyState message="No movies found." />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
            {data.items.map(movie => (
              <MovieCard
                key={movie.id}
                movie={movie}
                onClick={() => navigate({ to: '/items/$itemId', params: { itemId: String(movie.id) } })}
              />
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex justify-center gap-2 mt-8">
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage === 0}
                onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              >
                Previous
              </Button>
              <span className="text-sm text-muted-foreground self-center">
                Page {currentPage + 1} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage >= totalPages - 1}
                onClick={() => setOffset(offset + LIMIT)}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function MovieCard({ movie, onClick }: { movie: MovieSummaryOut; onClick: () => void }) {
  return (
    <button
      className="group text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
      onClick={onClick}
      aria-label={movie.title}
    >
      <div className="relative overflow-hidden rounded-lg aspect-[2/3] bg-muted mb-2">
        <MediaPoster
          src={movie.poster_url ?? null}
          alt={movie.title}
          className="w-full h-full transition-transform group-hover:scale-105"
        />
        {/* Session count badge */}
        {movie.total_sessions > 1 && (
          <div className="absolute top-1.5 right-1.5">
            <Badge variant="secondary" className="text-xs px-1.5 py-0.5">
              {movie.total_sessions}×
            </Badge>
          </div>
        )}
      </div>
      <p className="text-sm font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
        {movie.title}
      </p>
      <div className="flex flex-wrap gap-1 mt-0.5 items-center text-xs text-muted-foreground">
        {movie.year && <span>{movie.year}</span>}
        {movie.runtime_s && (
          <span>· <RuntimeDisplay seconds={movie.runtime_s} /></span>
        )}
      </div>
      {movie.last_watched_at && (
        <p className="text-xs text-muted-foreground mt-0.5">
          {formatRelative(movie.last_watched_at)}
        </p>
      )}
    </button>
  )
}
