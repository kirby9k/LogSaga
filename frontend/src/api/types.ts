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
  };
}
