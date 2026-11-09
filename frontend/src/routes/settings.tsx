import { useState, useEffect } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import {
  useHealth,
  useRebuildSessions,
  useArtworkStatus,
  useAdminSettings,
  useUpdateSettings,
  useJobs,
  useTriggerJob,
  type AdminSettings,
  type JobState,
} from '@/api/queries'
import { PageHeader } from '@/components/shared/PageHeader'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { CheckCircle2, AlertCircle, XCircle, RefreshCw, Save, Play } from 'lucide-react'

export const Route = createFileRoute('/settings')({
  component: SettingsPage,
})

// Human-readable labels and descriptions for each editable setting
const SETTING_META: Record<keyof AdminSettings, { label: string; desc: string; unit?: string }> = {
  min_started_seconds:              { label: 'Min start seconds',       desc: 'Minimum position (s) before a partial session is kept, not ignored', unit: 's' },
  session_gap_hours:                { label: 'Session gap',              desc: 'Max hours of inactivity before a new session is opened',              unit: 'h' },
  watched_percent:                  { label: 'Watched threshold',        desc: 'Completion % that marks a session as watched',                       unit: '%' },
  watched_remaining_minutes_movie:  { label: 'Credits window (movie)',   desc: 'Minutes from end of a movie that also counts as watched',             unit: 'min' },
  watched_remaining_minutes_episode:{ label: 'Credits window (episode)', desc: 'Minutes from end of an episode that also counts as watched',          unit: 'min' },
  poll_seconds:                     { label: 'Poll interval',            desc: 'How often to check Kodi for new play activity',                      unit: 's' },
  library_sync_hours:               { label: 'Library sync interval',    desc: 'How often to sync the full Kodi library into the local database',    unit: 'h' },
}

// Job labels and descriptions shown in the Maintenance Jobs card
const JOB_META: Record<string, { label: string; desc: string }> = {
  poll:             { label: 'Poll',              desc: 'Check Kodi for new play activity immediately' },
  sync_library:     { label: 'Sync Library',      desc: 'Refresh movies, episodes and shows from Kodi' },
  fetch_artwork:    { label: 'Fetch Artwork',      desc: 'Download missing artwork for all library items' },
  enrich_metadata:  { label: 'Enrich Metadata',   desc: 'Backfill plot, year, genres and codec info from Kodi' },
  rebuild_sessions: { label: 'Rebuild Sessions',  desc: 'Re-derive all sessions from raw observations' },
}

