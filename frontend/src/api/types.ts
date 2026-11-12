/**
 * TypeScript types generated from the FastAPI OpenAPI schema.
 * Regenerate with: npm run generate-types  (requires backend on :8000)
 */

export interface paths {
  "/api/health": {
    get: {
      responses: {
        200: { content: { "application/json": components["schemas"]["HealthOut"] } };
      };
    };
  };
  "/api/recent": {
    get: {
      parameters: {
        query?: {
          limit?: number;
          offset?: number;
          type?: string | null;
          status?: string | null;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["SessionListOut"] } };
      };
    };
  };
  "/api/sessions": {
    get: {
      parameters: {
        query?: {
          limit?: number;
          offset?: number;
          type?: string | null;
          status?: string | null;
          search?: string | null;
          date_from?: string | null;
          date_to?: string | null;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["SessionListOut"] } };
      };
    };
  };
  "/api/in-progress": {
    get: {
      responses: {
        200: { content: { "application/json": components["schemas"]["InProgressOut"][] } };
      };
    };
  };
  "/api/shows": {
    get: {
      parameters: {
        query?: {
          search?: string | null;
          filter?: string;
          sort?: string;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["ShowSummaryOut"][] } };
      };
    };
  };
  "/api/shows/{show_id}": {
    get: {
      parameters: {
        path: { show_id: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["ShowDetailOut"] } };
        404: { content: { "application/json": components["schemas"]["HTTPValidationError"] } };
      };
    };
  };
  "/api/shows/{show_id}/seasons/{season}": {
    get: {
      parameters: {
        path: { show_id: number; season: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["SeasonSummaryOut"] } };
        404: { content: { "application/json": components["schemas"]["HTTPValidationError"] } };
      };
    };
  };
  "/api/recently-added": {
    get: {
      parameters: {
        query?: {
          limit?: number;
          offset?: number;
          type?: string | null;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["RecentlyAddedListOut"] } };
      };
    };
  };
  "/api/items/{item_id}": {
    get: {
      parameters: {
        path: {
          item_id: number;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["MediaItemDetailOut"] } };
        404: { content: { "application/json": components["schemas"]["HTTPValidationError"] } };
        422: { content: { "application/json": components["schemas"]["HTTPValidationError"] } };
      };
    };
  };
  "/api/stats": {
    get: {
      parameters: {
        query?: {
          days?: number;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["StatsOut"] } };
      };
    };
  };
  "/api/movies": {
    get: {
      parameters: {
        query?: {
          search?: string | null;
          filter?: string;
          sort?: string;
          limit?: number;
          offset?: number;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["MovieListOut"] } };
      };
    };
  };
  "/api/admin/rebuild-sessions": {
    post: {
      responses: {
        200: { content: { "application/json": components["schemas"]["RebuildSessionsOut"] } };
      };
    };
  };
  "/api/admin/artwork-status": {
    get: {
      responses: {
        200: { content: { "application/json": components["schemas"]["ArtworkStatusOut"] } };
      };
    };
  };
  "/api/search": {
    get: {
      parameters: {
        query: {
          q: string;
          type?: string;
          limit?: number;
        };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["SearchOut"] } };
      };
    };
  };
  "/api/tmdb/movie/{tmdb_id}": {
    get: {
      parameters: {
        path: { tmdb_id: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["TMDBMovieOut"] } };
        503: { content: { "application/json": unknown } };
      };
    };
  };
  "/api/tmdb/tv/{tmdb_id}": {
    get: {
      parameters: {
        path: { tmdb_id: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["TMDBTVOut"] } };
        503: { content: { "application/json": unknown } };
      };
    };
  };
  "/api/tmdb/tv/{tmdb_id}/season/{season_number}": {
    get: {
      parameters: {
        path: { tmdb_id: number; season_number: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["TMDBSeasonOut"] } };
        503: { content: { "application/json": unknown } };
      };
    };
  };
  "/api/recently-viewed": {
    get: {
      parameters: {
        query?: { limit?: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["RecentlyViewedOut"][] } };
      };
    };
  };
  "/api/watched": {
    post: {
      requestBody: {
        content: { "application/json": components["schemas"]["PostWatchedIn"] };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["PostWatchedOut"] } };
      };
    };
  };
  "/api/actors/{tmdb_person_id}": {
    get: {
      parameters: {
        path: { tmdb_person_id: number };
      };
      responses: {
        200: { content: { "application/json": components["schemas"]["ActorDetailOut"] } };
        404: { content: { "application/json": components["schemas"]["HTTPValidationError"] } };
      };
    };
  };
}

