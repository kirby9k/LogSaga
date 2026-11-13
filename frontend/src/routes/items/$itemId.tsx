import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useItem, useHideSession } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import { AddToHistoryDialog } from '@/components/shared/AddToHistoryDialog'
import { CastRow } from '@/components/shared/CastRow'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { PercentBar } from '@/components/shared/PercentBar'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { RuntimeDisplay } from '@/components/shared/RuntimeDisplay'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { BookmarkPlus, ChevronDown, Trash2 } from 'lucide-react'
import { format, parseISO } from 'date-fns'
import { formatEpisode, formatDate, formatRuntime } from '@/lib/format'
import { KODI_STATE_LABELS, COMPLETION_REASON_LABELS } from '@/lib/constants'
import { cn } from '@/lib/utils'
import type { components } from '@/api/types'

type SessionOut = components['schemas']['SessionOut']
type ObservationOut = components['schemas']['ObservationOut']

export const Route = createFileRoute('/items/$itemId')({
  component: ItemDetailPage,
})

// ---------------------------------------------------------------------------
// Technical metadata helpers
// ---------------------------------------------------------------------------
function resolutionLabel(w: number | null | undefined, h: number | null | undefined): string | null {
  if (!w || !h) return null
  if (w >= 3840 || h >= 2160) return '4K UHD'
  if (w >= 2560 || h >= 1440) return '1440p'
  if (w >= 1920 || h >= 1080) return '1080p'
  if (w >= 1280 || h >= 720) return '720p'
  return `${w}×${h}`
}

const AUDIO_CODEC_NAMES: Record<string, string> = {
  dca: 'DTS',
  ac3: 'Dolby Digital',
  eac3: 'Dolby Digital Plus',
  truehd: 'Dolby TrueHD',
  aac: 'AAC',
  flac: 'FLAC',
}

function formatAudio(codec: string | null | undefined, channels: number | null | undefined): string | null {
  if (!codec) return null
  const name = AUDIO_CODEC_NAMES[codec.toLowerCase()] ?? codec.toUpperCase()
  if (!channels) return name
  // Map channel count to label: 8→7.1, 7→6.1, 6→5.1, 2→Stereo, 1→Mono
  const ch = channels === 8 ? '7.1' : channels === 7 ? '6.1' : channels === 6 ? '5.1'
    : channels === 2 ? 'Stereo' : channels === 1 ? 'Mono' : `${channels}ch`
  return `${name} ${ch}`
}

