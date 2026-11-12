/**
 * AddToHistoryDialog — modal to mark items as watched with a date choice.
 *
 * Three date options:
 *   1. Just Finished — current datetime (default)
 *   2. Release Date  — movie release / episode air date; for seasons/shows
 *                      each episode uses its own air date
 *   3. Other Date    — custom date+time picker
 *
 * Props:
 *   items         — list of WatchedItemIn to submit (pre-built by caller)
 *   releaseDate   — YYYY-MM-DD to pre-fill "Release Date" option (single items)
 *   releaseDates  — {season, episode} → YYYY-MM-DD map (for batch)
 *   label         — button label text, e.g. "Add Season 2 (22 episodes)"
 *   onSuccess     — called with the PostWatchedOut result on success
 */

import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { usePostWatched } from '@/api/queries'
import type { WatchedItemIn } from '@/api/queries'
import type { components } from '@/api/types'

type PostWatchedOut = components['schemas']['PostWatchedOut']

export type DateMode = 'now' | 'release' | 'custom'

interface AddToHistoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Items to submit — watched_at will be overwritten based on the date mode */
  items: WatchedItemIn[]
  /** Release date for the item (YYYY-MM-DD), used when mode=release */
  releaseDate?: string | null
  /**
   * Map of release dates per episode for batch adds.
   * Key: `${season}-${episode}`, value: YYYY-MM-DD
   * When provided, episodes without a date are skipped in release mode.
   */
  releaseDates?: Map<string, string>
  /** Number of episodes without a release date (shown as warning) */
  missingReleaseDates?: number
  /** Label shown on the submit button */
  label?: string
  onSuccess?: (result: PostWatchedOut) => void
}

function formatLocalISO(d: Date): string {
  // Return the date in local time as ISO string with timezone offset
  return d.toISOString()
}

function dateToUTCISO(dateStr: string, timeStr = '20:00'): string {
  // Parse a YYYY-MM-DD + HH:mm as local time, convert to UTC ISO
  const d = new Date(`${dateStr}T${timeStr}:00`)
  return formatLocalISO(d)
}

