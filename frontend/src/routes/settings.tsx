import { useState, useEffect, useRef } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import {
  useHealth,
  useRebuildSessions,
  useArtworkStatus,
  useCastStatus,
  useAdminSettings,
  useUpdateSettings,
  useJobs,
  useTriggerJob,
  useBackupList,
  useCreateBackup,
  useVerifyBackup,
  useRestoreBackup,
  useDeleteBackup,
  type AdminSettings,
  type JobState,
  type BackupInfoOut,
  type VerifyResultOut,
  type BackupTarget,
} from '@/api/queries'
import { PageHeader } from '@/components/shared/PageHeader'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  CheckCircle2, AlertCircle, XCircle, RefreshCw, Save, Play,
  Archive, Download, ShieldCheck, RotateCcw, Trash2, Plus,
} from 'lucide-react'

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
  fetch_artwork:    { label: 'Fetch Artwork',      desc: 'Download artwork for all library items (parallel)' },
  enrich_cast:      { label: 'Enrich Cast',        desc: 'Fetch cast from TMDB for items missing actor info' },
  enrich_tmdb_ids:  { label: 'Enrich TMDB IDs',   desc: 'Search TMDB to assign IDs to unmatched movies/shows' },
  rebuild_sessions: { label: 'Rebuild Sessions',  desc: 'Re-derive all sessions from raw observations' },
}

