import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import {
  useActor,
  useActorRecommendations,
  useActorFilmography,
  useActorRecentSessions,
} from '@/api/queries'
import type {
  ActorDiscoveryItem,
  FilmographyItemData,
  ActorRecentSessionData,
  CreditType,
} from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { ErrorState } from '@/components/shared/ErrorState'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { differenceInYears, format, parseISO } from 'date-fns'
import type { components } from '@/api/types'

type ActorAppearanceOut = components['schemas']['ActorAppearanceOut']

export const Route = createFileRoute('/actors/$actorId')({
  component: ActorDetailPage,
})

// ---------------------------------------------------------------------------
// Shared tab bar component
// ---------------------------------------------------------------------------
function TabBar<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { value: T; label: string }[]
  active: T
  onChange: (v: T) => void
}) {
  return (
    <div className="flex gap-0 border-b border-border mb-4">
      {tabs.map(t => (
        <button
          key={t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            'px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors',
            active === t.value
              ? 'border-foreground text-foreground'
              : 'border-transparent text-muted-foreground hover:text-foreground',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Credit filter button row
// ---------------------------------------------------------------------------
const CREDIT_LABELS: Record<CreditType, string> = {
  acting: 'Acting',
  self: 'Self',
  production: 'Production',
  unknown: 'Unknown',
}

function CreditFilterBar({
  items,
  active,
  onChange,
}: {
  items: FilmographyItemData[]
  active: CreditType
  onChange: (v: CreditType) => void
}) {
  const available = new Set(items.map(i => i.credit_type as CreditType))
  if (available.size <= 1) return null
  return (
    <div className="flex flex-wrap gap-2 mb-4">
      {(['acting', 'self', 'production', 'unknown'] as CreditType[])
        .filter(ct => available.has(ct))
        .map(ct => (
          <button
            key={ct}
            onClick={() => onChange(ct)}
            className={cn(
              'px-3 py-1 text-xs rounded-full border transition-colors',
              active === ct
                ? 'bg-accent text-accent-foreground border-accent'
                : 'border-border text-muted-foreground hover:text-foreground hover:bg-accent/40',
            )}
          >
            {CREDIT_LABELS[ct]}
            <span className="ml-1 opacity-60">
              {items.filter(i => i.credit_type === ct).length}
            </span>
          </button>
        ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
function ActorDetailPage() {
  const { actorId } = Route.useParams()
  const id = parseInt(actorId, 10)

  const { data: actor, isLoading, isError } = useActor(id)
  const { data: recommendations } = useActorRecommendations(id)
  const { data: filmography } = useActorFilmography(id)
  const { data: recentSessions } = useActorRecentSessions(id)

  // UI state
  const [photoOpen, setPhotoOpen] = useState(false)
  const [bioExpanded, setBioExpanded] = useState(false)
  const [libTab, setLibTab] = useState<'watched' | 'unwatched'>('watched')
  const [recTab, setRecTab] = useState<'popular' | 'recent'>('popular')
  const [filmTab, setFilmTab] = useState<'movies' | 'tv'>('movies')
  const [movieFilter, setMovieFilter] = useState<CreditType>('acting')
  const [tvFilter, setTvFilter] = useState<CreditType>('acting')

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={4} variant="card" /></div>
  if (isError || !actor) return <div className="p-4 md:p-6"><ErrorState message="Actor not found." /></div>

  // Age calculation
  const age = actor.birthday
    ? (() => { try { return differenceInYears(new Date(), parseISO(actor.birthday)) } catch { return null } })()
    : null

  const formattedBirthday = actor.birthday
    ? (() => { try { return format(parseISO(actor.birthday), 'd MMMM yyyy') } catch { return actor.birthday } })()
    : null

  const bioIsLong = (actor.biography?.length ?? 0) > 300

  const hasLibrary = actor.watched_items.length > 0 || actor.library_items.length > 0
  const hasRecs = recommendations && (
    recommendations.most_popular.length > 0 || recommendations.most_recent.length > 0
  )
  const hasFilmography = filmography && (
    filmography.movies.length > 0 || filmography.tv_shows.length > 0
  )
  const hasRecentSessions = (recentSessions?.length ?? 0) > 0

  // Filmography filtered items
  const filmItems = filmTab === 'movies' ? (filmography?.movies ?? []) : (filmography?.tv_shows ?? [])
  const activeFilter = filmTab === 'movies' ? movieFilter : tvFilter
  const setActiveFilter = filmTab === 'movies' ? setMovieFilter : setTvFilter
  const filteredFilmItems = filmItems.filter(i => i.credit_type === activeFilter)

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">

      {/* ── Actor profile header ── */}
      <div className="flex gap-6 mb-6">
        {/* Photo — clickable to open overlay */}
        <div className="shrink-0 w-32 sm:w-44">
          <button
            onClick={() => actor.profile_url && setPhotoOpen(true)}
            className={cn(
              'rounded-lg overflow-hidden aspect-[2/3] bg-muted block w-full',
              actor.profile_url && 'cursor-pointer hover:opacity-90 transition',
            )}
            aria-label="View full photo"
          >
            <MediaPoster
              src={actor.profile_url ?? null}
              alt={actor.name}
              className="w-full h-full"
              fallbackLetter={actor.name[0]}
            />
          </button>
        </div>

        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold leading-tight mb-2">{actor.name}</h1>

          {/* Birthday + age + birthplace */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground mb-3">
            {formattedBirthday && (
              <span>
                Born {formattedBirthday}
                {age != null && <span className="text-foreground/70"> · Age {age}</span>}
              </span>
            )}
            {actor.birthplace && <span>{actor.birthplace}</span>}
          </div>

          {/* External links */}
          <div className="flex flex-wrap gap-2 mb-3">
            <a
              href={`https://www.themoviedb.org/person/${actor.tmdb_person_id}`}
              target="_blank"
              rel="noreferrer"
            >
              <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">
                TMDB
              </Badge>
            </a>
            {actor.imdb_id && (
              <a
                href={`https://www.imdb.com/name/${actor.imdb_id}`}
                target="_blank"
                rel="noreferrer"
              >
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">
                  IMDb
                </Badge>
              </a>
            )}
            {actor.instagram_id && (
              <a
                href={`https://www.instagram.com/${actor.instagram_id}`}
                target="_blank"
                rel="noreferrer"
              >
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">
                  Instagram
                </Badge>
              </a>
            )}
            {actor.wikidata_id && (
              <a
                href={`https://www.wikidata.org/wiki/Special:GoToLinkedPage/enwiki/${actor.wikidata_id}`}
                target="_blank"
                rel="noreferrer"
              >
                <Badge variant="secondary" className="text-xs hover:bg-secondary/80 cursor-pointer">
                  Wikipedia
                </Badge>
              </a>
            )}
          </div>

          {/* Biography with expand/collapse */}
          {actor.biography && (
            <div>
              <p
                className={cn(
                  'text-sm leading-relaxed text-foreground/80',
                  !bioExpanded && bioIsLong && 'line-clamp-4',
                )}
              >
                {actor.biography}
              </p>
              {bioIsLong && (
                <button
                  onClick={() => setBioExpanded(e => !e)}
                  className="text-xs text-muted-foreground hover:text-foreground mt-1 transition-colors"
                >
                  {bioExpanded ? 'Read Less' : 'Read More'}
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Photo overlay ── */}
      <Dialog open={photoOpen} onOpenChange={setPhotoOpen}>
        <DialogContent className="max-w-sm p-0 overflow-hidden border-0">
          {actor.profile_url && (
            <img
              src={actor.profile_url}
              alt={actor.name}
              className="w-full h-auto block"
            />
          )}
        </DialogContent>
      </Dialog>

      {/* ── Library tabs: Watched History | Unwatched Library ── */}
      {hasLibrary && (
        <section className="mb-8">
          <TabBar
            tabs={[
              { value: 'watched', label: 'Your Watched History' },
              { value: 'unwatched', label: 'Your Unwatched Library' },
            ]}
            active={libTab}
            onChange={setLibTab}
          />
          {(() => {
            const items = libTab === 'watched' ? actor.watched_items : actor.library_items
            if (items.length === 0) {
              return (
                <p className="text-sm text-muted-foreground py-4">
                  {libTab === 'watched'
                    ? 'No watched items found for this actor.'
                    : 'No unwatched library items found for this actor.'}
                </p>
              )
            }
            return (
              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
                {items.map((item, i) => (
                  <AppearanceCard key={`${libTab}-${i}`} item={item} />
                ))}
              </div>
            )
          })()}
        </section>
      )}

      {/* ── Recommendations tabs: Most Popular | Most Recent ── */}
      {hasRecs && (
        <>
          <Separator className="my-8" />
          <section className="mb-8">
            <h2 className="text-lg font-semibold mb-3">Recommendations</h2>
            <TabBar
              tabs={[
                { value: 'popular', label: 'Most Popular' },
                { value: 'recent', label: 'Most Recent' },
              ]}
              active={recTab}
              onChange={setRecTab}
            />
            {(() => {
              const items =
                recTab === 'popular'
                  ? (recommendations?.most_popular ?? [])
                  : (recommendations?.most_recent ?? [])
              if (items.length === 0) {
                return <p className="text-sm text-muted-foreground py-4">No recommendations available.</p>
              }
              return (
                <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
                  {items.map(item => (
                    <DiscoveryCard
                      key={`${recTab}-${item.media_type}-${item.tmdb_id}`}
                      item={item}
                    />
                  ))}
                </div>
              )
            })()}
          </section>
        </>
      )}

      {/* ── Filmography: Movies | TV Shows tabs + credit filters ── */}
      {hasFilmography && (
        <>
          <Separator className="my-8" />
          <section className="mb-8">
            <h2 className="text-lg font-semibold mb-3">Filmography</h2>
            <TabBar
              tabs={[
                { value: 'movies', label: `Movies (${filmography?.movies.length ?? 0})` },
                { value: 'tv', label: `TV Shows (${filmography?.tv_shows.length ?? 0})` },
              ]}
              active={filmTab}
              onChange={setFilmTab}
            />
            <CreditFilterBar
              items={filmItems}
              active={activeFilter}
              onChange={setActiveFilter}
            />
            {filteredFilmItems.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4">
                No {CREDIT_LABELS[activeFilter].toLowerCase()} credits found.
              </p>
            ) : (
              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
                {filteredFilmItems.map((item, i) => (
                  <FilmographyCard key={`film-${item.media_type}-${item.tmdb_id}-${item.credit_type}-${i}`} item={item} />
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {/* ── Recently Played (always at bottom) ── */}
      {hasRecentSessions && (
        <>
          <Separator className="my-8" />
          <section>
            <h2 className="text-lg font-semibold mb-4">Recently Played</h2>
            <div className="space-y-2">
              {recentSessions!.map(s => (
                <RecentSessionRow key={s.session_id} session={s} />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AppearanceCard — library/watched items (existing local DB items)
// ---------------------------------------------------------------------------
function AppearanceCard({ item }: { item: ActorAppearanceOut }) {
  const inner = (
    <>
      <div className="aspect-[2/3] rounded-md overflow-hidden bg-muted mb-1 relative">
        <MediaPoster
          src={item.poster_url ?? null}
          alt={item.title}
          className="w-full h-full"
          fallbackLetter={item.title[0]}
        />
        {item.media_type === 'tv' && item.total_episodes != null && (
          <div className="absolute bottom-1 right-1">
            <Badge variant="secondary" className="text-xs px-1 py-0 leading-none">
              {item.watched_episodes ?? 0}/{item.total_episodes}
            </Badge>
          </div>
        )}
      </div>
      <p className="text-xs font-medium line-clamp-2 leading-snug">{item.title}</p>
      {item.character && (
        <p className="text-xs text-muted-foreground line-clamp-1">{item.character}</p>
      )}
      {item.last_watched_at && (
        <p className="text-xs text-muted-foreground">
          {(() => { try { return format(parseISO(item.last_watched_at), 'd MMM yyyy') } catch { return '' } })()}
        </p>
      )}
    </>
  )

  if (item.media_type === 'tv' && item.show_id != null) {
    return (
      <Link
        to="/shows/$showId"
        params={{ showId: String(item.show_id) }}
        className="block hover:opacity-90 transition"
      >
        {inner}
      </Link>
    )
  }
  if (item.item_id != null) {
    return (
      <Link
        to="/items/$itemId"
        params={{ itemId: String(item.item_id) }}
        className="block hover:opacity-90 transition"
      >
        {inner}
      </Link>
    )
  }
  return <div>{inner}</div>
}

// ---------------------------------------------------------------------------
// DiscoveryCard — recommendations (may now include library items)
// ---------------------------------------------------------------------------
function DiscoveryCard({ item }: { item: ActorDiscoveryItem }) {
  const to = item.media_type === 'movie'
    ? `/tmdb/movie/${item.tmdb_id}`
    : `/tmdb/tv/${item.tmdb_id}`
  return (
    <Link to={to as never} className="block hover:opacity-90 transition">
      <div className="aspect-[2/3] rounded-md overflow-hidden bg-muted mb-1 relative">
        <MediaPoster
          src={item.poster_url ?? null}
          alt={item.title}
          className="w-full h-full"
          fallbackLetter={item.title[0]}
        />
        {item.watched && (
          <div className="absolute top-1 left-1">
            <Badge className="text-xs px-1 py-0 leading-none bg-green-700/90 text-white border-0">
              Watched
            </Badge>
          </div>
        )}
        {item.in_library && !item.watched && (
          <div className="absolute top-1 left-1">
            <Badge variant="secondary" className="text-xs px-1 py-0 leading-none opacity-80">
              In Library
            </Badge>
          </div>
        )}
      </div>
      <p className="text-xs font-medium line-clamp-2 leading-snug">{item.title}</p>
      {item.year && <p className="text-xs text-muted-foreground">{item.year}</p>}
    </Link>
  )
}

// ---------------------------------------------------------------------------
// FilmographyCard — full TMDB filmography items
// ---------------------------------------------------------------------------
function FilmographyCard({ item }: { item: FilmographyItemData }) {
  const localTo =
    item.media_type === 'tv' && item.show_id != null
      ? { to: '/shows/$showId' as const, params: { showId: String(item.show_id) } }
      : item.item_id != null
        ? { to: '/items/$itemId' as const, params: { itemId: String(item.item_id) } }
        : null

  const tmdbTo = item.media_type === 'movie'
    ? `/tmdb/movie/${item.tmdb_id}`
    : `/tmdb/tv/${item.tmdb_id}`

  const creditLabel = item.credit_type === 'production'
    ? (item.job ?? 'Production')
    : (item.character ?? null)

  const inner = (
    <>
      <div className="aspect-[2/3] rounded-md overflow-hidden bg-muted mb-1 relative">
        <MediaPoster
          src={item.poster_url ?? null}
          alt={item.title}
          className="w-full h-full"
          fallbackLetter={item.title[0]}
        />
        {item.watched && (
          <div className="absolute top-1 left-1">
            <Badge className="text-xs px-1 py-0 leading-none bg-green-700/90 text-white border-0">
              ✓
            </Badge>
          </div>
        )}
        {item.media_type === 'tv' && item.total_episodes != null && item.in_library && (
          <div className="absolute bottom-1 right-1">
            <Badge variant="secondary" className="text-xs px-1 py-0 leading-none">
              {item.watched_episodes ?? 0}/{item.total_episodes}
            </Badge>
          </div>
        )}
      </div>
      <p className="text-xs font-medium line-clamp-2 leading-snug">{item.title}</p>
      {item.year && <p className="text-xs text-muted-foreground">{item.year}</p>}
      {creditLabel && (
        <p className="text-xs text-muted-foreground line-clamp-1 italic">{creditLabel}</p>
      )}
    </>
  )

  if (localTo) {
    return (
      <Link {...localTo} className="block hover:opacity-90 transition">
        {inner}
      </Link>
    )
  }
  return (
    <Link to={tmdbTo as never} className="block hover:opacity-90 transition">
      {inner}
    </Link>
  )
}

// ---------------------------------------------------------------------------
// RecentSessionRow — recently played sessions list
// ---------------------------------------------------------------------------
function RecentSessionRow({ session }: { session: ActorRecentSessionData }) {
  const dateStr = session.completed_at
    ? (() => { try { return format(parseISO(session.completed_at), 'd MMM yyyy') } catch { return '' } })()
    : ''

  const secondaryLine = session.media_type === 'tv' && session.show_title
    ? `${session.show_title}${session.season_number != null && session.episode_number != null
        ? ` · S${session.season_number}E${session.episode_number}`
        : ''}`
    : null

  const inner = (
    <div className="flex items-center gap-3 px-3 py-2 rounded-md hover:bg-accent/40 transition-colors">
      <div className="shrink-0 w-9 aspect-[2/3] rounded overflow-hidden bg-muted">
        <MediaPoster
          src={session.poster_url ?? null}
          alt={session.title}
          className="w-full h-full"
          fallbackLetter={session.title[0]}
        />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium line-clamp-1">{session.title}</p>
        {secondaryLine && (
          <p className="text-xs text-muted-foreground line-clamp-1">{secondaryLine}</p>
        )}
      </div>
      {dateStr && (
        <span className="text-xs text-muted-foreground shrink-0">{dateStr}</span>
      )}
    </div>
  )

  if (session.media_type === 'tv' && session.show_id != null) {
    return (
      <Link to="/shows/$showId" params={{ showId: String(session.show_id) }}>
        {inner}
      </Link>
    )
  }
  if (session.item_id != null) {
    return (
      <Link to="/items/$itemId" params={{ itemId: String(session.item_id) }}>
        {inner}
      </Link>
    )
  }
  return <div>{inner}</div>
}
