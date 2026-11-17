/**
 * Rating display components for Kodi-sourced community and personal ratings.
 *
 * GridRating — compact single-line display for grid cards (best available + userrating)
 * RatingsRow — full breakdown for detail pages (all sources with vote counts)
 */

export type RatingFields = {
  rating_imdb?: number | null
  votes_imdb?: number | null
  rating_tmdb?: number | null
  votes_tmdb?: number | null
  rating_trakt?: number | null
  votes_trakt?: number | null
  userrating?: number | null
}

function formatVotes(votes: number): string {
  if (votes >= 1_000_000) return `${(votes / 1_000_000).toFixed(1)}M`
  if (votes >= 1_000) return `${Math.round(votes / 1_000)}K`
  return votes.toString()
}

/**
 * Shows the best available community rating + personal userrating.
 * Intended for compact grid card footers.
 * Source priority for community rating: IMDb → TMDB → Trakt
 */
export function GridRating({ item }: { item: RatingFields }) {
  const communityValue = item.rating_imdb ?? item.rating_tmdb ?? item.rating_trakt
  const communitySource =
    item.rating_imdb != null ? 'IMDb' :
    item.rating_tmdb != null ? 'TMDB' :
    item.rating_trakt != null ? 'Trakt' : null

  if (communityValue == null && !item.userrating) return null

  return (
    <div className="flex gap-2 text-xs text-muted-foreground">
      {communityValue != null && communitySource && (
        <span>{communitySource} {communityValue.toFixed(1)}</span>
      )}
      {item.userrating != null && item.userrating > 0 && (
        <span className="text-amber-400">★ {item.userrating}</span>
      )}
    </div>
  )
}

/**
 * Shows all available rating sources with vote counts.
 * Intended for item and show detail pages.
 */
export function RatingsRow({ item }: { item: RatingFields }) {
  type Source = { label: string; value: number; votes?: number | null }
  const sources: Source[] = []
  if (item.rating_imdb != null) sources.push({ label: 'IMDb', value: item.rating_imdb, votes: item.votes_imdb })
  if (item.rating_tmdb != null) sources.push({ label: 'TMDB', value: item.rating_tmdb, votes: item.votes_tmdb })
  if (item.rating_trakt != null) sources.push({ label: 'Trakt', value: item.rating_trakt, votes: item.votes_trakt })

  const hasUserRating = item.userrating != null && item.userrating > 0

  if (sources.length === 0 && !hasUserRating) return null

  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {sources.map(s => (
        <span key={s.label} className="text-muted-foreground">
          <span className="text-foreground font-medium">{s.label}</span>{' '}
          {s.value.toFixed(1)}
          {s.votes != null && s.votes > 0 && (
            <span className="text-xs"> ({formatVotes(s.votes)})</span>
          )}
        </span>
      ))}
      {hasUserRating && (
        <span className="text-amber-400 font-medium">
          ★ {item.userrating}/10
        </span>
      )}
    </div>
  )
}
