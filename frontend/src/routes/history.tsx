import { useState, useEffect, useCallback } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useSessions } from '@/api/queries'
import { StatusBadge } from '@/components/shared/StatusBadge'
import { TimeAgo } from '@/components/shared/TimeAgo'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { Download } from 'lucide-react'
import { formatEpisode, formatRuntime } from '@/lib/format'
import { apiClient } from '@/api/client'

const PAGE_SIZE = 50

export const Route = createFileRoute('/history')({
  validateSearch: (search: Record<string, unknown>) => ({
    q: (search.q as string) ?? '',
    type: (search.type as string) ?? '',
    status: (search.status as string) ?? '',
    date_from: (search.date_from as string) ?? '',
    date_to: (search.date_to as string) ?? '',
    page: Number(search.page ?? 1),
  }),
  component: HistoryPage,
})

function HistoryPage() {
  const navigate = Route.useNavigate()
  const search = Route.useSearch()
  const { q, type, status, date_from, date_to, page } = search

  // Debounce text search to avoid a query on every keystroke
  const [inputQ, setInputQ] = useState(q)
  useEffect(() => {
    const t = setTimeout(() => {
      navigate({ search: s => ({ ...s, q: inputQ, page: 1 }) })
    }, 300)
    return () => clearTimeout(t)
  }, [inputQ, navigate])

  const offset = (page - 1) * PAGE_SIZE
  const { data, isLoading, isError } = useSessions({
    limit: PAGE_SIZE,
    offset,
    search: q || null,
    type: type || null,
    status: status || null,
    date_from: date_from || null,
    date_to: date_to || null,
  })

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1

  // CSV export — fetches up to 500 rows with current filters
  const handleExport = useCallback(async () => {
    const { data: exportData } = await apiClient.GET('/api/sessions', {
      params: {
        query: {
          limit: 500,
          search: q || null,
          type: type || null,
          status: status || null,
          date_from: date_from || null,
          date_to: date_to || null,
        },
      },
    })
    if (!exportData) return
    const headers = ['title', 'show_title', 'season', 'episode', 'type', 'status', 'started_at', 'completed_at', 'runtime_s', 'percent']
    const rows = exportData.items.map(({ session, item }) => [
      `"${item.title.replace(/"/g, '""')}"`,
      `"${(item.show_title ?? '').replace(/"/g, '""')}"`,
      item.season ?? '',
      item.episode ?? '',
      item.media_type,
      session.status,
      session.started_at,
      session.completed_at ?? '',
      session.runtime_s ?? '',
      session.percent != null ? Math.round(session.percent) : '',
    ].join(','))
    const csv = [headers.join(','), ...rows].join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'logsaga.csv'
    a.click()
    URL.revokeObjectURL(url)
  }, [q, type, status, date_from, date_to])

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="History"
        subtitle={data ? `${data.total} sessions` : undefined}
        actions={
          <Button variant="outline" size="sm" onClick={handleExport} className="gap-2">
            <Download size={14} /> Export CSV
          </Button>
        }
      />

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-4">
        <Input
          placeholder="Search title or show…"
          value={inputQ}
          onChange={e => setInputQ(e.target.value)}
          className="w-56"
        />
        <Select
          value={type || 'all'}
          onValueChange={v => navigate({ search: s => ({ ...s, type: v === 'all' ? '' : v, page: 1 }) })}
        >
          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="movie">Movies</SelectItem>
            <SelectItem value="episode">TV</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={status || 'all'}
          onValueChange={v => navigate({ search: s => ({ ...s, status: v === 'all' ? '' : v, page: 1 }) })}
        >
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="completed">Watched</SelectItem>
            <SelectItem value="partial">Partial</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="date"
          value={date_from}
          onChange={e => navigate({ search: s => ({ ...s, date_from: e.target.value, page: 1 }) })}
          className="w-36"
          placeholder="From"
        />
        <Input
          type="date"
          value={date_to}
          onChange={e => navigate({ search: s => ({ ...s, date_to: e.target.value, page: 1 }) })}
          className="w-36"
          placeholder="To"
        />
      </div>

      {isError && <ErrorState />}

      {/* Table */}
      <div className="rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead className="hidden sm:table-cell">Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="hidden md:table-cell">Runtime</TableHead>
              <TableHead>When</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading &&
              Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                  <TableCell className="hidden sm:table-cell"><Skeleton className="h-4 w-16" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                  <TableCell className="hidden md:table-cell"><Skeleton className="h-4 w-12" /></TableCell>
                  <TableCell><Skeleton className="h-4 w-20" /></TableCell>
                </TableRow>
              ))}
            {!isLoading && data && data.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-muted-foreground py-10">
                  No results.
                </TableCell>
              </TableRow>
            )}
            {data?.items.map(({ session, item }) => (
              <TableRow
                key={session.id}
                className="cursor-pointer hover:bg-accent/50"
              >
                <TableCell>
                  <Link
                    to="/items/$itemId"
                    params={{ itemId: String(item.id) }}
                    className="hover:underline"
                  >
                    <div className="font-medium line-clamp-1">
                      {item.season != null && item.episode != null
                        ? `${formatEpisode(item.season, item.episode)} · ${item.title}`
                        : item.title}
                    </div>
                    {item.show_title && (
                      <div className="text-xs text-muted-foreground">{item.show_title}</div>
                    )}
                  </Link>
                </TableCell>
                <TableCell className="hidden sm:table-cell capitalize text-muted-foreground">
                  {item.media_type}
                </TableCell>
                <TableCell>
                  <StatusBadge status={session.status} completionReason={session.completion_reason} />
                </TableCell>
                <TableCell className="hidden md:table-cell text-muted-foreground">
                  {formatRuntime(session.runtime_s)}
                </TableCell>
                <TableCell>
                  <TimeAgo iso={session.last_activity_at} className="text-sm text-muted-foreground" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {data && totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => navigate({ search: s => ({ ...s, page: page - 1 }) })}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => navigate({ search: s => ({ ...s, page: page + 1 }) })}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
