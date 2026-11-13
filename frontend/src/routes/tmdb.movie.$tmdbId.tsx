import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useTMDBMovie } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { CastRow } from '@/components/shared/CastRow'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { ErrorState } from '@/components/shared/ErrorState'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { BookmarkPlus, ChevronRight, Film, Star } from 'lucide-react'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/tmdb/movie/$tmdbId')({
  component: TMDBMoviePage,
})

function TMDBMoviePage() {
  const { tmdbId } = Route.useParams()
  const id = parseInt(tmdbId, 10)
  const { data: movie, isLoading, isError } = useTMDBMovie(id)
  const [dialogOpen, setDialogOpen] = useState(false)

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={4} variant="card" /></div>
  if (isError || !movie) return <div className="p-4 md:p-6"><ErrorState message="Movie not found on TMDB." /></div>

  const year = movie.release_date ? new Date(movie.release_date).getFullYear() : null
  const runtime = movie.runtime ? `${movie.runtime}m` : null
  const heroSrc = movie.backdrop_url ?? null

  // Filter to cast members that have a TMDB person id (needed for actor page links)
  const castWithIds = movie.cast.filter(
    (p): p is typeof p & { tmdb_person_id: number } => p.tmdb_person_id != null,
  )

  const items: WatchedItemIn[] = [{
    media_type: 'movie',
    tmdb_id: movie.tmdb_id,
    watched_at: '',
  }]

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm text-muted-foreground mb-4">
        <Film size={14} />
        <span>TMDB</span>
        <ChevronRight size={14} />
        <span className="text-foreground font-medium line-clamp-1">{movie.title}</span>
      </nav>

      {/* Hero backdrop — full-bleed, letterboxed (black bars preserve aspect ratio) */}
      {heroSrc && (
        <div className="relative -mx-4 md:-mx-6 h-36 md:h-48 overflow-hidden bg-black">
          <img src={heroSrc} alt="" className="w-full h-full object-contain" />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-background/40" />
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
              src={movie.poster_url ?? null}
              alt={movie.title}
              className="w-full h-full"
              fallbackLetter={movie.title[0]}
            />
          </div>
        </div>

        <div className={cn('flex-1 min-w-0', heroSrc && 'pb-2')}>
          {/* Title — always visible */}
          <h1 className="text-2xl font-bold leading-tight mb-2">{movie.title}</h1>

          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground mb-3">
            {year && <span>{year}</span>}
            {runtime && <span>{runtime}</span>}
            {movie.vote_average != null && (
              <span className="flex items-center gap-1">
                <Star size={12} className="text-yellow-400 fill-yellow-400" />
                {movie.vote_average.toFixed(1)}
              </span>
            )}
          </div>

          {movie.genres.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {movie.genres.map(g => (
                <Badge key={g} variant="outline" className="text-xs">{g}</Badge>
              ))}
            </div>
          )}

          {movie.overview && (
            <p className="text-sm leading-relaxed text-foreground/80 mb-4 line-clamp-5">
              {movie.overview}
            </p>
          )}

          {movie.director && (
            <p className="text-sm text-muted-foreground mb-3">
              <span className="font-medium text-foreground">Director:</span> {movie.director}
            </p>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2 mt-4">
            <Button onClick={() => setDialogOpen(true)}>
              <BookmarkPlus size={15} className="mr-1.5" />
              Add to History
            </Button>
            {movie.local_id && (
              <Button variant="outline" asChild>
                <Link to="/items/$itemId" params={{ itemId: String(movie.local_id) }}>
                  In Library →
                </Link>
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Cast with portrait images */}
      {castWithIds.length > 0 && (
        <CastRow cast={castWithIds} />
      )}

      <AddToHistoryDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        items={items}
        releaseDate={movie.release_date ?? null}
        label="Add to History"
      />
    </div>
  )
}