function SettingsPage() {
  const { data: health, isLoading } = useHealth()
  const rebuild = useRebuildSessions()
  const { data: artStatus } = useArtworkStatus()
  const { data: settingsData, isLoading: settingsLoading } = useAdminSettings()
  const updateSettings = useUpdateSettings()
  const { data: jobs, refetch: refetchJobs } = useJobs()

  // Local draft for the form — initialised from server when data arrives
  const [draft, setDraft] = useState<Partial<AdminSettings>>({})
  const [saveMsg, setSaveMsg] = useState<string | null>(null)

  useEffect(() => {
    if (settingsData && Object.keys(draft).length === 0) {
      setDraft(settingsData)
    }
  }, [settingsData]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = (key: keyof AdminSettings, raw: string) => {
    const v = raw.includes('.') ? parseFloat(raw) : parseInt(raw, 10)
    setDraft(d => ({ ...d, [key]: isNaN(v) ? raw : v }))
  }

  const handleSave = () => {
    setSaveMsg(null)
    updateSettings.mutate(draft as AdminSettings, {
      onSuccess: () => setSaveMsg('Settings saved. Changes take effect on the next poll/sync cycle.'),
      onError: () => setSaveMsg('Save failed — check that all values are in the allowed range.'),
    })
  }

  const statusIcon = health?.status === 'ok'
    ? <CheckCircle2 className="text-green-500" size={18} />
    : health?.status === 'degraded'
    ? <AlertCircle className="text-yellow-500" size={18} />
    : <XCircle className="text-red-500" size={18} />

  return (
    <div className="p-4 md:p-6 max-w-2xl">
      <PageHeader title="Settings / Status" />

      {/* Sync health card */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            {!isLoading && statusIcon}
            Sync Status
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {isLoading ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : health ? (
            <>
              <Row label="Status">
                <Badge variant={health.status === 'ok' ? 'default' : 'secondary'}>
                  {health.status}
                </Badge>
              </Row>
              {health.kodi_db_name && (
                <Row label="Kodi DB"><span className="font-mono text-xs">{health.kodi_db_name}</span></Row>
              )}
              <Row label="Last success"><TimeAgo iso={health.last_success_at} /></Row>
              <Row label="Last poll"><TimeAgo iso={health.last_poll_at} /></Row>
              <Row label="Polls">{health.poll_count}</Row>
              <Row label="Observations">{health.observation_count}</Row>
              <Row label="Errors">{health.error_count}</Row>
              {health.last_error && (
                <Row label="Last error">
                  <span className="text-destructive text-xs">{health.last_error}</span>
                </Row>
              )}
            </>
          ) : (
            <p className="text-muted-foreground">No sync data yet.</p>
          )}
        </CardContent>
      </Card>

      {/* Maintenance Jobs card */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm">Maintenance Jobs</CardTitle>
          <CardDescription className="text-xs">
            Run jobs manually. Jobs run in the background — the page stays responsive.
            To restart the app use <code className="font-mono">docker restart logsaga</code> from the Unraid console.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {jobs
            ? jobs.map(job => (
                <JobRow
                  key={job.name}
                  job={job}
                  meta={JOB_META[job.name]}
                  onRun={async () => { await refetchJobs() }}
                  isRebuild={job.name === 'rebuild_sessions'}
                  rebuild={rebuild}
                />
              ))
            : Object.keys(JOB_META).map(name => (
                <JobRow
                  key={name}
                  job={{ name, running: false, last_run: null, last_result: null }}
                  meta={JOB_META[name]}
                  onRun={async () => { await refetchJobs() }}
                  isRebuild={name === 'rebuild_sessions'}
                  rebuild={rebuild}
                />
              ))
          }
        </CardContent>
      </Card>

      {/* Editable session rules card */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm">Session Rules</CardTitle>
          <CardDescription className="text-xs">
            Changes take effect on the next poll or sync cycle. Credentials and paths must
            be changed in your .env file.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {settingsLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {(Object.keys(SETTING_META) as (keyof AdminSettings)[]).map(key => {
                  const meta = SETTING_META[key]
                  const val = draft[key] ?? ''
                  return (
                    <div key={key} className="space-y-1">
                      <Label htmlFor={key} className="text-xs font-medium">
                        {meta.label}
                        {meta.unit && (
                          <span className="text-muted-foreground ml-1">({meta.unit})</span>
                        )}
                      </Label>
                      <Input
                        id={key}
                        type="number"
                        step={key === 'session_gap_hours' || key === 'library_sync_hours' ? '0.1' : '1'}
                        value={val}
                        onChange={e => handleChange(key, e.target.value)}
                        className="h-8 text-sm font-mono"
                      />
                      <p className="text-xs text-muted-foreground">{meta.desc}</p>
                    </div>
                  )
                })}
              </div>

              <div className="flex items-center gap-3 pt-2">
                <Button
                  onClick={handleSave}
                  disabled={updateSettings.isPending}
                  size="sm"
                  className="gap-2"
                >
                  {updateSettings.isPending
                    ? <><RefreshCw size={13} className="animate-spin" /> Saving…</>
                    : <><Save size={13} /> Save Changes</>}
                </Button>
                {saveMsg && (
                  <p className={`text-xs ${updateSettings.isError ? 'text-destructive' : 'text-muted-foreground'}`}>
                    {saveMsg}
                  </p>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Artwork progress card */}
      {artStatus && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm">Artwork Cache</CardTitle>
            <CardDescription className="text-xs">
              Downloaded artwork for media items.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="Items with artwork">
              {artStatus.items_with_artwork} / {artStatus.total_items}
            </Row>
            <Row label="Artwork rows">{artStatus.total_artwork_rows}</Row>
            <Row label="Pending">{artStatus.pending}</Row>
            {artStatus.total_items > 0 && (
              <div className="space-y-1">
                <Progress
                  value={Math.round((artStatus.items_with_artwork / artStatus.total_items) * 100)}
                  className="h-2"
                />
                <p className="text-xs text-muted-foreground text-right">
                  {Math.round((artStatus.items_with_artwork / artStatus.total_items) * 100)}% complete
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// JobRow — one row in the Maintenance Jobs card
// ---------------------------------------------------------------------------
function JobRow({
  job,
  meta,
  onRun,
  isRebuild,
  rebuild,
}: {
  job: JobState
  meta: { label: string; desc: string } | undefined
  onRun: () => Promise<void>
  isRebuild: boolean
  rebuild: ReturnType<typeof useRebuildSessions>
}) {
  const trigger = useTriggerJob(job.name)

  const handleRun = () => {
    if (isRebuild) {
      rebuild.mutate(undefined, { onSettled: onRun })
    } else {
      trigger.mutate(undefined, { onSettled: onRun })
    }
  }

  const isRunning = isRebuild ? rebuild.isPending : job.running
  const lastResult = isRebuild
    ? (rebuild.isSuccess && rebuild.data ? rebuild.data.message : rebuild.isError ? 'Rebuild failed.' : null)
    : job.last_result

  return (
    <div className="flex items-start gap-3 py-2 border-b last:border-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{meta?.label ?? job.name}</span>
          {isRunning && (
            <Badge variant="secondary" className="text-xs gap-1">
              <RefreshCw size={10} className="animate-spin" /> Running
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{meta?.desc}</p>
        {!isRunning && lastResult && (
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{lastResult}</p>
        )}
        {!isRunning && !isRebuild && job.last_run && (
          <p className="text-xs text-muted-foreground">
            Last run: <TimeAgo iso={job.last_run} />
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant="outline"
        className="gap-1.5 shrink-0"
        disabled={isRunning}
        onClick={handleRun}
      >
        {isRunning
          ? <><RefreshCw size={12} className="animate-spin" /> Running…</>
          : <><Play size={12} /> Run</>
        }
      </Button>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-muted-foreground capitalize shrink-0">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  )
}