export function AddToHistoryDialog({
  open,
  onOpenChange,
  items,
  releaseDate,
  releaseDates,
  missingReleaseDates = 0,
  label = 'Add to History',
  onSuccess,
}: AddToHistoryDialogProps) {
  const [mode, setMode] = useState<DateMode>('now')
  const [customDate, setCustomDate] = useState(() => {
    const d = new Date()
    return d.toISOString().slice(0, 10) // YYYY-MM-DD
  })
  const [customTime, setCustomTime] = useState('20:00')
  const [result, setResult] = useState<PostWatchedOut | null>(null)

  const mutation = usePostWatched()
  const isBatch = items.length > 1

  function buildItems(): WatchedItemIn[] {
    if (mode === 'now') {
      const ts = new Date().toISOString()
      return items.map(it => ({ ...it, watched_at: ts }))
    }

    if (mode === 'release') {
      if (releaseDates) {
        // Batch mode: each episode gets its own air date; skip those without one
        const result: WatchedItemIn[] = []
        for (const it of items) {
          const key = `${it.season}-${it.episode}`
          const rd = releaseDates.get(key)
          if (rd) {
            result.push({ ...it, watched_at: dateToUTCISO(rd) })
          }
        }
        return result
      }
      if (releaseDate) {
        return items.map(it => ({ ...it, watched_at: dateToUTCISO(releaseDate) }))
      }
      // No release date available — fall back to now
      const ts = new Date().toISOString()
      return items.map(it => ({ ...it, watched_at: ts }))
    }

    // mode === 'custom'
    const ts = dateToUTCISO(customDate, customTime)
    return items.map(it => ({ ...it, watched_at: ts }))
  }

  async function handleSubmit() {
    const payload = buildItems()
    if (payload.length === 0) {
      setResult({ created: 0, skipped: items.length, errors: ['No episodes with release dates found'] })
      return
    }
    try {
      const res = await mutation.mutateAsync(payload)
      setResult(res)
      onSuccess?.(res)
    } catch (e) {
      setResult({ created: 0, skipped: 0, errors: [String(e)] })
    }
  }

  function handleClose() {
    setResult(null)
    setMode('now')
    onOpenChange(false)
  }

  const hasRelease = !!releaseDate || (releaseDates && releaseDates.size > 0)

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add to Watched History</DialogTitle>
        </DialogHeader>

        {result ? (
          // Result view
          <div className="space-y-3 py-2">
            <div className="text-sm space-y-1">
              {result.created > 0 && (
                <p className="text-green-400">✓ {result.created} session{result.created !== 1 ? 's' : ''} added</p>
              )}
              {result.skipped > 0 && (
                <p className="text-muted-foreground">{result.skipped} skipped</p>
              )}
              {result.errors.length > 0 && (
                <ul className="text-destructive text-xs space-y-0.5 mt-1">
                  {result.errors.slice(0, 5).map((e, i) => (
                    <li key={i}>• {e}</li>
                  ))}
                  {result.errors.length > 5 && (
                    <li className="text-muted-foreground">…and {result.errors.length - 5} more</li>
                  )}
                </ul>
              )}
            </div>
            <DialogFooter>
              <Button onClick={handleClose}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          // Date picker view
          <div className="space-y-4 py-2">
            {/* Date mode selection */}
            <div className="space-y-2.5">
              {/* Just Finished */}
              <label className="flex items-start gap-3 cursor-pointer group">
                <input
                  type="radio"
                  name="date-mode"
                  value="now"
                  checked={mode === 'now'}
                  onChange={() => setMode('now')}
                  className="mt-0.5 accent-primary"
                />
                <div>
                  <p className="text-sm font-medium">Just Finished</p>
                  <p className="text-xs text-muted-foreground">Mark as watched right now</p>
                </div>
              </label>

              {/* Release Date */}
              <label className={`flex items-start gap-3 ${hasRelease ? 'cursor-pointer' : 'opacity-40 cursor-not-allowed'} group`}>
                <input
                  type="radio"
                  name="date-mode"
                  value="release"
                  checked={mode === 'release'}
                  onChange={() => setMode('release')}
                  disabled={!hasRelease}
                  className="mt-0.5 accent-primary"
                />
                <div>
                  <p className="text-sm font-medium">Release Date</p>
                  {isBatch ? (
                    <p className="text-xs text-muted-foreground">
                      Each episode uses its original air date
                      {missingReleaseDates > 0 && (
                        <span className="text-amber-400 ml-1">
                          ({missingReleaseDates} episode{missingReleaseDates !== 1 ? 's' : ''} without air date will be skipped)
                        </span>
                      )}
                    </p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {releaseDate ?? 'No release date available'}
                    </p>
                  )}
                </div>
              </label>

              {/* Custom Date */}
              <label className="flex items-start gap-3 cursor-pointer group">
                <input
                  type="radio"
                  name="date-mode"
                  value="custom"
                  checked={mode === 'custom'}
                  onChange={() => setMode('custom')}
                  className="mt-0.5 accent-primary"
                />
                <div className="flex-1">
                  <p className="text-sm font-medium">Other Date</p>
                  {mode === 'custom' && (
                    <div className="flex gap-2 mt-1.5">
                      <input
                        type="date"
                        value={customDate}
                        onChange={e => setCustomDate(e.target.value)}
                        className="flex-1 bg-input border border-border rounded px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-ring"
                      />
                      <input
                        type="time"
                        value={customTime}
                        onChange={e => setCustomTime(e.target.value)}
                        className="w-24 bg-input border border-border rounded px-2 py-1 text-sm outline-none focus:ring-1 focus:ring-ring"
                      />
                    </div>
                  )}
                </div>
              </label>
            </div>

            {/* Summary */}
            {isBatch && (
              <p className="text-xs text-muted-foreground">
                Adding {items.length} episode{items.length !== 1 ? 's' : ''}
                {mode === 'release' && missingReleaseDates > 0
                  ? ` (${items.length - missingReleaseDates} with dates)`
                  : ''}
              </p>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={handleClose}>Cancel</Button>
              <Button
                onClick={handleSubmit}
                disabled={mutation.isPending}
              >
                {mutation.isPending ? 'Adding…' : label}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
