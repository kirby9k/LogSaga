import { useState, useMemo } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useStats } from '@/api/queries'
import { PageHeader } from '@/components/shared/PageHeader'
import { ErrorState } from '@/components/shared/ErrorState'
import { LoadingSkeleton } from '@/components/shared/LoadingSkeleton'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts'
import { format, subDays } from 'date-fns'

export const Route = createFileRoute('/stats')({
  component: StatsPage,
})

type Days = 30 | 90 | 365

function StatsPage() {
  const [days, setDays] = useState<Days>(30)
  const { data, isLoading, isError } = useStats(days)

  // Gap-fill: generate a full date range and merge API data (missing days → 0 minutes)
  const chartData = useMemo(() => {
    if (!data) return []
    const apiMap = new Map(data.daily.map(d => [d.date, d.minutes_watched]))
    const today = new Date()
    return Array.from({ length: days }).map((_, i) => {
      const date = subDays(today, days - 1 - i)
      const key = format(date, 'yyyy-MM-dd')
      return {
        date: key,
        label: format(date, 'MMM d'),
        minutes: apiMap.get(key) ?? 0,
      }
    })
  }, [data, days])

  const chartConfig = {
    minutes: { label: 'Minutes', color: 'hsl(var(--chart-1))' },
  }

  return (
    <div className="p-4 md:p-6">
      <PageHeader
        title="Statistics"
        actions={
          <div className="flex gap-1">
            {([30, 90, 365] as Days[]).map(d => (
              <Button
                key={d}
                size="sm"
                variant={days === d ? 'default' : 'ghost'}
                onClick={() => setDays(d)}
              >
                {d === 365 ? '1y' : `${d}d`}
              </Button>
            ))}
          </div>
        }
      />

      {isLoading && <LoadingSkeleton count={4} variant="row" />}
      {isError && <ErrorState />}

      {data && (
        <div className="space-y-6">
          {/* Summary cards — row 1 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Watch Time
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">
                  {Math.round(data.total_minutes_watched / 60)}h
                </p>
                <p className="text-xs text-muted-foreground">{data.total_minutes_watched} min total</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Movies
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{data.movies_watched}</p>
                <p className="text-xs text-muted-foreground">
                  {Math.round(data.movie_minutes / 60)}h watched
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Episodes
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{data.episodes_watched}</p>
                <p className="text-xs text-muted-foreground">
                  {Math.round(data.episode_minutes / 60)}h watched
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Completion
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">
                  {Math.round(data.completion_rate * 100)}%
                </p>
                <p className="text-xs text-muted-foreground">{data.partial_sessions} partial</p>
              </CardContent>
            </Card>
          </div>

          {/* Summary cards — row 2 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Rewatches
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{data.total_rewatches}</p>
                <p className="text-xs text-muted-foreground">items watched 2× or more</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Longest Session
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">
                  {data.longest_session_minutes >= 60
                    ? `${Math.round(data.longest_session_minutes / 60)}h`
                    : `${data.longest_session_minutes}m`}
                </p>
                <p className="text-xs text-muted-foreground">single session</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Current Streak
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{data.current_streak_days}</p>
                <p className="text-xs text-muted-foreground">days in a row</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-xs text-muted-foreground font-normal uppercase tracking-wider">
                  Longest Streak
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{data.longest_streak_days}</p>
                <p className="text-xs text-muted-foreground">days all-time</p>
              </CardContent>
            </Card>
          </div>

          {/* Bar chart */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Daily Watch Time (minutes)</CardTitle>
            </CardHeader>
            <CardContent>
              <ChartContainer config={chartConfig} className="h-48 w-full">
                <BarChart data={chartData} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    interval={days <= 30 ? 6 : days <= 90 ? 13 : 60}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    axisLine={false}
                    tickLine={false}
                    width={28}
                  />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <Bar dataKey="minutes" fill="var(--color-minutes)" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ChartContainer>
            </CardContent>
          </Card>

          {/* Top shows */}
          {data.top_shows.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Top Shows</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Show</TableHead>
                      <TableHead className="text-right">Episodes</TableHead>
                      <TableHead className="text-right">Watch Time</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.top_shows.map(show => (
                      <TableRow key={show.show_title}>
                        <TableCell className="font-medium">
                          {show.show_id ? (
                            <Link
                              to="/shows/$showId"
                              params={{ showId: String(show.show_id) }}
                              className="hover:text-primary hover:underline transition-colors"
                            >
                              {show.show_title}
                            </Link>
                          ) : (
                            show.show_title
                          )}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {show.watched_episodes}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {Math.round(show.total_minutes / 60)}h {show.total_minutes % 60}m
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
