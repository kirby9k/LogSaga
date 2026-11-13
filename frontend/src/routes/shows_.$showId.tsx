import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useShowDetail } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { CastRow } from '@/components/shared/CastRow'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { PercentBar } from '@/components/shared/PercentBar'
import { KodiStateBadge } from '@/components/shared/KodiStateBadge'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { BookmarkPlus, Check, ChevronDown, ChevronRight, Tv } from 'lucide-react'
import { format, parseISO } from 'date-fns'
import { formatEpisode } from '@/lib/format'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/shows_/$showId')({
  component: ShowDetailPage,
})

function ShowDetailPage() {
  const { showId } = Route.useParams()
  const id = parseInt(showId, 10)
  const { data: show, isLoading, isError } = useShowDetail(id)
  const [openSeasons, setOpenSeasons] = useState<Record<number, boolean>>({})
  const [addDialogOpen, setAddDialogOpen] = useState(false)

  const toggleSeason = (seasonNum: number) =>
    setOpenSeasons(prev => ({ ...prev, [seasonNum]: !prev[seasonNum] }))

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={4} variant="card" /></div>
  if (isError || !show) return <div className="p-4 md:p-6"><ErrorState message="Show not found." /></div>

  const pct = show.total_episodes > 0
    ? (show.watched_episodes / show.total_episodes) * 100
    : 0

  const heroSrc = show.fanart_url ?? null

  // Recently played episodes (have last_session_at), sorted newest first
  const recentEpisodes = show.seasons
    .flatMap(s => s.episodes.map(e => ({ ...e, season: s.season })))
    .filter(e => e.last_session_at != null)
    .sort((a, b) => (b.last_session_at! > a.last_session_at! ? 1 : -1))
    .slice(0, 5)

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4">
        <Link to="/shows" className="flex items-center gap-1 hover:text-foreground transition-colors">
          <Tv size={14} />
          TV Shows
        </Link>
        <ChevronRight size={14} />
        <span className="text-foreground font-medium line-clamp-1">{show.show_title}</span>
      </nav>

      {/* Hero backdrop — full-bleed cinematic banner */}
      {heroSrc && (
        <div className="relative -mx-4 md:-mx-6 h-64 md:h-96 overflow-hidden">
          <img src={heroSrc} alt="" className="w-full h-full object-cover object-[50%_65%]" />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-background/60" />
        </div>
      )}

      {/* Poster + metadata row */}
      <div className={cn(
        'flex gap-4 md:gap-6 mb-8',
        heroSrc ? '-mt-10 md:-mt-14 items-end' : 'items-start',
      )}>
        <div className={cn(
          'shrink-0',
          heroSrc ? 'w-24 md:w-36 relative z-10' : 'w-32 sm:w-44',
        )}>
          <div className="rounded-lg overflow-hidden aspect-[2/3] bg-muted shadow-lg">
            <MediaPoster
              src={show.poster_url}
              alt={show.show_title}
              className="w-full h-full"
              fallbackLetter={show.show_title[0]}
            />
          </div>
        </div>

        <div className={cn('flex-1 min-w-0', heroSrc && 'pb-2')}>
          {/* Show title — always visible */}
          <div className="flex flex-wrap items-start gap-2 mb-1">
            <h1 className="text-2xl font-bold leading-tight">{show.show_title}</h1>
            <KodiStateBadge state={show.kodi_state} />
          </div>

          {/* Meta row */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground mb-3">
            {show.year && <span>{show.year}</span>}
            {show.premiered && show.premiered !== show.year?.toString() && (
              <span>Premiered {show.premiered}</span>
            )}
            {show.studio && <span>{show.studio}</span>}
            {show.status && (
              <Badge variant="outline" className="text-xs">{show.status}</Badge>
            )}
          </div>

          {show.genres && (
            <p className="text-sm text-muted-foreground mb-3">{show.genres}</p>
          )}

          {show.plot && (
            <p className="text-sm leading-relaxed text-foreground/80 mb-4 line-clamp-4">
              {show.plot}
            </p>
          )}

          {/* Progress */}
          <div className="mb-2">
            <div className="flex justify-between text-sm mb-1">
              <span className="text-muted-foreground">
                {show.watched_episodes} / {show.total_episodes} episodes watched
              </span>
              <span className="font-medium">{Math.round(pct)}%</span>
            </div>
            <PercentBar percent={pct} showLabel={false} />
          </div>

          {/* External IDs */}
          <div className="flex flex-wrap gap-2 mt-3">
            {show.tvdb_id && (
              <a href={`https://www.thetvdb.com/series/${show.tvdb_id}`} target="_blank" rel="noreferrer">
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">TVDB {show.tvdb_id}</Badge>
              </a>
            )}
            {show.imdb_id && (
              <a href={`https://www.imdb.com/title/${show.imdb_id}`} target="_blank" rel="noreferrer">
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">{show.imdb_id}</Badge>
              </a>
            )}
            {show.tmdb_id && (
              <a href={`https://www.themoviedb.org/tv/${show.tmdb_id}`} target="_blank" rel="noreferrer">
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">TMDB {show.tmdb_id}</Badge>
              </a>
            )}
          </div>

          {/* Add unwatched episodes to history */}
          {show.tmdb_id && (() => {
            const unwatched = show.seasons
              .filter(s => s.season > 0)
              .flatMap(s => s.episodes.filter(e => !e.watched).map(e => ({ s, e })))
            if (unwatched.length === 0) return null
            return (
              <div className="mt-3">
                <Button variant="outline" size="sm" onClick={() => setAddDialogOpen(true)}>
                  <BookmarkPlus size={14} className="mr-1.5" />
                  Add {unwatched.length} Unwatched to History
                </Button>
              </div>
            )
          })()}
        </div>
      </div>

      {/* Cast */}
      {show.cast.length > 0 && (
        <div className="mb-8">
          <CastRow cast={show.cast} />
        </div>
      )}

      {/* Seasons — accordion */}
      <h2 className="text-lg font-semibold mb-4">Seasons</h2>
      <div className="space-y-2">
        {show.seasons.map(season => {
          const isOpen = !!openSeasons[season.season]
          const seasonPct = season.total_episodes > 0
            ? (season.watched_episodes / season.total_episodes) * 100
            : 0
          return (
            <Collapsible
              key={season.season}
              open={isOpen}
              onOpenChange={() => toggleSeason(season.season)}
            >
              <CollapsibleTrigger asChild>
                <div className="border border-border rounded-lg p-4 hover:bg-muted/50 cursor-pointer transition-colors group select-none">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium group-hover:text-primary transition-colors">
                          Season {season.season}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {season.watched_episodes}/{season.total_episodes} watched
                        </span>
                      </div>
                      <PercentBar percent={seasonPct} showLabel={false} />
                    </div>
                    {isOpen
                      ? <ChevronDown size={16} className="text-muted-foreground flex-shrink-0" />
                      : <ChevronRight size={16} className="text-muted-foreground flex-shrink-0" />
                    }
                  </div>
                </div>
              </CollapsibleTrigger>

              <CollapsibleContent>
                <div className="border border-border border-t-0 rounded-b-lg px-4 pb-3 -mt-1">
                  {/* Episode list */}
                  <div className="divide-y divide-border">
                    {season.episodes.map(ep => (
                      <Link
                        key={ep.id}
                        to="/items/$itemId"
                        params={{ itemId: String(ep.id) }}
                        className="flex items-center gap-3 py-2 hover:bg-muted/30 -mx-4 px-4 transition-colors"
                      >
                        <span className="text-xs text-muted-foreground w-8 shrink-0 font-mono">
                          {formatEpisode(season.season, ep.episode)}
                        </span>
                        <span className="text-sm flex-1 leading-snug line-clamp-1">{ep.title}</span>
                        {ep.runtime_s != null && (
                          <span className="text-xs text-muted-foreground shrink-0">
                            {Math.round(ep.runtime_s / 60)}m
                          </span>
                        )}
                        {ep.watched && (
                          <Check size={13} className="text-green-500 shrink-0" />
                        )}
                      </Link>
                    ))}
                  </div>

                  {/* Link to full Season Detail page */}
                  <div className="pt-3">
                    <Link
                      to="/shows/$showId/seasons/$season"
                      params={{ showId, season: String(season.season) }}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
                    >
                      View full season
                      <ChevronRight size={12} />
                    </Link>
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          )
        })}
      </div>

      {/* Next unwatched hint */}
      {show.seasons.some(s => s.episodes.some(e => !e.watched)) && (() => {
        const nextEp = show.seasons
          .flatMap(s => s.episodes.map(e => ({ ...e, season: s.season })))
          .find(e => !e.watched)
        if (!nextEp) return null
        return (
          <div className="mt-6 p-4 bg-muted/50 rounded-lg border border-border">
            <p className="text-sm text-muted-foreground">
              Next up:{' '}
              <span className="font-medium text-foreground">
                {formatEpisode(nextEp.season, nextEp.episode)} — {nextEp.title}
              </span>
            </p>
          </div>
        )
      })()}

      {/* Recently Played episodes */}
      {recentEpisodes.length > 0 && (
        <section className="mt-8">
          <h3 className="text-base font-semibold mb-3">Recently Played</h3>
          <div className="flex gap-3 overflow-x-auto pb-2">
            {recentEpisodes.map(ep => (
              <Link
                key={ep.id}
                to="/items/$itemId"
                params={{ itemId: String(ep.id) }}
                className="shrink-0 w-40 group"
              >
                <div className="aspect-video rounded bg-muted overflow-hidden mb-1 relative">
                  {ep.thumb_url ? (
                    <img
                      src={ep.thumb_url}
                      alt={ep.title}
                      className="w-full h-full object-cover group-hover:opacity-90 transition"
                    />
                  ) : (
                    <div className="w-full h-full bg-muted" />
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/70 to-transparent" />
                  <div className="absolute bottom-1 left-1.5 right-1.5">
                    <p className="text-white text-xs font-medium line-clamp-1">{ep.title}</p>
                    <p className="text-white/70 text-xs">{formatEpisode(ep.season, ep.episode)}</p>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {format(parseISO(ep.last_session_at!), 'd MMM')}
                </p>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Add to History dialog */}
      {show.tmdb_id && (() => {
        const tmdbIdNum = parseInt(show.tmdb_id!, 10)
        const items: WatchedItemIn[] = show.seasons
          .filter(s => s.season > 0)
          .flatMap(s => s.episodes
            .filter(e => !e.watched)
            .map(e => ({
              media_type: 'episode' as const,
              tmdb_id: tmdbIdNum,
              season: s.season,
              episode: e.episode,
              watched_at: '',
            }))
          )
        const releaseDates = new Map<string, string>()
        let missingReleaseDates = 0
        for (const s of show.seasons.filter(s => s.season > 0)) {
          for (const e of s.episodes.filter(e => !e.watched)) {
            if (e.release_date) {
              releaseDates.set(`${s.season}-${e.episode}`, e.release_date)
            } else {
              missingReleaseDates++
            }
          }
        }
        return (
          <AddToHistoryDialog
            open={addDialogOpen}
            onOpenChange={setAddDialogOpen}
            items={items}
            releaseDates={releaseDates}
            missingReleaseDates={missingReleaseDates}
            label={`Add ${items.length} Episodes to History`}
          />
        )
      })()}
    </div>
  )
}