function ItemDetailPage() {
  const { itemId } = Route.useParams()
  const id = Number(itemId)
  const { data, isLoading, isError } = useItem(id)
  const [addDialogOpen, setAddDialogOpen] = useState(false)

  if (isLoading) {
    return (
      <div className="p-4 md:p-6">
        <LoadingSkeleton count={1} variant="card" />
      </div>
    )
  }
  if (isError || !data) {
    return <div className="p-4 md:p-6"><ErrorState message="Item not found." /></div>
  }

  const displayTitle = data.show_title && data.season != null && data.episode != null
    ? `${formatEpisode(data.season, data.episode)} · ${data.title}`
    : data.title

  // Preference-ordered poster selection (portrait 2:3 slot)
  const EP_POSTER_PREF = ['season.poster', 'tvshow.poster']
  const MOVIE_POSTER_PREF = ['poster', 'fanart']
  const posterPref = data.media_type === 'episode' ? EP_POSTER_PREF : MOVIE_POSTER_PREF
  const posterArt = posterPref.reduce<typeof data.artwork[0] | null>(
    (found, type) => found ?? data.artwork.find(a => a.art_type === type) ?? null,
    null,
  )
  const posterSrc = posterArt?.local_path ? `/art/${posterArt.local_path}` : null

  // Episode screencap (16:9) shown below the info block
  const thumbArt = data.media_type === 'episode'
    ? data.artwork.find(a => a.art_type === 'thumb')
    : null

  // Fanart becomes the hero backdrop
  const fanartArt = data.artwork.find(a =>
    a.art_type === (data.media_type === 'episode' ? 'tvshow.fanart' : 'fanart')
  )
  const heroSrc = fanartArt?.local_path ? `/art/${fanartArt.local_path}` : null

  return (
    <div className="p-4 md:p-6">
      {/* Hero backdrop — full-bleed cinematic banner */}
      {heroSrc && (
        <div className="relative -mx-4 md:-mx-6 -mt-4 md:-mt-6 h-64 md:h-96 overflow-hidden">
          <img src={heroSrc} alt="" className="w-full h-full object-cover object-[50%_65%]" />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent to-background/60" />
        </div>
      )}

      {/* Poster + info row */}
      <div className={cn(
        'flex gap-4 md:gap-6',
        heroSrc ? '-mt-10 md:-mt-14 items-end' : 'flex-col md:flex-row',
      )}>
        {/* Poster */}
        <div className={cn(
          'shrink-0',
          heroSrc
            ? 'w-24 md:w-36 relative z-10'
            : 'w-full md:w-48',
        )}>
          <div className={cn(
            'overflow-hidden rounded-lg aspect-[2/3] bg-muted shadow-lg',
            !heroSrc && 'md:w-48',
          )}>
            <MediaPoster src={posterSrc} alt={data.title} className="w-full h-full" />
          </div>
        </div>

        {/* Info */}
        <div className={cn('flex-1 min-w-0 space-y-4', heroSrc && 'pb-2')}>
          {/* Title */}
          <div>
            <h2 className="text-xl font-semibold">{displayTitle}</h2>
            {data.show_title && data.media_type === 'episode' && (
              <p className="text-muted-foreground">
                {data.show_id ? (
                  <Link
                    to="/shows/$showId"
                    params={{ showId: String(data.show_id) }}
                    className="hover:text-foreground hover:underline transition-colors"
                  >
                    {data.show_title}
                  </Link>
                ) : (
                  data.show_title
                )}
              </p>
            )}
          </div>

          <div className="flex flex-wrap gap-2 items-center">
            <Badge variant="outline">{data.media_type === 'episode' ? 'TV' : 'Movie'}</Badge>
            {data.year && <Badge variant="outline">{data.year}</Badge>}
            <Badge variant="secondary">
              {KODI_STATE_LABELS[data.kodi_state] ?? data.kodi_state}
            </Badge>
            {data.runtime_s && (
              <span className="text-sm text-muted-foreground">
                <RuntimeDisplay seconds={data.runtime_s} />
              </span>
            )}
          </div>

          {data.plot && (
            <p className="text-sm text-muted-foreground leading-relaxed line-clamp-4">
              {data.plot}
            </p>
          )}

          {data.genres && (
            <div className="flex flex-wrap gap-1">
              {data.genres.split('|').map(g => (
                <Badge key={g} variant="outline" className="text-xs">{g.trim()}</Badge>
              ))}
            </div>
          )}

          {/* Technical metadata chips — only shown when data is available */}
          {(() => {
            const chips: string[] = []
            if (data.release_date) {
              try {
                const dateStr = format(parseISO(data.release_date), 'd MMMM yyyy')
                const label = data.media_type === 'episode' ? `First aired: ${dateStr}` : dateStr
                chips.push(label)
              } catch { /* skip */ }
            }
            if (data.media_type === 'movie' && data.director) chips.push(`Director: ${data.director}`)
            const res = resolutionLabel(data.video_width, data.video_height)
            if (res) chips.push(res)
            if (data.video_codec) chips.push(data.video_codec.toUpperCase())
            if (data.hdr_type) chips.push(data.hdr_type)
            const audio = formatAudio(data.audio_codec, data.audio_channels)
            if (audio) chips.push(audio)
            if (chips.length === 0) return null
            return (
              <div className="flex flex-wrap gap-1">
                {chips.map(chip => (
                  <Badge key={chip} variant="outline" className="text-xs">{chip}</Badge>
                ))}
              </div>
            )
          })()}

          {/* External IDs */}
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            {data.tmdb_id && (
              <a
                href={`https://www.themoviedb.org/${data.media_type === 'episode' ? 'tv' : 'movie'}/${data.tmdb_id}`}
                target="_blank"
                rel="noreferrer"
                className="hover:text-foreground underline"
              >
                TMDB
              </a>
            )}
            {data.imdb_id && (
              <a
                href={`https://www.imdb.com/title/${data.imdb_id}`}
                target="_blank"
                rel="noreferrer"
                className="hover:text-foreground underline"
              >
                IMDb
              </a>
            )}
          </div>

          {/* Add to History */}
          {data.tmdb_id && (
            <div className="pt-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setAddDialogOpen(true)}
              >
                <BookmarkPlus size={14} className="mr-1.5" />
                Add to History
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Cast */}
      {data.cast.length > 0 && (
        <div className="mt-6">
          <CastRow cast={data.cast} />
        </div>
      )}

      {/* Episode screencap — 16:9, shown below the info block */}
      {thumbArt?.local_path && (
        <div className="mt-6 aspect-video overflow-hidden rounded-lg bg-muted">
          <img
            src={`/art/${thumbArt.local_path}`}
            alt="Episode thumbnail"
            className="w-full h-full object-cover"
          />
        </div>
      )}

      <Separator className="my-6" />

      {/* Viewing History — collapsible, open by default */}
      <section>
        <Collapsible defaultOpen>
          <CollapsibleTrigger className="flex items-center gap-2 text-base font-semibold hover:text-primary transition-colors mb-3">
            <ChevronDown size={16} className="transition-transform [&[data-state=open]]:rotate-180" />
            Viewing History ({data.sessions.length} {data.sessions.length === 1 ? 'play' : 'plays'})
          </CollapsibleTrigger>
          <CollapsibleContent>
            {data.sessions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No sessions recorded.</p>
            ) : (
              <div className="space-y-2">
                {data.sessions.map(s => (
                  <SessionCard key={s.id} session={s} />
                ))}
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>
      </section>

      {/* Observations — collapsed by default */}
      {data.observations.length > 0 && (
        <>
          <Separator className="my-6" />
          <Collapsible>
            <CollapsibleTrigger className="flex items-center gap-2 text-sm font-medium hover:text-primary transition-colors">
              <ChevronDown size={16} className="transition-transform [&[data-state=open]]:rotate-180" />
              Raw Observations ({data.observations.length})
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-3">
              <ObservationsTable observations={data.observations} />
            </CollapsibleContent>
          </Collapsible>
        </>
      )}

      {/* Add to History dialog */}
      {data.tmdb_id && (() => {
        const tmdbIdNum = parseInt(data.tmdb_id!, 10)
        const items: WatchedItemIn[] = data.media_type === 'movie'
          ? [{ media_type: 'movie', tmdb_id: tmdbIdNum, watched_at: '' }]
          : [{ media_type: 'episode', tmdb_id: tmdbIdNum, season: data.season ?? undefined, episode: data.episode ?? undefined, watched_at: '' }]
        return (
          <AddToHistoryDialog
            open={addDialogOpen}
            onOpenChange={setAddDialogOpen}
            items={items}
            releaseDate={data.release_date ?? null}
            label="Add to History"
          />
        )
      })()}
    </div>
  )
}

function SessionCard({ session }: { session: SessionOut }) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const hideSession = useHideSession()

  // Sentinel date used for plays that happened before the backfill (date unknown)
  const isUnknownDate = session.source === 'backfill_prior'

  // Duration from first to last activity (how long the viewing session lasted)
  const durationS = isUnknownDate ? 0 : (
    parseISO(session.last_activity_at).getTime() -
    parseISO(session.started_at).getTime()
  ) / 1000

  // Show ended_at only when it differs meaningfully from last_activity_at
  const showEndedAt =
    !isUnknownDate &&
    session.ended_at != null &&
    session.ended_at !== session.last_activity_at

  const handleDelete = () => {
    hideSession.mutate(session.id, {
      onSuccess: () => {
        setConfirmDelete(false)
      },
    })
  }

  return (
    <Card className="p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge status={session.status} completionReason={session.completion_reason} />
            {session.completion_reason && (
              <span className="text-xs text-muted-foreground">
                {COMPLETION_REASON_LABELS[session.completion_reason] ?? session.completion_reason}
              </span>
            )}
          </div>
          {session.percent != null && (
            <PercentBar percent={session.percent} className="max-w-xs" />
          )}
          <p className="text-xs text-muted-foreground">
            {isUnknownDate
              ? 'Date unknown (played before history tracking began)'
              : formatDate(session.started_at)}
            {durationS > 60 && ` · ${formatRuntime(durationS)} watched`}
          </p>
          {showEndedAt && (
            <p className="text-xs text-muted-foreground">
              Ended: {formatDate(session.ended_at!)}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isUnknownDate && (
            <TimeAgo iso={session.last_activity_at} className="text-xs text-muted-foreground" />
          )}
          {confirmDelete ? (
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="destructive"
                className="h-6 px-2 text-xs"
                disabled={hideSession.isPending}
                onClick={handleDelete}
              >
                {hideSession.isPending ? '…' : 'Delete'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-xs"
                onClick={() => setConfirmDelete(false)}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
              title="Delete this session"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={13} />
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

function ObservationsTable({ observations }: { observations: ObservationOut[] }) {
  return (
    <div className="rounded-md border border-border text-xs overflow-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Kind</TableHead>
            <TableHead>When</TableHead>
            <TableHead>PlayCount</TableHead>
            <TableHead>Position</TableHead>
            <TableHead>Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {observations.map(o => (
            <TableRow key={o.id}>
              <TableCell className="font-mono">{o.kind}</TableCell>
              <TableCell>{formatDate(o.observed_at)}</TableCell>
              <TableCell>{o.kodi_play_count ?? '—'}</TableCell>
              <TableCell>{o.resume_pos_s != null ? formatRuntime(o.resume_pos_s) : '—'}</TableCell>
              <TableCell>{o.resume_total_s != null ? formatRuntime(o.resume_total_s) : '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
