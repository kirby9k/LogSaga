import { createFileRoute, Link } from '@tanstack/react-router'
import { useActor } from '@/api/queries'
import { MediaPoster } from '@/components/shared/MediaPoster'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { ErrorState } from '@/components/shared/ErrorState'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { format, parseISO } from 'date-fns'
import type { components } from '@/api/types'

type ActorAppearanceOut = components['schemas']['ActorAppearanceOut']

export const Route = createFileRoute('/actors/$actorId')({
  component: ActorDetailPage,
})

function ActorDetailPage() {
  const { actorId } = Route.useParams()
  const id = parseInt(actorId, 10)
  const { data: actor, isLoading, isError } = useActor(id)

  if (isLoading) return <div className="p-4 md:p-6"><LoadingSkeleton count={4} variant="card" /></div>
  if (isError || !actor) return <div className="p-4 md:p-6"><ErrorState message="Actor not found." /></div>

  const formattedBirthday = actor.birthday
    ? (() => { try { return format(parseISO(actor.birthday), 'd MMMM yyyy') } catch { return actor.birthday } })()
    : null

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto">
      {/* Actor profile header */}
      <div className="flex gap-6 mb-8">
        <div className="shrink-0 w-32 sm:w-44">
          <div className="rounded-lg overflow-hidden aspect-[2/3] bg-muted">
            <MediaPoster
              src={actor.profile_url ?? null}
              alt={actor.name}
              className="w-full h-full"
              fallbackLetter={actor.name[0]}
            />
          </div>
        </div>

        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold leading-tight mb-3">{actor.name}</h1>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground mb-4">
            {formattedBirthday && <span>Born {formattedBirthday}</span>}
            {actor.birthplace && <span>{actor.birthplace}</span>}
          </div>

          {actor.biography && (
            <p className="text-sm leading-relaxed text-foreground/80 line-clamp-6">
              {actor.biography}
            </p>
          )}
        </div>
      </div>

      {/* Watched history */}
      {actor.watched_items.length > 0 && (
        <section className="mb-8">
          <h2 className="text-lg font-semibold mb-4">Your Watched History</h2>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
            {actor.watched_items.map((item, i) => (
              <AppearanceCard key={`watched-${i}`} item={item} />
            ))}
          </div>
        </section>
      )}

      {actor.watched_items.length > 0 && actor.library_items.length > 0 && (
        <Separator className="my-8" />
      )}

      {/* Library items (not yet watched) */}
      {actor.library_items.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-4">Your Kodi Library</h2>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
            {actor.library_items.map((item, i) => (
              <AppearanceCard key={`library-${i}`} item={item} />
            ))}
          </div>
        </section>
      )}

      {actor.watched_items.length === 0 && actor.library_items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No items found in your library featuring this actor.
        </p>
      )}
    </div>
  )
}

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
        {/* Episode progress badge for TV */}
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
