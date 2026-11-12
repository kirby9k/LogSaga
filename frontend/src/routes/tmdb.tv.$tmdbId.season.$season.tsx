import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useTMDBSeason } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { ErrorState } from '@/components/shared/ErrorState'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { Button } from '@/components/ui/button'
import { BookmarkPlus, ChevronRight, Tv } from 'lucide-react'

export const Route = createFileRoute('/tmdb/tv/$tmdbId/season/$season')({
  component: TMDBSeasonPage,
})

function TMDBSeasonPage() {
  const { tmdbId, season: seasonParam } = Route.useParams()
  const id = parseInt(tmdbId, 10)
  const seasonNum = parseInt(seasonParam, 10)
  const { data: season, isLoading, isError } = useTMDBSeason(id, seasonNum)
  const [dialogOpen, setDialogOpen] = useState(false)

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={8} variant="row" /></div>
  if (isError || !season) return <div className="p-4 md:p-6"><ErrorState message="Season not found on TMDB." /></div>

  const items: WatchedItemIn[] = season.episodes.map(ep => ({
    media_type: 'episode' as const,
    tmdb_id: id,
    season: ep.season,
    episode: ep.episode,
    watched_at: '',
  }))

  const releaseDates = new Map<string, string>()
  let missingReleaseDates = 0
  for (const ep of season.episodes) {
    if (ep.air_date) {
      releaseDates.set(`${ep.season}-${ep.episode}`, ep.air_date)
    } else {
      missingReleaseDates++
    }
  }

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4 flex-wrap">
        <Tv size={14} />
        <span>TMDB</span>
        <ChevronRight size={14} />
        <Link
          to="/tmdb/tv/$tmdbId"
          params={{ tmdbId }}
          className="hover:text-foreground transition-colors"
        >
          TV Show
        </Link>
        <ChevronRight size={14} />
        <span className="text-foreground font-medium">Season {season.season}</span>
      </nav>

      {/* Season header */}
      <div className="flex items-start gap-4 mb-6">
        {season.poster_url && (
          <div className="flex-shrink-0 w-24 aspect-[2/3] rounded-lg overflow-hidden bg-muted">
            <MediaPoster
              src={season.poster_url}
              alt={season.title}
              className="w-full h-full"
            />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold mb-1">{season.title}</h1>
          {season.air_date && (
            <p className="text-sm text-muted-foreground mb-2">{season.air_date.slice(0, 4)}</p>
          )}
          {season.overview && (
            <p className="text-sm text-muted-foreground leading-relaxed mb-3 line-clamp-3">
              {season.overview}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setDialogOpen(true)} disabled={items.length === 0}>
              <BookmarkPlus size={15} className="mr-1.5" />
              Add Season ({items.length} episodes)
            </Button>
            {season.local_show_id && (
              <Button variant="outline" asChild>
                <Link to="/shows/$showId" params={{ showId: String(season.local_show_id) }}>
                  In Library →
                </Link>
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Episode list */}
      <div className="divide-y divide-border border border-border rounded-lg overflow-hidden">
        {season.episodes.map(ep => (
          <div key={ep.episode} className="flex items-start gap-4 p-4">
            {/* Still image */}
            <div className="flex-shrink-0 w-24 aspect-video rounded bg-muted overflow-hidden">
              {ep.still_url ? (
                <img
                  src={ep.still_url}
                  alt={ep.title}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-muted-foreground text-xs">
                  E{String(ep.episode).padStart(2, '0')}
                </div>
              )}
            </div>

            {/* Episode info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                <span className="text-xs text-muted-foreground font-mono">
                  E{String(ep.episode).padStart(2, '0')}
                </span>
                <span className="text-sm font-medium line-clamp-1">{ep.title}</span>
              </div>
              <div className="flex gap-3 text-xs text-muted-foreground">
                {ep.air_date && <span>{ep.air_date}</span>}
                {ep.runtime != null && <span>{ep.runtime}m</span>}
              </div>
              {ep.overview && (
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{ep.overview}</p>
              )}
            </div>
          </div>
        ))}
      </div>

      <AddToHistoryDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        items={items}
        releaseDates={releaseDates}
        missingReleaseDates={missingReleaseDates}
        label={`Add Season ${season.season} (${items.length} episodes)`}
      />
    </div>
  )
}
