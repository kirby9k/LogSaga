import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useRecentlyAdded } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PageHeader } from '@/components/shared/PageHeader'
import { KodiStateBadge } from '@/components/shared/KodiStateBadge'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Button } from '@/components/ui/button'
import { formatEpisode } from '@/lib/format'
import type { components } from '@/api/types'

type RecentlyAddedItemOut = components['schemas']['RecentlyAddedItemOut']

export const Route = createFileRoute('/recently-added')({
  component: RecentlyAddedPage,
})

const LIMIT = 50

type TypeFilter = 'all' | 'movie' | 'episode'

const TYPE_FILTERS: { value: TypeFilter; label: string }[] = [
  { value: 'all',     label: 'All' },
  { value: 'movie',   label: 'Movies' },
  { value: 'episode', label: 'Episodes' },
]

function RecentlyAddedPage() {
  const navigate = useNavigate()
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [offset, setOffset] = useState(0)

  const { data, isLoading, isError } = useRecentlyAdded({
    limit: LIMIT,
    offset,
    type: typeFilter === 'all' ? null : typeFilter,
  })

  const totalPages = data ? Math.ceil(data.total / LIMIT) : 0
  const currentPage = Math.floor(offset / LIMIT)

  function handleItemClick(item: RecentlyAddedItemOut) {
    if (item.media_type === 'episode' && item.show_id != null) {
      navigate({
        to: '/shows/$showId',
        params: { showId: String(item.show_id) },
      })
    } else {
      navigate({
        to: '/items/$itemId',
        params: { itemId: String(item.id) },
      })
    }
  }

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Recently Added"
        subtitle={data ? `${data.total} items` : undefined}
      />

      {/* Type filter tabs */}
      <div className="flex gap-1 mb-6 border-b border-border">
        {TYPE_FILTERS.map(f => (
          <button
            key={f.value}
            onClick={() => { setTypeFilter(f.value); setOffset(0) }}
            className={[
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              typeFilter === f.value
                ? 'border-primary text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            {f.label}
          </button>
        ))}
      </div>

      {isLoading && <LoadingSkeleton count={12} variant="card" />}
      {isError && <ErrorState message="Failed to load recently added." />}
      {!isLoading && !isError && data?.items.length === 0 && (
        <EmptyState message="Nothing recently added." />
      )}

      {data && data.items.length > 0 && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
            {data.items.map(item => (
              <RecentlyAddedCard
                key={item.id}
                item={item}
                onClick={() => handleItemClick(item)}
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

function RecentlyAddedCard({ item, onClick }: { item: RecentlyAddedItemOut; onClick: () => void }) {
  const isEpisode = item.media_type === 'episode'
  const posterSrc = isEpisode ? (item.poster_url ?? item.thumbnail_url) : item.poster_url

  const subtitle = isEpisode && item.show_title
    ? item.show_title
    : item.year
    ? String(item.year)
    : null

  const episodeTag = isEpisode && item.season != null && item.episode != null
    ? formatEpisode(item.season, item.episode)
    : null

  return (
    <button
      className="group text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
      onClick={onClick}
      aria-label={item.title}
    >
      <div className="relative overflow-hidden rounded-lg aspect-[2/3] bg-muted mb-2">
        <MediaPoster
          src={posterSrc ?? null}
          alt={item.title}
          className="w-full h-full transition-transform group-hover:scale-105"
          fallbackLetter={item.show_title?.[0] ?? item.title[0]}
        />
        {/* kodi_state badge */}
        <div className="absolute bottom-1.5 left-1.5">
          <KodiStateBadge state={item.kodi_state} compact />
        </div>
      </div>

      {episodeTag && (
        <p className="text-xs text-primary font-medium">{episodeTag}</p>
      )}
      <p className="text-sm font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
        {isEpisode ? item.title : item.title}
      </p>
      {subtitle && (
        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{subtitle}</p>
      )}
      {item.date_added && item.date_added !== '1951-09-25' && (
        <p className="text-xs text-muted-foreground mt-0.5">
          {item.date_added.slice(0, 10)}
        </p>
      )}
    </button>
  )
}
