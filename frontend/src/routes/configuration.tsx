import { useState, useEffect } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import {
  useAdminConfig,
  useUpdateConfigKodi,
  useUpdateConfigJSONRPC,
  useUpdateConfigTMDB,
  useUpdateConfigMQTT,
  useUpdateConfigApp,
  type ConnectionTestResult,
} from '@/api/queries'
import { PageHeader } from '@/components/shared/PageHeader'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { CheckCircle2, AlertCircle, MinusCircle, RefreshCw, Save, Eye, EyeOff, Info } from 'lucide-react'

export const Route = createFileRoute('/configuration')({
  component: ConfigurationPage,
})

// ---------------------------------------------------------------------------
// Shared helper components
// ---------------------------------------------------------------------------

/** Show/hide password input with optional "currently set" label. */
function PasswordInput({
  id,
  value,
  onChange,
  placeholder = 'Enter new password to change',
  isSet = false,
}: {
  id: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  isSet?: boolean
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="space-y-1">
      <div className="relative">
        <Input
          id={id}
          type={show ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="h-8 text-sm font-mono pr-8"
          autoComplete="new-password"
        />
        <button
          type="button"
          onClick={() => setShow(s => !s)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
          tabIndex={-1}
          aria-label={show ? 'Hide password' : 'Show password'}
        >
          {show ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      </div>
      {isSet && value === '' && (
        <p className="text-xs text-muted-foreground">Currently set — leave blank to keep existing</p>
      )}
    </div>
  )
}

/** Displays the result of a save + connection test. */
function TestResult({ result }: { result: ConnectionTestResult | null }) {
  if (!result) return null
  if (result.status === 'ok') {
    return (
      <div className="flex items-center gap-1.5 text-xs text-green-500">
        <CheckCircle2 size={12} />
        {result.message}
      </div>
    )
  }
  if (result.status === 'error') {
    return (
      <div className="flex items-start gap-1.5 text-xs text-destructive">
        <AlertCircle size={12} className="mt-0.5 shrink-0" />
        {result.message}
      </div>
    )
  }
  // not_configured
  return (
    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
      <MinusCircle size={12} />
      {result.message}
    </div>
  )
}

/** Blue info box for optional integration sections. */
function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-2 rounded-md bg-blue-500/10 border border-blue-500/20 px-3 py-2 text-xs text-blue-300/90 mb-3">
      <Info size={13} className="shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  )
}

/** SaveButton with pending spinner. */
function SaveButton({ pending }: { pending: boolean }) {
  return (
    <Button size="sm" type="submit" className="gap-2" disabled={pending}>
      {pending
        ? <><RefreshCw size={13} className="animate-spin" /> Saving…</>
        : <><Save size={13} /> Save</>
      }
    </Button>
  )
}

// ---------------------------------------------------------------------------
// Configuration page
// ---------------------------------------------------------------------------
function ConfigurationPage() {
  const { data: config, isLoading } = useAdminConfig()
  const updateKodi    = useUpdateConfigKodi()
  const updateJSONRPC = useUpdateConfigJSONRPC()
  const updateTMDB    = useUpdateConfigTMDB()
  const updateMQTT    = useUpdateConfigMQTT()
  const updateApp     = useUpdateConfigApp()

  // Kodi DB form state
  const [kodi, setKodi] = useState({ host: '', port: 3306, user: 'logsaga', password: '', db_name: '' })
  const [kodiResult, setKodiResult] = useState<ConnectionTestResult | null>(null)

  // JSON-RPC form state
  const [jsonrpc, setJsonrpc] = useState({ url: '', user: '', password: '' })
  const [jsonrpcResult, setJsonrpcResult] = useState<ConnectionTestResult | null>(null)

  // TMDB form state
  const [tmdb, setTmdb] = useState({ api_key: '' })
  const [tmdbResult, setTmdbResult] = useState<ConnectionTestResult | null>(null)

  // MQTT form state
  const [mqtt, setMqtt] = useState({ host: '', port: 1883, user: '', password: '', discovery_prefix: 'homeassistant' })
  const [mqttResult, setMqttResult] = useState<ConnectionTestResult | null>(null)

  // App form state
  const [appCfg, setAppCfg] = useState({ public_base_url: 'http://localhost:8790', log_level: 'INFO', tz: 'America/Los_Angeles' })
  const [appResult, setAppResult] = useState<ConnectionTestResult | null>(null)

  // Initialise form from server data (once, when data first arrives)
  useEffect(() => {
    if (!config) return
    setKodi(k => k.host === '' ? {
      host: config.kodi.host,
      port: config.kodi.port,
      user: config.kodi.user,
      password: '',
      db_name: config.kodi.db_name,
    } : k)
    setJsonrpc(j => j.url === '' ? {
      url: config.jsonrpc.url,
      user: config.jsonrpc.user,
      password: '',
    } : j)
    setMqtt(m => m.host === '' && m.discovery_prefix === 'homeassistant' ? {
      host: config.mqtt.host,
      port: config.mqtt.port,
      user: config.mqtt.user,
      password: '',
      discovery_prefix: config.mqtt.discovery_prefix,
    } : m)
    setAppCfg(a => a.public_base_url === 'http://localhost:8790' ? {
      public_base_url: config.app.public_base_url,
      log_level: config.app.log_level,
      tz: config.app.tz,
    } : a)
  }, [config]) // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading) {
    return (
      <div className="p-4 md:p-6 max-w-2xl">
        <PageHeader title="Configuration" />
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Submit handlers
  // ---------------------------------------------------------------------------
  const handleKodiSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateKodi.mutate(
      { host: kodi.host, port: kodi.port, user: kodi.user, password: kodi.password || null, db_name: kodi.db_name },
      { onSuccess: r => setKodiResult(r), onError: () => setKodiResult({ status: 'error', message: 'Save failed.' }) },
    )
  }

  const handleJsonrpcSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateJSONRPC.mutate(
      { url: jsonrpc.url, user: jsonrpc.user, password: jsonrpc.password || null },
      { onSuccess: r => setJsonrpcResult(r), onError: () => setJsonrpcResult({ status: 'error', message: 'Save failed.' }) },
    )
  }

  const handleTmdbSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateTMDB.mutate(
      { api_key: tmdb.api_key || null },
      { onSuccess: r => setTmdbResult(r), onError: () => setTmdbResult({ status: 'error', message: 'Save failed.' }) },
    )
  }

  const handleMqttSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateMQTT.mutate(
      { host: mqtt.host, port: mqtt.port, user: mqtt.user, password: mqtt.password || null, discovery_prefix: mqtt.discovery_prefix },
      { onSuccess: r => setMqttResult(r), onError: () => setMqttResult({ status: 'error', message: 'Save failed.' }) },
    )
  }

  const handleAppSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateApp.mutate(
      { public_base_url: appCfg.public_base_url, log_level: appCfg.log_level, tz: appCfg.tz },
      { onSuccess: r => setAppResult(r), onError: () => setAppResult({ status: 'error', message: 'Save failed.' }) },
    )
  }

  return (
    <div className="p-4 md:p-6 max-w-2xl">
      <PageHeader title="Configuration" />

      {/* ------------------------------------------------------------------ */}
      {/* Kodi Database                                                        */}
      {/* ------------------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            Kodi Database
            <Badge variant="outline" className="text-xs font-normal">Required</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            LogSAGA reads your Kodi library from MariaDB. The account must have SELECT-only
            access on <code>MyVideos%.*</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleKodiSave} className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Host" id="kodi-host">
                <Input id="kodi-host" value={kodi.host} onChange={e => setKodi(k => ({ ...k, host: e.target.value }))}
                  placeholder="192.168.1.x" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Port" id="kodi-port">
                <Input id="kodi-port" type="number" value={kodi.port}
                  onChange={e => setKodi(k => ({ ...k, port: parseInt(e.target.value) || 3306 }))}
                  className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Username" id="kodi-user">
                <Input id="kodi-user" value={kodi.user} onChange={e => setKodi(k => ({ ...k, user: e.target.value }))}
                  className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Password" id="kodi-password">
                <PasswordInput id="kodi-password" value={kodi.password}
                  onChange={v => setKodi(k => ({ ...k, password: v }))}
                  isSet={config?.kodi.password_is_set} />
              </Field>
              <Field label="Database name" id="kodi-db-name"
                desc="Leave blank for auto-detect (recommended)">
                <Input id="kodi-db-name" value={kodi.db_name}
                  onChange={e => setKodi(k => ({ ...k, db_name: e.target.value }))}
                  placeholder="auto-detect" className="h-8 text-sm font-mono" />
              </Field>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <SaveButton pending={updateKodi.isPending} />
              <TestResult result={kodiResult} />
            </div>
          </form>
        </CardContent>
      </Card>

      {/* ------------------------------------------------------------------ */}
      {/* Kodi JSON-RPC                                                        */}
      {/* ------------------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            Kodi JSON-RPC
            <Badge variant="secondary" className="text-xs font-normal">Optional</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            Needed for downloading artwork (posters, fanart, episode thumbnails). Without
            this, watch history works but no artwork appears.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleJsonrpcSave} className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="URL" id="jsonrpc-url" className="sm:col-span-2">
                <Input id="jsonrpc-url" value={jsonrpc.url}
                  onChange={e => setJsonrpc(j => ({ ...j, url: e.target.value }))}
                  placeholder="http://kodi-headless:8086/jsonrpc" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Username" id="jsonrpc-user">
                <Input id="jsonrpc-user" value={jsonrpc.user}
                  onChange={e => setJsonrpc(j => ({ ...j, user: e.target.value }))}
                  placeholder="optional" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Password" id="jsonrpc-password">
                <PasswordInput id="jsonrpc-password" value={jsonrpc.password}
                  onChange={v => setJsonrpc(j => ({ ...j, password: v }))}
                  isSet={config?.jsonrpc.password_is_set} />
              </Field>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <SaveButton pending={updateJSONRPC.isPending} />
              <TestResult result={jsonrpcResult} />
            </div>
          </form>
        </CardContent>
      </Card>

      {/* ------------------------------------------------------------------ */}
      {/* TMDB                                                                 */}
      {/* ------------------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            TMDB
            <Badge variant="secondary" className="text-xs font-normal">Optional</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            Enables cast data, actor pages, the global search bar (Ctrl+K), and manually
            adding titles to your history.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InfoBox>
            Get a free API key at themoviedb.org/settings/api — the free tier is sufficient.
            Accepts both a v3 API key (short hex) and a v4 Read Access Token (long JWT).
          </InfoBox>
          <form onSubmit={handleTmdbSave} className="space-y-3">
            <Field label="API Key" id="tmdb-key">
              <PasswordInput id="tmdb-key" value={tmdb.api_key}
                onChange={v => setTmdb({ api_key: v })}
                placeholder="Paste v3 key or v4 Read Access Token"
                isSet={config?.tmdb.api_key_is_set} />
            </Field>
            <div className="flex items-center gap-3 pt-1">
              <SaveButton pending={updateTMDB.isPending} />
              <TestResult result={tmdbResult} />
            </div>
          </form>
        </CardContent>
      </Card>

      {/* ------------------------------------------------------------------ */}
      {/* MQTT                                                                 */}
      {/* ------------------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm flex items-center justify-between">
            MQTT / Home Assistant
            <Badge variant="secondary" className="text-xs font-normal">Optional</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            Publishes "currently watching" and "recently watched" sensors to Home Assistant
            via MQTT discovery. Leave Host blank to disable.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleMqttSave} className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Host" id="mqtt-host" desc="Leave blank to disable MQTT">
                <Input id="mqtt-host" value={mqtt.host}
                  onChange={e => setMqtt(m => ({ ...m, host: e.target.value }))}
                  placeholder="192.168.1.x or empty to disable" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Port" id="mqtt-port">
                <Input id="mqtt-port" type="number" value={mqtt.port}
                  onChange={e => setMqtt(m => ({ ...m, port: parseInt(e.target.value) || 1883 }))}
                  className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Username" id="mqtt-user">
                <Input id="mqtt-user" value={mqtt.user}
                  onChange={e => setMqtt(m => ({ ...m, user: e.target.value }))}
                  placeholder="optional" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Password" id="mqtt-password">
                <PasswordInput id="mqtt-password" value={mqtt.password}
                  onChange={v => setMqtt(m => ({ ...m, password: v }))}
                  isSet={config?.mqtt.password_is_set} />
              </Field>
              <Field label="Discovery prefix" id="mqtt-prefix">
                <Input id="mqtt-prefix" value={mqtt.discovery_prefix}
                  onChange={e => setMqtt(m => ({ ...m, discovery_prefix: e.target.value }))}
                  className="h-8 text-sm font-mono" />
              </Field>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <SaveButton pending={updateMQTT.isPending} />
              <TestResult result={mqttResult} />
            </div>
          </form>
        </CardContent>
      </Card>

      {/* ------------------------------------------------------------------ */}
      {/* Application settings                                                 */}
      {/* ------------------------------------------------------------------ */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-sm">Application</CardTitle>
          <CardDescription className="text-xs">
            General settings not tied to a specific integration.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleAppSave} className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Public base URL" id="app-url" className="sm:col-span-2"
                desc="Used in MQTT artwork URLs — set to your Unraid server's URL">
                <Input id="app-url" value={appCfg.public_base_url}
                  onChange={e => setAppCfg(a => ({ ...a, public_base_url: e.target.value }))}
                  className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Timezone" id="app-tz"
                desc="Must match your Kodi clients' local timezone">
                <Input id="app-tz" value={appCfg.tz}
                  onChange={e => setAppCfg(a => ({ ...a, tz: e.target.value }))}
                  placeholder="America/Los_Angeles" className="h-8 text-sm font-mono" />
              </Field>
              <Field label="Log level" id="app-log">
                <Select value={appCfg.log_level} onValueChange={v => setAppCfg(a => ({ ...a, log_level: v }))}>
                  <SelectTrigger id="app-log" className="h-8 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="DEBUG">DEBUG</SelectItem>
                    <SelectItem value="INFO">INFO</SelectItem>
                    <SelectItem value="WARNING">WARNING</SelectItem>
                    <SelectItem value="ERROR">ERROR</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <SaveButton pending={updateApp.isPending} />
              <TestResult result={appResult} />
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Field — label + optional description + children
// ---------------------------------------------------------------------------
function Field({
  label,
  id,
  desc,
  className,
  children,
}: {
  label: string
  id: string
  desc?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={`space-y-1 ${className ?? ''}`}>
      <Label htmlFor={id} className="text-xs font-medium">{label}</Label>
      {children}
      {desc && <p className="text-xs text-muted-foreground">{desc}</p>}
    </div>
  )
}
