/**
 * GlobalSearch — Ctrl+K command-palette overlay.
 *
 * Shows combined local library + TMDB results in a floating dialog.
 * Local items link to existing detail pages; TMDB-only items link to
 * /tmdb/movie/{id} or /tmdb/tv/{id}.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Film, Tv, Search, X } from 'lucide-react'
import { useSearch as useSearchQuery, useRecentlyViewed } from '@/api/queries'
import type { components } from '@/api/types'

type SearchResult = components['schemas']['SearchResultOut']
type RecentlyViewed = components['schemas']['RecentlyViewedOut']

interface GlobalSearchProps {
  open: boolean
  onClose: () => void
}

function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

function ResultRow({
  result,
  focused,
  onSelect,
}: {
  result: SearchResult
  focused: boolean
  onSelect: () => void
}) {
  const isLocal = result.source === 'local'
  const mediaIcon =
    result.media_type === 'movie' ? (
      <Film size={14} className="text-muted-foreground" />
    ) : (
      <Tv size={14} className="text-muted-foreground" />
    )

  return (
    <button
      className={`w-full flex items-center gap-3 px-3 py-2 text-left rounded-md transition-colors ${
        focused ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
      }`}
      onClick={onSelect}
    >
      {/* Poster thumbnail */}
      <div className="flex-shrink-0 w-8 h-12 bg-muted rounded overflow-hidden">
        {result.poster_url ? (
          <img
            src={result.poster_url}
            alt=""
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            {mediaIcon}
          </div>
        )}
      </div>

      {/* Title + meta */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 truncate">
          <span className="font-medium text-sm truncate">{result.title}</span>
          {result.year && (
            <span className="text-xs text-muted-foreground flex-shrink-0">
              {result.year}
            </span>
          )}
        </div>
        {result.show_title && (
          <div className="text-xs text-muted-foreground truncate">{result.show_title}</div>
        )}
      </div>

      {/* Source badge */}
      <span
        className={`flex-shrink-0 text-xs px-1.5 py-0.5 rounded font-medium ${
          isLocal
            ? 'bg-primary/20 text-primary'
            : 'bg-muted text-muted-foreground'
        }`}
      >
        {isLocal ? 'Library' : 'TMDB'}
      </span>
    </button>
  )
}

function RecentRow({
  item,
  onSelect,
}: {
  item: RecentlyViewed
  onSelect: () => void
}) {
  return (
    <button
      className="w-full flex items-center gap-3 px-3 py-2 text-left rounded-md hover:bg-accent/50 transition-colors"
      onClick={onSelect}
    >
      <div className="flex-shrink-0 w-8 h-12 bg-muted rounded overflow-hidden">
        {item.poster_url ? (
          <img
            src={item.poster_url}
            alt=""
            className="w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Tv size={14} className="text-muted-foreground" />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <span className="text-sm font-medium truncate">{item.title}</span>
      </div>
      <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
        TMDB
      </span>
    </button>
  )
}

export function GlobalSearch({ open, onClose }: GlobalSearchProps) {
  const [query, setQuery] = useState('')
  const [focusedIndex, setFocusedIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  const debouncedQuery = useDebounce(query, 300)
  const { data: searchData } = useSearchQuery(
    { q: debouncedQuery },
    open && debouncedQuery.length > 0,
  )
  const { data: recentData } = useRecentlyViewed(5)

  const results = searchData?.results ?? []
  const recentItems = recentData ?? []

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQuery('')
      setFocusedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  // Reset focus when results change
  useEffect(() => {
    setFocusedIndex(0)
  }, [results.length])

  if (!open) return null

  function getResultPath(r: SearchResult): string {
    if (r.source === 'local') {
      if (r.media_type === 'tv' && r.show_id) return `/shows/${r.show_id}`
      if (r.local_id) return `/items/${r.local_id}`
    }
    if (r.media_type === 'movie') return `/tmdb/movie/${r.tmdb_id}`
    return `/tmdb/tv/${r.tmdb_id}`
  }

  function getRecentPath(rv: RecentlyViewed): string {
    return rv.media_type === 'movie'
      ? `/tmdb/movie/${rv.tmdb_id}`
      : `/tmdb/tv/${rv.tmdb_id}`
  }

  function selectResult(r: SearchResult) {
    navigate({ to: getResultPath(r) as never })
    onClose()
  }

  function selectRecent(rv: RecentlyViewed) {
    navigate({ to: getRecentPath(rv) as never })
    onClose()
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    const total = results.length
    if (e.key === 'Escape') {
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusedIndex(i => Math.min(i + 1, total - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusedIndex(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results[focusedIndex]) {
      selectResult(results[focusedIndex])
    }
  }

  const showRecent = query.length === 0 && recentItems.length > 0
  const showResults = query.length > 0

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] px-4"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" aria-hidden="true" />

      {/* Dialog */}
      <div
        className="relative w-full max-w-lg bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-2 px-3 border-b border-border">
          <Search size={16} className="text-muted-foreground flex-shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search movies, shows…"
            className="flex-1 h-12 bg-transparent text-sm placeholder:text-muted-foreground outline-none"
          />
          {query && (
            <button onClick={() => setQuery('')} className="text-muted-foreground hover:text-foreground">
              <X size={14} />
            </button>
          )}
        </div>

        {/* Results */}
        {showResults && (
          <div className="max-h-80 overflow-y-auto p-2">
            {results.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-6">No results found</p>
            ) : (
              results.map((r, i) => (
                <ResultRow
                  key={`${r.source}-${r.media_type}-${r.tmdb_id ?? r.local_id}`}
                  result={r}
                  focused={i === focusedIndex}
                  onSelect={() => selectResult(r)}
                />
              ))
            )}
          </div>
        )}

        {/* Recently viewed (empty state) */}
        {showRecent && (
          <div className="max-h-72 overflow-y-auto p-2">
            <p className="text-xs text-muted-foreground px-3 py-1 font-medium uppercase tracking-wide">
              Recently Viewed
            </p>
            {recentItems.map(rv => (
              <RecentRow
                key={rv.id}
                item={rv}
                onSelect={() => selectRecent(rv)}
              />
            ))}
          </div>
        )}

        {/* Empty query, no recents */}
        {!showResults && !showRecent && (
          <p className="text-sm text-muted-foreground text-center py-8">
            Start typing to search your library and TMDB
          </p>
        )}

        {/* Keyboard hint */}
        <div className="border-t border-border px-3 py-1.5 flex items-center gap-3 text-xs text-muted-foreground">
          <span><kbd className="bg-muted px-1 rounded">↑↓</kbd> navigate</span>
          <span><kbd className="bg-muted px-1 rounded">↵</kbd> select</span>
          <span><kbd className="bg-muted px-1 rounded">Esc</kbd> close</span>
        </div>
      </div>
    </div>
  )
}
