/**
 * TanStack Query hooks for all LogSAGA API endpoints.
 * Each hook wraps a typed openapi-fetch call with appropriate staleTime.
 */

import { useMutation, useQuery, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import { apiClient } from './client'

// ---------------------------------------------------------------------------
// Query key constants — centralised so invalidation is reliable
// ---------------------------------------------------------------------------
export const QK = {
  health: ['health'] as const,
  artworkStatus: ['artworkStatus'] as const,
  recent: (params: RecentParams) => ['recent', params] as const,
  sessions: (params: SessionsParams) => ['sessions', params] as const,
  inProgress: ['inProgress'] as const,
  shows: (params: ShowsParams) => ['shows', params] as const,
  showDetail: (id: number) => ['showDetail', id] as const,
  seasonDetail: (showId: number, season: number) => ['seasonDetail', showId, season] as const,
  movies: (params: MovieParams) => ['movies', params] as const,
  recentlyAdded: (params: RecentlyAddedParams) => ['recentlyAdded', params] as const,
  item: (id: number) => ['item', id] as const,
  stats: (days: number) => ['stats', days] as const,
  actor: (tmdbPersonId: number) => ['actor', tmdbPersonId] as const,
} as const

// ---------------------------------------------------------------------------
// Param types (mirrors query parameters from types.ts)
// ---------------------------------------------------------------------------
export interface RecentParams {
  limit?: number
  offset?: number
  type?: string | null
  status?: string | null
}

export interface SessionsParams {
  limit?: number
  offset?: number
  type?: string | null
  status?: string | null
  search?: string | null
  date_from?: string | null
  date_to?: string | null
}

// ---------------------------------------------------------------------------
// Helper: throw on API error
// ---------------------------------------------------------------------------
function assertOk<T>(data: T | undefined, error: unknown): T {
  if (error) throw new Error(String(error))
  return data!
}

// ---------------------------------------------------------------------------
// GET /api/health  — polled every 30 s
// ---------------------------------------------------------------------------
export function useHealth() {
  return useQuery({
    queryKey: QK.health,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/health')
      return assertOk(data, error)
    },
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/recent
// ---------------------------------------------------------------------------
export function useRecent(params: RecentParams = {}) {
  return useQuery({
    queryKey: QK.recent(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/recent', {
        params: { query: params },
      })
      return assertOk(data, error)
    },
    staleTime: 30_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/recent — infinite (append) version for Recently Played page
// ---------------------------------------------------------------------------
const RECENT_PAGE_SIZE = 50

export function useRecentInfinite(params: Omit<RecentParams, 'limit' | 'offset'> = {}) {
  return useInfiniteQuery({
    queryKey: ['recentInfinite', params] as const,
    queryFn: async ({ pageParam }) => {
      const { data, error } = await apiClient.GET('/api/recent', {
        params: { query: { ...params, limit: RECENT_PAGE_SIZE, offset: pageParam as number } },
      })
      return assertOk(data, error)
    },
    initialPageParam: 0,
    getNextPageParam: (_lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + p.items.length, 0)
      const total = allPages[0]?.total ?? 0
      return loaded < total ? loaded : undefined
    },
    staleTime: 30_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/sessions  (history table, with all filters)
// ---------------------------------------------------------------------------
export function useSessions(params: SessionsParams = {}) {
  return useQuery({
    queryKey: QK.sessions(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/sessions', {
        params: { query: params },
      })
      return assertOk(data, error)
    },
    staleTime: 30_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/in-progress  — polled every 30 s while page is open
// ---------------------------------------------------------------------------
export function useInProgress() {
  return useQuery({
    queryKey: QK.inProgress,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/in-progress')
      return assertOk(data, error)
    },
    staleTime: 30_000,
    refetchInterval: 30_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/shows
// ---------------------------------------------------------------------------
export interface ShowsParams {
  search?: string
  filter?: string
  sort?: string
}

export function useShows(params: ShowsParams = {}) {
  return useQuery({
    queryKey: QK.shows(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/shows', {
        params: { query: params },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/shows/{show_id}
// ---------------------------------------------------------------------------
export function useShowDetail(showId: number) {
  return useQuery({
    queryKey: QK.showDetail(showId),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/shows/{show_id}', {
        params: { path: { show_id: showId } },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
    enabled: showId > 0,
  })
}

// ---------------------------------------------------------------------------
// GET /api/shows/{show_id}/seasons/{season}
// ---------------------------------------------------------------------------
export function useSeasonDetail(showId: number, season: number) {
  return useQuery({
    queryKey: QK.seasonDetail(showId, season),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/shows/{show_id}/seasons/{season}', {
        params: { path: { show_id: showId, season } },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
    enabled: showId > 0,
  })
}

// ---------------------------------------------------------------------------
// GET /api/movies
// ---------------------------------------------------------------------------
export interface MovieParams {
  search?: string | null
  filter?: string
  sort?: string
  limit?: number
  offset?: number
}

export interface RecentlyAddedParams {
  limit?: number
  offset?: number
  type?: string | null
}

export function useMovies(params: MovieParams = {}) {
  return useQuery({
    queryKey: QK.movies(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/movies', {
        params: { query: params },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/recently-added
// ---------------------------------------------------------------------------
export function useRecentlyAdded(params: RecentlyAddedParams = {}) {
  return useQuery({
    queryKey: QK.recentlyAdded(params),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/recently-added', {
        params: { query: params },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/items/{item_id}
// ---------------------------------------------------------------------------
export function useItem(id: number) {
  return useQuery({
    queryKey: QK.item(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/items/{item_id}', {
        params: { path: { item_id: id } },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
    enabled: id > 0,
  })
}

// ---------------------------------------------------------------------------
// GET /api/stats
// ---------------------------------------------------------------------------
export function useStats(days = 30) {
  return useQuery({
    queryKey: QK.stats(days),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/stats', {
        params: { query: { days } },
      })
      return assertOk(data, error)
    },
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/admin/artwork-status  — polled every 10 s during download
// ---------------------------------------------------------------------------
export function useArtworkStatus() {
  return useQuery({
    queryKey: QK.artworkStatus,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/admin/artwork-status')
      return assertOk(data, error)
    },
    staleTime: 10_000,
    refetchInterval: 10_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/admin/settings  +  PUT /api/admin/settings
// ---------------------------------------------------------------------------
export interface AdminSettings {
  min_started_seconds: number
  session_gap_hours: number
  watched_percent: number
  watched_remaining_minutes_movie: number
  watched_remaining_minutes_episode: number
  poll_seconds: number
  library_sync_hours: number
}

export function useAdminSettings() {
  return useQuery({
    queryKey: ['adminSettings'] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/admin/settings' as never)
      return assertOk(data as unknown as AdminSettings, error)
    },
    staleTime: 30_000,
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (updates: Partial<AdminSettings>) => {
      const { data, error } = await apiClient.PUT('/api/admin/settings' as never, {
        body: updates,
      } as never)
      return assertOk(data as unknown as AdminSettings, error)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['adminSettings'] })
      queryClient.invalidateQueries({ queryKey: ['health'] })
    },
  })
}

// ---------------------------------------------------------------------------
// POST /api/admin/rebuild-sessions  (mutation — invalidates everything)
// ---------------------------------------------------------------------------
export function useRebuildSessions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await apiClient.POST('/api/admin/rebuild-sessions')
      return assertOk(data, error)
    },
    onSuccess: () => {
      // Rebuild affects sessions, recent, stats, shows — invalidate all
      queryClient.invalidateQueries()
    },
  })
}

// ---------------------------------------------------------------------------
// GET /api/admin/jobs  — polled while any job is running
// POST /api/admin/poll, /sync-library, /fetch-artwork, /enrich-metadata
// ---------------------------------------------------------------------------
export interface JobState {
  name: string
  running: boolean
  last_run?: string | null
  last_result?: string | null
}

export function useJobs() {
  return useQuery({
    queryKey: ['jobs'] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/admin/jobs' as never)
      return assertOk(data as unknown as JobState[], error)
    },
    staleTime: 2_000,
    refetchInterval: (query) => {
      const jobs = query.state.data as JobState[] | undefined
      return jobs?.some(j => j.running) ? 5_000 : false
    },
  })
}

// ---------------------------------------------------------------------------
// DELETE /api/sessions/{session_id}  — soft-delete (hide) a watch session
// ---------------------------------------------------------------------------
export function useHideSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (sessionId: number) => {
      const { error } = await apiClient.DELETE('/api/sessions/{session_id}' as never, {
        params: { path: { session_id: sessionId } },
      } as never)
      if (error) throw new Error(String(error))
    },
    onSuccess: () => {
      // Invalidate item queries so the detail page refreshes
      queryClient.invalidateQueries({ queryKey: ['item'] })
      queryClient.invalidateQueries({ queryKey: QK.inProgress })
      queryClient.invalidateQueries({ queryKey: ['recentInfinite'] })
    },
  })
}

/** Trigger one of the fire-and-forget admin jobs by backend name. */
export function useTriggerJob(jobName: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const endpoint = `/api/admin/${jobName.replace('_', '-')}` as never
      const { data, error } = await apiClient.POST(endpoint)
      return assertOk(data, error)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

// ---------------------------------------------------------------------------
// GET /api/search
// ---------------------------------------------------------------------------
export interface SearchParams {
  q: string
  type?: string
  limit?: number
}

export function useSearch(params: SearchParams, enabled = true) {
  return useQuery({
    queryKey: ['search', params] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/search', {
        params: { query: { q: params.q, type: params.type, limit: params.limit } },
      })
      return assertOk(data, error)
    },
    enabled: enabled && params.q.trim().length > 0,
    staleTime: 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/tmdb/movie/{tmdb_id}
// ---------------------------------------------------------------------------
export function useTMDBMovie(tmdbId: number | null) {
  return useQuery({
    queryKey: ['tmdbMovie', tmdbId] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/tmdb/movie/{tmdb_id}', {
        params: { path: { tmdb_id: tmdbId! } },
      })
      return assertOk(data, error)
    },
    enabled: tmdbId !== null,
    staleTime: 24 * 60 * 60_000, // 24 h (backend caches for 30 days)
  })
}

// ---------------------------------------------------------------------------
// GET /api/tmdb/tv/{tmdb_id}
// ---------------------------------------------------------------------------
export function useTMDBTV(tmdbId: number | null) {
  return useQuery({
    queryKey: ['tmdbTV', tmdbId] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/tmdb/tv/{tmdb_id}', {
        params: { path: { tmdb_id: tmdbId! } },
      })
      return assertOk(data, error)
    },
    enabled: tmdbId !== null,
    staleTime: 24 * 60 * 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/tmdb/tv/{tmdb_id}/season/{season_number}
// ---------------------------------------------------------------------------
export function useTMDBSeason(tmdbId: number | null, season: number | null) {
  return useQuery({
    queryKey: ['tmdbSeason', tmdbId, season] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        '/api/tmdb/tv/{tmdb_id}/season/{season_number}',
        { params: { path: { tmdb_id: tmdbId!, season_number: season! } } },
      )
      return assertOk(data, error)
    },
    enabled: tmdbId !== null && season !== null,
    staleTime: 24 * 60 * 60_000,
  })
}

// ---------------------------------------------------------------------------
// GET /api/recently-viewed
// ---------------------------------------------------------------------------
export function useRecentlyViewed(limit = 20) {
  return useQuery({
    queryKey: ['recentlyViewed', limit] as const,
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/recently-viewed', {
        params: { query: { limit } },
      })
      return assertOk(data, error)
    },
    staleTime: 30_000,
  })
}

// ---------------------------------------------------------------------------
// POST /api/watched  — manual watch history
// ---------------------------------------------------------------------------
export interface WatchedItemIn {
  media_type: string
  tmdb_id: number
  season?: number | null
  episode?: number | null
  watched_at: string
}

export function usePostWatched() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (items: WatchedItemIn[]) => {
      const { data, error } = await apiClient.POST('/api/watched', {
        body: { items },
      })
      return assertOk(data, error)
    },
    onSuccess: () => {
      // Invalidate history views
      queryClient.invalidateQueries({ queryKey: QK.recent({}) })
      queryClient.invalidateQueries({ queryKey: ['recentInfinite'] })
      queryClient.invalidateQueries({ queryKey: ['recentlyViewed'] })
      queryClient.invalidateQueries({ queryKey: QK.inProgress })
    },
  })
}

// ---------------------------------------------------------------------------
// GET /api/actors/{tmdb_person_id}
// ---------------------------------------------------------------------------
export function useActor(tmdbPersonId: number | null) {
  return useQuery({
    queryKey: QK.actor(tmdbPersonId!),
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        '/api/actors/{tmdb_person_id}' as never,
        { params: { path: { tmdb_person_id: tmdbPersonId! } } } as never,
      )
      return assertOk(data as unknown as import('./types').components['schemas']['ActorDetailOut'], error)
    },
    enabled: tmdbPersonId !== null,
    staleTime: 60 * 60_000,  // 1 hour (biography changes rarely)
  })
}
