import { formatDistanceToNow, format, parseISO } from 'date-fns'

/** Convert seconds to "1h 24m" or "43m" */
export function formatRuntime(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

/** Convert 0-100 float to "87%" */
export function formatPercent(pct: number | null | undefined): string {
  if (pct == null) return '—'
  return `${Math.round(pct)}%`
}

/** ISO string → "2 days ago" */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true })
  } catch {
    return '—'
  }
}

/** ISO string → "Nov 4, 2026 11:30 PM" */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return format(parseISO(iso), 'MMM d, yyyy h:mm a')
  } catch {
    return '—'
  }
}

/** season+episode → "S02E05" */
export function formatEpisode(season: number | null | undefined, episode: number | null | undefined): string {
  if (season == null || episode == null) return ''
  return `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`
}

/** Build a display title: "Show Name S02E05 · Episode Title" or just title */
export function formatItemTitle(
  title: string,
  showTitle: string | null | undefined,
  season: number | null | undefined,
  episode: number | null | undefined,
): string {
  if (showTitle && season != null && episode != null) {
    return `${showTitle} ${formatEpisode(season, episode)} · ${title}`
  }
  return title
}
