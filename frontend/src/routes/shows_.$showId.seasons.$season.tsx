import { useState } from 'react'
import { createFileRoute, useNavigate, Link } from '@tanstack/react-router'
import { useSeasonDetail, useShowDetail } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { KodiStateBadge } from '@/components/shared/KodiStateBadge'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Button } from '@/components/ui/button'
import { BookmarkPlus, Check, ChevronRight, Tv } from 'lucide-react'

export const Route = createFileRoute('/shows_/$showId/seasons/$season')({
  component: SeasonDetailPage,
})

function SeasonDetailPage() {
  const { showId, season: seasonParam } = Route.useParams()
  const navigate = useNavigate()
  const showIdNum = parseInt(showId, 10)
  const seasonNum = parseInt(seasonParam, 10)

  const { data: season, isLoading, isError } = useSeasonDetail(showIdNum, seasonNum)
  // Fetch show for breadcrumb title + tmdb_id
  const { data: show } = useShowDetail(showIdNum)
  const [addDialogOpen, setAddDialogOpen] = useState(false)

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={8} variant="row" /></div>
  if (isError || !season) return <div className="p-4 md:p-6"><ErrorState message="Season not found." /></div>

  const showTitle = show?.show_title ?? `Show ${showId}`

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4 flex-wrap">
        <Link to="/shows" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Tv size={14} />
          TV Shows
        </Link>
        <ChevronRight size={14} />
        <Link
          to="/shows/$showId"
          params={{ showId }}
          className="hover:text-foreground transition-colors line-clamp-1 max-w-xs"
        >
          {showTitle}
        </Link>
        <ChevronRight size={14} />
        <span className="text-foreground font-medium">Season {season.season}</span>
      </nav>

      {/* Season header */}
      <div className="flex items-start justify-between gap-4 mb-6 flex-wrap">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold">{showTitle} — Season {season.season}</h1>
          <span className="text-sm text-muted-foreground">
            {season.watched_episodes}/{season.total_episodes} watched
          </span>
        </div>
        {show?.tmdb_id && season.episodes.length > 0 && (
          <Button variant="outline" size="sm" onClick={() => setAddDialogOpen(true)}>
            <BookmarkPlus size={14} className="mr-1.5" />
            Add Season to History
          </Button>
        )}
      </div>

      {/* Episode list */}
      <div className="divide-y divide-border border border-border rounded-lg overflow-hidden">
        {season.episodes.map(ep => (
          <div
            key={ep.id}
            className="flex items-start gap-4 p-4 hover:bg-muted/50 cursor-pointer transition-colors group"
            onClick={() =>
              navigate({
                to: '/items/$itemId',
                params: { itemId: String(ep.id) },
              })
            }
          >
            {/* Episode thumb */}
            <div className="flex-shrink-0 w-24 aspect-video rounded bg-muted overflow-hidden">
              {ep.thumb_url ? (
                <MediaPoster
                  src={ep.thumb_url}
                  alt={ep.title}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-muted-foreground text-xs">
                  E{String(ep.episode).padStart(2, '0')}
                </div>
              )}
            </div>

            {/* Episode info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-muted-foreground font-mono">
                      E{String(ep.episode).padStart(2, '0')}
                    </span>
                    <span className="text-sm font-medium line-clamp-1 group-hover:text-primary transition-colors">
                      {ep.title}
                    </span>
                  </div>
                  {ep.release_date && (
                    <p className="text-xs text-muted-foreground mt-0.5">{ep.release_date}</p>
                  )}
                  {ep.plot && (
                    <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{ep.plot}</p>
                  )}
                </div>
                <div className="flex-shrink-0 flex items-center gap-2">
                  {ep.watched && (
                    <Check size={14} className="text-green-500" />
                  )}
                  <KodiStateBadge state={ep.kodi_state} compact />
                  <ChevronRight size={14} className="text-muted-foreground" />
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add Season to History dialog */}
      {show?.tmdb_id && (() => {
        const tmdbIdNum = parseInt(show.tmdb_id!, 10)
        const items: WatchedItemIn[] = season.episodes.map(ep => ({
          media_type: 'episode' as const,
          tmdb_id: tmdbIdNum,
          season: seasonNum,
          episode: ep.episode,
          watched_at: '',
        }))
        const releaseDates = new Map<string, string>()
        let missingReleaseDates = 0
        for (const ep of season.episodes) {
          if (ep.release_date) {
            releaseDates.set(`${seasonNum}-${ep.episode}`, ep.release_date)
          } else {
            missingReleaseDates++
          }
        }
        return (
          <AddToHistoryDialog
            open={addDialogOpen}
            onOpenChange={setAddDialogOpen}
            items={items}
            releaseDates={releaseDates}
            missingReleaseDates={missingReleaseDates}
            label={`Add Season ${seasonNum} (${items.length} episodes)`}
          />
        )
      })()}
    </div>
  )
}
