import { useState, useEffect } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQueries } from '@tanstack/react-query'
import { useTMDBTV } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { apiClient } from '@/api/client'
import { CastRow } from '@/components/shared/CastRow'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { ErrorState } from '@/components/shared/ErrorState'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { BookmarkPlus, ChevronRight, Loader2, Star, Tv } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { components } from '@/api/types'

type TMDBSeasonOut = components['schemas']['TMDBSeasonOut']

export const Route = createFileRoute('/tmdb/tv/$tmdbId')({
  component: TMDBTVPage,
})

function TMDBTVPage() {
  const { tmdbId } = Route.useParams()
  const id = parseInt(tmdbId, 10)
  const { data: show, isLoading, isError } = useTMDBTV(id)

  // --- Full show add state ---
  const [preparingFullShow, setPreparingFullShow] = useState(false)
  const [fullShowDialogOpen, setFullShowDialogOpen] = useState(false)
  const [fullShowItems, setFullShowItems] = useState<WatchedItemIn[]>([])
  const [fullShowDates, setFullShowDates] = useState<Map<string, string>>(new Map())
  const [fullShowMissing, setFullShowMissing] = useState(0)

  const regularSeasons = show?.seasons.filter(s => s.season > 0) ?? []
  const totalEpisodes = regularSeasons.reduce((sum, s) => sum + s.episode_count, 0)

  // Lazily fetch all season episode data when user initiates "Add Full Show"
  const seasonResults = useQueries({
    queries: preparingFullShow && regularSeasons.length > 0
      ? regularSeasons.map(s => ({
          queryKey: ['tmdbSeason', id, s.season] as const,
          queryFn: async (): Promise<TMDBSeasonOut> => {
            const { data, error } = await apiClient.GET(
              '/api/tmdb/tv/{tmdb_id}/season/{season_number}',
              { params: { path: { tmdb_id: id, season_number: s.season } } },
            )
            if (error) throw new Error(String(error))
            return data! as TMDBSeasonOut
          },
          staleTime: 24 * 60 * 60_000,
        }))
      : [],
  })

  const successCount = seasonResults.filter(r => r.isSuccess).length
  const errorCount = seasonResults.filter(r => r.isError).length

  useEffect(() => {
    if (!preparingFullShow) return
    if (regularSeasons.length === 0) {
      setPreparingFullShow(false)
      return
    }
    if (errorCount > 0) {
      setPreparingFullShow(false)
      return
    }
    if (successCount < regularSeasons.length) return

    // All seasons loaded — build items
    const allEps = seasonResults.flatMap(r => r.data!.episodes)
    const items: WatchedItemIn[] = allEps.map(ep => ({
      media_type: 'episode' as const,
      tmdb_id: id,
      season: ep.season,
      episode: ep.episode,
      watched_at: '',
    }))
    const dates = new Map<string, string>()
    let missing = 0
    for (const ep of allEps) {
      if (ep.air_date) {
        dates.set(`${ep.season}-${ep.episode}`, ep.air_date)
      } else {
        missing++
      }
    }
    setFullShowItems(items)
    setFullShowDates(dates)
    setFullShowMissing(missing)
    setPreparingFullShow(false)
    setFullShowDialogOpen(true)
  }, [preparingFullShow, successCount, errorCount, regularSeasons.length, id])

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={4} variant="card" /></div>
  if (isError || !show) return <div className="p-4 md:p-6"><ErrorState message="Show not found on TMDB." /></div>

  const year = show.first_air_date ? show.first_air_date.slice(0, 4) : null
  const heroSrc = show.backdrop_url ?? null

  // Filter to cast members that have a TMDB person id (needed for actor page links)
  const castWithIds = show.cast.filter(
    (p): p is typeof p & { tmdb_person_id: number } => p.tmdb_person_id != null,
  )

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4">
        <Tv size={14} />
        <span>TMDB</span>
        <ChevronRight size={14} />
        <span className="text-foreground font-medium line-clamp-1">{show.title}</span>
      </nav>

      {/* Hero backdrop — full-bleed cinematic banner */}
      {heroSrc && (
        <div className="relative -mx-4 md:-mx-6 h-96 md:h-[576px] overflow-hidden bg-background">
          <img src={heroSrc} alt="" className="w-full h-full object-contain" />
          <div className="absolute inset-x-0 bottom-0 h-[45%] bg-gradient-to-b from-transparent to-background" />
        </div>
      )}

      {/* Poster + metadata row */}
      <div className={cn(
        'flex gap-4 md:gap-6 mb-8',
        heroSrc ? 'relative z-10 -mt-14 md:-mt-20 items-end' : 'items-start',
      )}>
        <div className={cn(
          'shrink-0',
          heroSrc ? 'w-24 md:w-36 relative z-10' : 'w-32 sm:w-44',
        )}>
          <div className="rounded-lg overflow-hidden aspect-[2/3] bg-muted shadow-lg">
            <MediaPoster
              src={show.poster_url ?? null}
              alt={show.title}
              className="w-full h-full"
              fallbackLetter={show.title[0]}
            />
          </div>
        </div>

        <div className={cn('flex-1 min-w-0', heroSrc && 'pb-2')}>
          {/* Title — always visible */}
          <h1 className="text-2xl font-bold leading-tight mb-2">{show.title}</h1>

          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground mb-3">
            {year && <span>{year}</span>}
            {show.status && (
              <Badge variant="outline" className="text-xs">{show.status}</Badge>
            )}
            {show.vote_average != null && (
              <span className="flex items-center gap-1">
                <Star size={12} className="text-yellow-400 fill-yellow-400" />
                {show.vote_average.toFixed(1)}
              </span>
            )}
          </div>

          {show.genres.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {show.genres.map(g => (
                <Badge key={g} variant="outline" className="text-xs">{g}</Badge>
              ))}
            </div>
          )}

          {show.overview && (
            <p className="text-sm leading-relaxed text-foreground/80 mb-4 line-clamp-5">
              {show.overview}
            </p>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2 mt-4">
            <Button
              onClick={() => setPreparingFullShow(true)}
              disabled={preparingFullShow || totalEpisodes === 0}
            >
              {preparingFullShow ? (
                <>
                  <Loader2 size={15} className="mr-1.5 animate-spin" />
                  Loading…
                </>
              ) : (
                <>
                  <BookmarkPlus size={15} className="mr-1.5" />
                  Add Full Show ({totalEpisodes} episodes)
                </>
              )}
            </Button>
            {show.local_show_id && (
              <Button variant="outline" asChild>
                <Link to="/shows/$showId" params={{ showId: String(show.local_show_id) }}>
                  In Library →
                </Link>
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Cast with portrait images */}
      {castWithIds.length > 0 && (
        <div className="mb-8">
          <CastRow cast={castWithIds} />
        </div>
      )}

      {/* Season list */}
      <h2 className="text-lg font-semibold mb-4">Seasons</h2>
      <div className="space-y-2">
        {regularSeasons.map(season => (
          <Link
            key={season.season}
            to="/tmdb/tv/$tmdbId/season/$season"
            params={{ tmdbId, season: String(season.season) }}
            className="flex items-center justify-between gap-4 border border-border rounded-lg p-4 hover:bg-muted/50 transition-colors group"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-medium group-hover:text-primary transition-colors">
                  {season.title ?? `Season ${season.season}`}
                </span>
                <span className="text-xs text-muted-foreground">
                  {season.episode_count} episodes
                </span>
                {season.air_date && (
                  <span className="text-xs text-muted-foreground">
                    · {season.air_date.slice(0, 4)}
                  </span>
                )}
              </div>
            </div>
            <ChevronRight size={16} className="text-muted-foreground flex-shrink-0" />
          </Link>
        ))}
      </div>

      {/* Full show add dialog */}
      <AddToHistoryDialog
        open={fullShowDialogOpen}
        onOpenChange={setFullShowDialogOpen}
        items={fullShowItems}
        releaseDates={fullShowDates}
        missingReleaseDates={fullShowMissing}
        label={`Add Full Show (${fullShowItems.length} episodes)`}
      />
    </div>
  )
}