function SettingsPage() {
  const { data: health, isLoading } = useHealth()
  const rebuild = useRebuildSessions()
  const { data: artStatus } = useArtworkStatus()
  const { data: castStatus } = useCastStatus()
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
              <Row label="Version">
                <span className="font-mono text-xs">{health.version}</span>
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

      {/* Cast data progress card */}
      {castStatus && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="text-sm">Cast Data</CardTitle>
            <CardDescription className="text-xs">
              Actor credits from TMDB. Run <code>logsaga enrich-cast</code> or <code>logsaga enrich-episode-cast</code> to populate.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="Movies / episodes with cast">
              {castStatus.items_with_cast} / {castStatus.total_items}
            </Row>
            {castStatus.total_items > 0 && (
              <div className="space-y-1">
                <Progress
                  value={Math.round((castStatus.items_with_cast / castStatus.total_items) * 100)}
                  className="h-2"
                />
                <p className="text-xs text-muted-foreground text-right">
                  {Math.round((castStatus.items_with_cast / castStatus.total_items) * 100)}% items complete
                  {castStatus.pending_items > 0 && ` (${castStatus.pending_items} pending)`}
                </p>
              </div>
            )}
            <Row label="Shows with cast">
              {castStatus.shows_with_cast} / {castStatus.total_shows}
            </Row>
            {castStatus.total_shows > 0 && (
              <div className="space-y-1">
                <Progress
                  value={Math.round((castStatus.shows_with_cast / castStatus.total_shows) * 100)}
                  className="h-2"
                />
                <p className="text-xs text-muted-foreground text-right">
                  {Math.round((castStatus.shows_with_cast / castStatus.total_shows) * 100)}% shows complete
                  {castStatus.pending_shows > 0 && ` (${castStatus.pending_shows} pending)`}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

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

      {/* Backup & Restore card */}
      <BackupCard />
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

// ---------------------------------------------------------------------------
// BackupCard — Backup & Restore section
// ---------------------------------------------------------------------------
function BackupCard() {
  const { data: backupList, isLoading, refetch } = useBackupList()
  const createBackup = useCreateBackup()
  const deleteBackup = useDeleteBackup()
  const verifyMutation = useVerifyBackup()
  const restoreMutation = useRestoreBackup()

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [verifyResult, setVerifyResult] = useState<VerifyResultOut | null>(null)
  // Which backup was most recently verified (filename or "upload") — for spinner targeting
  const [verifyTarget, setVerifyTarget] = useState<string | null>(null)
  const [confirmRestore, setConfirmRestore] = useState<{ target: BackupTarget; label: string } | null>(null)
  const [statusMsg, setStatusMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const handleCreate = () => {
    setStatusMsg(null)
    createBackup.mutate(undefined, {
      onSuccess: (info) => {
        setStatusMsg({ type: 'success', text: `Backup created: ${info.filename}` })
        refetch()
      },
      onError: (err) => setStatusMsg({ type: 'error', text: `Create failed: ${(err as Error).message}` }),
    })
  }

  const handleVerify = (target: BackupTarget, label: string) => {
    setVerifyResult(null)
    setVerifyTarget(label)
    setStatusMsg(null)
    verifyMutation.mutate(target, {
      onSuccess: (result) => setVerifyResult(result),
      onError: (err) => setStatusMsg({ type: 'error', text: `Verify failed: ${(err as Error).message}` }),
    })
  }

  const handleRestore = (target: BackupTarget, label: string) => {
    setConfirmRestore({ target, label })
  }

  const confirmDoRestore = () => {
    if (!confirmRestore) return
    setConfirmRestore(null)
    setStatusMsg(null)
    restoreMutation.mutate(confirmRestore.target, {
      onSuccess: () => {
        setStatusMsg({ type: 'success', text: 'Restore complete — reloading…' })
        setTimeout(() => window.location.reload(), 2000)
      },
      onError: (err) => setStatusMsg({ type: 'error', text: `Restore failed: ${(err as Error).message}` }),
    })
  }

  const handleDelete = (filename: string) => {
    setStatusMsg(null)
    deleteBackup.mutate(filename, {
      onError: (err) => setStatusMsg({ type: 'error', text: `Delete failed: ${(err as Error).message}` }),
    })
  }

  return (
    <>
      <Card className="mb-4">
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Archive size={15} />
              Backup &amp; Restore
            </CardTitle>
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5 shrink-0"
              disabled={createBackup.isPending}
              onClick={handleCreate}
            >
              {createBackup.isPending
                ? <><RefreshCw size={12} className="animate-spin" /> Creating…</>
                : <><Plus size={12} /> Create Backup</>
              }
            </Button>
          </div>
          <CardDescription className="text-xs mt-1">
            Server backups are stored next to the database in a <code className="font-mono">backups/</code> folder.
            Downloads have passwords removed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">

          {/* Status / error message */}
          {statusMsg && (
            <p className={`text-xs ${statusMsg.type === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}>
              {statusMsg.text}
            </p>
          )}

          {/* Server backup list */}
          {isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : !backupList || backupList.backups.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No backups yet — click Create Backup to get started.
            </p>
          ) : (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                Server Backups
              </p>
              <p className="text-xs text-muted-foreground">
                Stored in: <code className="font-mono text-xs">{backupList.backup_dir}</code>
              </p>
              <div className="mt-2">
                {backupList.backups.map(b => (
                  <BackupRow
                    key={b.filename}
                    backup={b}
                    onVerify={() => handleVerify({ filename: b.filename }, b.filename)}
                    onRestore={() => handleRestore({ filename: b.filename }, b.filename)}
                    onDelete={() => handleDelete(b.filename)}
                    isVerifying={verifyMutation.isPending && verifyTarget === b.filename}
                    isRestoring={restoreMutation.isPending}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Upload section */}
          <div className="space-y-2 pt-2 border-t">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
              Upload a Backup
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={e => {
                setSelectedFile(e.target.files?.[0] ?? null)
                setVerifyResult(null)
              }}
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5"
                onClick={() => fileInputRef.current?.click()}
              >
                Choose .zip file
              </Button>
              {selectedFile && (
                <span className="text-xs text-muted-foreground truncate max-w-[200px]">
                  {selectedFile.name}
                </span>
              )}
            </div>
            {selectedFile && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={verifyMutation.isPending}
                  onClick={() => handleVerify({ file: selectedFile }, 'upload')}
                >
                  {verifyMutation.isPending && verifyTarget === 'upload'
                    ? <><RefreshCw size={12} className="animate-spin" /> Verifying…</>
                    : <><ShieldCheck size={12} /> Verify</>
                  }
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={restoreMutation.isPending}
                  onClick={() => handleRestore({ file: selectedFile }, selectedFile.name)}
                >
                  <RotateCcw size={12} className="mr-1" />
                  Restore
                </Button>
              </div>
            )}
          </div>

          {/* Verify result panel */}
          {verifyResult && (
            <div className={`rounded-md border p-3 space-y-1.5 text-sm ${
              verifyResult.ok
                ? 'border-green-500/30 bg-green-500/5'
                : 'border-destructive/30 bg-destructive/5'
            }`}>
              <div className="flex items-center gap-2">
                {verifyResult.ok
                  ? <CheckCircle2 size={14} className="text-green-500 shrink-0" />
                  : <XCircle size={14} className="text-destructive shrink-0" />
                }
                <span className="font-medium text-xs">
                  {verifyResult.ok ? 'Backup OK' : `Verification failed: ${verifyResult.error}`}
                </span>
              </div>
              {verifyResult.ok && (
                <>
                  <div className="flex items-center gap-2 flex-wrap">
                    {verifyResult.app_compatible
                      ? <Badge variant="default" className="text-xs">Schema compatible</Badge>
                      : <Badge variant="destructive" className="text-xs">Schema mismatch — cannot restore safely</Badge>
                    }
                    {verifyResult.schema_version && (
                      <span className="text-xs text-muted-foreground font-mono">{verifyResult.schema_version}</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {[
                      verifyResult.sessions != null && `${verifyResult.sessions.toLocaleString()} sessions`,
                      verifyResult.media_items != null && `${verifyResult.media_items.toLocaleString()} items`,
                      verifyResult.shows != null && `${verifyResult.shows.toLocaleString()} shows`,
                    ].filter(Boolean).join(' · ')}
                  </p>
                  {verifyResult.credentials_stripped && (
                    <p className="text-xs text-muted-foreground">
                      Passwords not included in this file (stripped before download).
                    </p>
                  )}
                </>
              )}
            </div>
          )}

        </CardContent>
      </Card>

      {/* Restore confirmation dialog */}
      <Dialog open={confirmRestore !== null} onOpenChange={open => { if (!open) setConfirmRestore(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Restore</DialogTitle>
            <DialogDescription>
              This will permanently replace <strong>all current data</strong> with the contents
              of this backup. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {confirmRestore && (
            <p className="text-sm text-muted-foreground font-mono break-all">{confirmRestore.label}</p>
          )}
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setConfirmRestore(null)}
              disabled={restoreMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={restoreMutation.isPending}
              onClick={confirmDoRestore}
            >
              {restoreMutation.isPending
                ? <><RefreshCw size={12} className="animate-spin mr-1" /> Restoring…</>
                : 'Restore — this cannot be undone'
              }
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ---------------------------------------------------------------------------
// BackupRow — one row in the server backups list
// ---------------------------------------------------------------------------
function BackupRow({
  backup,
  onVerify,
  onRestore,
  onDelete,
  isVerifying,
  isRestoring,
}: {
  backup: BackupInfoOut
  onVerify: () => void
  onRestore: () => void
  onDelete: () => void
  isVerifying: boolean
  isRestoring: boolean
}) {
  const dateStr = backup.created_at
    ? new Date(backup.created_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
    : '—'
  const sizeStr = formatBytes(backup.size_bytes)

  return (
    <div className="flex items-start gap-2 py-2 border-b last:border-0">
      <div className="flex-1 min-w-0 space-y-0.5">
        <p className="text-xs font-mono truncate">{backup.filename}</p>
        <p className="text-xs text-muted-foreground">
          {dateStr} · {sizeStr}
          {backup.sessions != null && ` · ${backup.sessions.toLocaleString()} sessions`}
        </p>
      </div>
      <div className="flex items-center gap-0.5 shrink-0">
        {/* Download — anchor tag so browser handles the file download */}
        <a
          href={`/api/admin/backup/${encodeURIComponent(backup.filename)}/download`}
          download
          title="Download (passwords removed)"
        >
          <Button size="sm" variant="ghost" className="h-7 w-7 p-0">
            <Download size={12} />
          </Button>
        </a>
        <Button
          size="sm" variant="ghost" className="h-7 w-7 p-0"
          onClick={onVerify} disabled={isVerifying}
          title="Verify backup integrity"
        >
          {isVerifying
            ? <RefreshCw size={12} className="animate-spin" />
            : <ShieldCheck size={12} />
          }
        </Button>
        <Button
          size="sm" variant="ghost" className="h-7 w-7 p-0"
          onClick={onRestore} disabled={isRestoring}
          title="Restore from this backup"
        >
          <RotateCcw size={12} />
        </Button>
        <Button
          size="sm" variant="ghost" className="h-7 w-7 p-0 text-destructive hover:text-destructive"
          onClick={onDelete}
          title="Delete backup"
        >
          <Trash2 size={12} />
        </Button>
      </div>
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// ---------------------------------------------------------------------------
// Row — generic label/value layout row used throughout this page
// ---------------------------------------------------------------------------
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="text-muted-foreground capitalize shrink-0">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  )
}
