import { Link } from '@tanstack/react-router'

interface CastMember {
  tmdb_person_id: number
  name: string
  character?: string | null
  profile_url?: string | null
}

interface Props {
  cast: CastMember[]
}

export function CastRow({ cast }: Props) {
  if (!cast.length) return null

  return (
    <section>
      <h3 className="text-base font-semibold mb-3">Cast</h3>
      <div className="flex gap-3 overflow-x-auto pb-2 scrollbar-thin">
        {cast.map(person => (
          <Link
            key={person.tmdb_person_id}
            to="/actors/$actorId"
            params={{ actorId: String(person.tmdb_person_id) }}
            className="shrink-0 w-24 group"
          >
            <div className="aspect-[2/3] rounded-md overflow-hidden bg-muted mb-1">
              {person.profile_url ? (
                <img
                  src={person.profile_url}
                  alt={person.name}
                  className="w-full h-full object-cover group-hover:opacity-90 transition"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-2xl text-muted-foreground font-semibold select-none">
                  {person.name[0]?.toUpperCase() ?? '?'}
                </div>
              )}
            </div>
            <p className="text-xs font-medium line-clamp-1">{person.name}</p>
            {person.character && (
              <p className="text-xs text-muted-foreground line-clamp-1">{person.character}</p>
            )}
          </Link>
        ))}
      </div>
    </section>
  )
}
