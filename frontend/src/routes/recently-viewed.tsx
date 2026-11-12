import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useRecentlyViewed } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import type { components } from '@/api/types'

type RecentlyViewedOut = components['schemas']['RecentlyViewedOut']

export const Route = createFileRoute('/recently-viewed')({
  component: RecentlyViewedPage,
})

function RecentlyViewedPage() {
  const navigate = useNavigate()
  const { data: items, isLoading, isError } = useRecentlyViewed(50)

  function handleClick(item: RecentlyViewedOut) {
    if (item.media_type === 'movie') {
      navigate({ to: '/tmdb/movie/$tmdbId', params: { tmdbId: String(item.tmdb_id) } })
    } else {
      navigate({ to: '/tmdb/tv/$tmdbId', params: { tmdbId: String(item.tmdb_id) } })
    }
  }

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Recently Viewed"
        subtitle={items ? `${items.length} items` : undefined}
      />

      {isLoading && <LoadingSkeleton count={12} variant="card" />}
      {isError && <ErrorState message="Failed to load recently viewed." />}
      {!isLoading && !isError && (!items || items.length === 0) && (
        <EmptyState message="No recently viewed TMDB pages yet. Use search (Ctrl+K) to browse." />
      )}

      {items && items.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
          {items.map(item => (
            <button
              key={item.id}
              className="group text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-lg"
              onClick={() => handleClick(item)}
              aria-label={item.title}
            >
              <div className="relative overflow-hidden rounded-lg aspect-[2/3] bg-muted mb-2">
                <MediaPoster
                  src={item.poster_url ?? null}
                  alt={item.title}
                  className="w-full h-full transition-transform group-hover:scale-105"
                  fallbackLetter={item.title[0]}
                />
                <div className="absolute top-1.5 right-1.5">
                  <span className="text-xs bg-black/60 text-white px-1.5 py-0.5 rounded">
                    {item.media_type === 'movie' ? 'Movie' : 'TV'}
                  </span>
                </div>
              </div>
              <p className="text-sm font-medium leading-snug line-clamp-2 group-hover:text-primary transition-colors">
                {item.title}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {new Date(item.viewed_at).toLocaleDateString()}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
