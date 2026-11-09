export const STATUS_LABELS: Record<string, string> = {
  completed: 'Watched',
  partial: 'Partial',
  open: 'In Progress',
  ignored: 'Ignored',
}

export const COMPLETION_REASON_LABELS: Record<string, string> = {
  position: 'Reached end',
  kodi_playcount: 'Kodi auto-mark',
  kodi_marked: 'Marked watched',
}

export const KODI_STATE_LABELS: Record<string, string> = {
  watched: 'Watched',
  unwatched: 'Unwatched',
  in_progress: 'In Progress',
  removed: 'Removed',
}

export const MEDIA_TYPE_LABELS: Record<string, string> = {
  movie: 'Movie',
  episode: 'Episode',
}
