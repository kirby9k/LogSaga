import { createFileRoute, Link } from '@tanstack/react-router'
import { useInProgress } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PercentBar } from '@/components/shared/PercentBar'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { EmptyState } from '@/components/shared/EmptyState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { formatRuntime, formatEpisode } from '@/lib/format'

export const Route = createFileRoute('/continue')({
  component: ContinuePage,
})

function ContinuePage() {
  const { data, isLoading, isError } = useInProgress()

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Continue Watching"
        subtitle="Items with an active resume point in Kodi"
      />

      {isLoading && <LoadingSkeleton count={4} variant="row" />}
      {isError && <ErrorState />}
      {data && data.length === 0 && (
        <EmptyState message="Nothing to resume right now." />
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map(({ item, resume_pos_s, resume_total_s, percent }) => (
            <Link
              key={item.id}
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
                <PercentBar percent={percent} className="mt-2 max-w-xs" />
                {resume_pos_s != null && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Resume at {formatRuntime(resume_pos_s)}
                    {resume_total_s != null && ` of ${formatRuntime(resume_total_s)}`}
                  </p>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