export interface components {
  schemas: {
    ArtworkOut: {
      art_type: string;
      local_path?: string | null;
      thumbnail_path?: string | null;
    };

    SessionOut: {
      id: number;
      started_at: string;
      last_activity_at: string;
      ended_at?: string | null;
      completed_at?: string | null;
      max_position_s?: number | null;
      runtime_s?: number | null;
      percent?: number | null;
      status: string;
      completion_reason?: string | null;
      source: string;
    };

    ObservationOut: {
      id: number;
      kind: string;
      observed_at: string;
      kodi_play_count?: number | null;
      resume_pos_s?: number | null;
      resume_total_s?: number | null;
    };

    MediaItemSummaryOut: {
      id: number;
      media_type: string;
      title: string;
      show_title?: string | null;
      show_id?: number | null;
      season?: number | null;
      episode?: number | null;
      year?: number | null;
      runtime_s?: number | null;
      kodi_state: string;
      poster_url?: string | null;
      thumbnail_url?: string | null;
    };

    MediaItemDetailOut: {
      id: number;
      media_type: string;
      title: string;
      show_title?: string | null;
      show_id?: number | null;
      season?: number | null;
      episode?: number | null;
      year?: number | null;
      plot?: string | null;
      runtime_s?: number | null;
      genres?: string | null;
      imdb_id?: string | null;
      tmdb_id?: string | null;
      tvdb_id?: string | null;
      release_date?: string | null;
      director?: string | null;
      video_codec?: string | null;
      video_width?: number | null;
      video_height?: number | null;
      hdr_type?: string | null;
      audio_codec?: string | null;
      audio_channels?: number | null;
      kodi_state: string;
      created_at: string;
      updated_at: string;
      artwork: components["schemas"]["ArtworkOut"][];
      sessions: components["schemas"]["SessionOut"][];
      observations: components["schemas"]["ObservationOut"][];
      cast: components["schemas"]["CastMemberOut"][];
    };

    RecentItemOut: {
      session: components["schemas"]["SessionOut"];
      item: components["schemas"]["MediaItemSummaryOut"];
    };

    SessionListOut: {
      total: number;
      items: components["schemas"]["RecentItemOut"][];
    };

    InProgressOut: {
      item: components["schemas"]["MediaItemSummaryOut"];
      resume_pos_s?: number | null;
      resume_total_s?: number | null;
      percent?: number | null;
    };

    NextUnwatchedOut: {
      item_id: number;
      season: number;
      episode: number;
      title: string;
    };

    ShowSummaryOut: {
      show_id: number;
      show_title: string;
      total_episodes: number;
      watched_episodes: number;
      kodi_state: string;
      last_activity_at?: string | null;
      poster_url?: string | null;
      thumbnail_url?: string | null;
      next_unwatched?: components["schemas"]["NextUnwatchedOut"] | null;
    };

    EpisodeSummaryOut: {
      id: number;
      episode: number;
      title: string;
      release_date?: string | null;
      runtime_s?: number | null;
      kodi_state: string;
      watched: boolean;
      plot?: string | null;
      thumb_url?: string | null;
      last_session_at?: string | null;
    };

    SeasonSummaryOut: {
      season: number;
      total_episodes: number;
      watched_episodes: number;
      episodes: components["schemas"]["EpisodeSummaryOut"][];
    };

    ShowDetailOut: {
      show_id: number;
      show_title: string;
      plot?: string | null;
      year?: number | null;
      premiered?: string | null;
      studio?: string | null;
      status?: string | null;
      genres?: string | null;
      tvdb_id?: string | null;
      imdb_id?: string | null;
      tmdb_id?: string | null;
      kodi_state: string;
      total_episodes: number;
      watched_episodes: number;
      poster_url?: string | null;
      fanart_url?: string | null;
      logo_url?: string | null;
      seasons: components["schemas"]["SeasonSummaryOut"][];
      cast: components["schemas"]["CastMemberOut"][];
    };

    RecentlyAddedItemOut: {
      id: number;
      media_type: string;
      title: string;
      show_title?: string | null;
      show_id?: number | null;
      season?: number | null;
      episode?: number | null;
      year?: number | null;
      date_added?: string | null;
      kodi_state: string;
      poster_url?: string | null;
      thumbnail_url?: string | null;
    };

    RecentlyAddedListOut: {
      total: number;
      items: components["schemas"]["RecentlyAddedItemOut"][];
    };

    DailyStatOut: {
      date: string;
      minutes_watched: number;
      sessions_count: number;
    };

    ShowStatOut: {
      show_title: string;
      watched_episodes: number;
      total_minutes: number;
      show_id?: number | null;
    };

    StatsOut: {
      total_sessions: number;
      completed_sessions: number;
      partial_sessions: number;
      total_minutes_watched: number;
      movies_watched: number;
      episodes_watched: number;
      completion_rate: number;
      daily: components["schemas"]["DailyStatOut"][];
      top_shows: components["schemas"]["ShowStatOut"][];
      total_rewatches: number;
      movie_minutes: number;
      episode_minutes: number;
      longest_session_minutes: number;
      current_streak_days: number;
      longest_streak_days: number;
    };

    MovieSummaryOut: {
      id: number;
      title: string;
      year?: number | null;
      runtime_s?: number | null;
      genres?: string | null;
      kodi_state: string;
      poster_url?: string | null;
      thumbnail_url?: string | null;
      total_sessions: number;
      last_watched_at?: string | null;
    };

    MovieListOut: {
      total: number;
      items: components["schemas"]["MovieSummaryOut"][];
    };

    ArtworkStatusOut: {
      total_items: number;
      items_with_artwork: number;
      total_artwork_rows: number;
      pending: number;
    };

    RebuildSessionsOut: {
      items_processed: number;
      sessions_created: number;
      message: string;
    };

    JobStateOut: {
      name: string;
      running: boolean;
      last_run?: string | null;
      last_result?: string | null;
    };

    HealthOut: {
      status: string;
      version: string;
      kodi_db_name?: string | null;
      last_poll_at?: string | null;
      last_success_at?: string | null;
      last_error?: string | null;
      last_error_at?: string | null;
      poll_count: number;
      observation_count: number;
      error_count: number;
      settings: Record<string, unknown>;
    };

    HTTPValidationError: {
      detail?: components["schemas"]["ValidationError"][];
    };

    ValidationError: {
      loc: (string | number)[];
      msg: string;
      type: string;
    };

    // --- Search ---
    SearchResultOut: {
      source: string;         // "local" or "tmdb"
      media_type: string;     // "movie", "episode", "tv"
      tmdb_id?: string | null;
      local_id?: number | null;
      show_id?: number | null;
      title: string;
      show_title?: string | null;
      season?: number | null;
      episode?: number | null;
      year?: number | null;
      poster_url?: string | null;
      has_history: boolean;
    };

    SearchOut: {
      results: components["schemas"]["SearchResultOut"][];
      tmdb_enabled: boolean;
    };

    // --- Cast members ---
    CastMemberOut: {
      tmdb_person_id: number;
      name: string;
      character?: string | null;
      credit_order: number;
      profile_url?: string | null;
    };

    ActorAppearanceOut: {
      media_type: string;        // "movie" or "tv"
      title: string;
      item_id?: number | null;   // media_item.id for movies
      show_id?: number | null;   // kodi_show.id for TV shows
      character?: string | null;
      total_episodes?: number | null;
      watched_episodes?: number | null;
      watched: boolean;
      last_watched_at?: string | null;
      poster_url?: string | null;
    };

    ActorDetailOut: {
      tmdb_person_id: number;
      name: string;
      biography?: string | null;
      birthday?: string | null;
      birthplace?: string | null;
      profile_url?: string | null;
      watched_items: components["schemas"]["ActorAppearanceOut"][];
      library_items: components["schemas"]["ActorAppearanceOut"][];
    };

    // --- TMDB detail ---
    TMDBPersonOut: {
      name: string;
      job?: string | null;
      character?: string | null;
      tmdb_person_id?: number | null;
      profile_url?: string | null;
    };

    TMDBEpisodeOut: {
      season: number;
      episode: number;
      title: string;
      overview?: string | null;
      air_date?: string | null;
      runtime?: number | null;
      still_url?: string | null;
    };

    TMDBSeasonSummaryOut: {
      season: number;
      title: string;
      episode_count: number;
      air_date?: string | null;
      poster_url?: string | null;
    };

    TMDBSeasonOut: {
      tmdb_id: number;
      season: number;
      title: string;
      overview?: string | null;
      air_date?: string | null;
      poster_url?: string | null;
      episodes: components["schemas"]["TMDBEpisodeOut"][];
      local_show_id?: number | null;
    };

    TMDBTVOut: {
      tmdb_id: number;
      title: string;
      overview?: string | null;
      first_air_date?: string | null;
      status?: string | null;
      genres: string[];
      poster_url?: string | null;
      backdrop_url?: string | null;
      vote_average?: number | null;
      seasons: components["schemas"]["TMDBSeasonSummaryOut"][];
      cast: components["schemas"]["TMDBPersonOut"][];
      local_show_id?: number | null;
    };

    TMDBMovieOut: {
      tmdb_id: number;
      title: string;
      overview?: string | null;
      release_date?: string | null;
      runtime?: number | null;
      genres: string[];
      poster_url?: string | null;
      backdrop_url?: string | null;
      vote_average?: number | null;
      director?: string | null;
      cast: components["schemas"]["TMDBPersonOut"][];
      local_id?: number | null;
    };

    // --- Recently viewed ---
    RecentlyViewedOut: {
      id: number;
      media_type: string;
      tmdb_id: number;
      title: string;
      poster_url?: string | null;
      viewed_at: string;
    };

    // --- Manual history ---
    WatchedItemIn: {
      media_type: string;
      tmdb_id: number;
      season?: number | null;
      episode?: number | null;
      watched_at: string;  // ISO 8601 UTC
    };

    PostWatchedIn: {
      items: components["schemas"]["WatchedItemIn"][];
    };

    PostWatchedOut: {
      created: number;
      skipped: number;
      errors: string[];
    };
  };
}
