import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  BellIcon,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  CrossIcon,
  DataTable,
  DotIcon,
  ErrorState,
  Input,
  MaskedSecret,
  Modal,
  PageHeader,
  RefreshIcon,
  Select,
  Skeleton,
  Switch,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import { apiPost, apiPut, waitForTask } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { LANGUAGES, type Language } from '@/lib/language'
import { POLL } from '@/lib/query'

/**
 * Alert channels and triggers.
 *
 * Channels live in the runtime setting `notify.channels`, so this page is a
 * typed editor over that one key rather than a second source of truth.
 *
 * The deduplication windows are shown rather than hidden: an alert that fires on
 * every request during a circuit outage empties a phone in ten minutes, and the
 * user then switches notifications off for good. Alerting stays trustworthy
 * because it is rare (docs/design/15-operations.md).
 */

const SETTINGS_KEY = ['admin', 'settings'] as const

const KEY_ENABLED = 'notify.enabled'
const KEY_CHANNELS = 'notify.channels'
const KEY_LANGUAGE = 'notify.language'

interface SettingRow {
  key: string
  value: unknown
}

interface SettingsResponse {
  version: number
  settings: SettingRow[]
}

/** Mirrors ChannelType in src/dtk/ops/channels.py. Values are never translated. */
const CHANNEL_TYPES = ['webhook', 'bark', 'wecom', 'dingtalk', 'telegram', 'smtp'] as const
type ChannelType = (typeof CHANNEL_TYPES)[number]

/** Mirrors TRIGGERS in src/dtk/ops/notify.py, which mirrors doc 15's table. */
interface TriggerSpec {
  event: string
  severity: 'error' | 'warning'
  dedupSeconds: number
  scope: string | null
}

const MINUTE = 60
const HOUR = 3600

const TRIGGERS: readonly TriggerSpec[] = [
  { event: 'endpoint_circuit_open', severity: 'error', dedupSeconds: 30 * MINUTE, scope: 'endpoint' },
  { event: 'pool_empty', severity: 'error', dedupSeconds: 15 * MINUTE, scope: 'platform' },
  { event: 'pool_below_min', severity: 'warning', dedupSeconds: 60 * MINUTE, scope: 'platform' },
  { event: 'proxy_unhealthy', severity: 'warning', dedupSeconds: 60 * MINUTE, scope: 'proxy' },
  { event: 'signature_stale', severity: 'error', dedupSeconds: 24 * HOUR, scope: 'endpoint' },
  { event: 'cookie_expiring', severity: 'warning', dedupSeconds: 24 * HOUR, scope: 'identity_id' },
  { event: 'backup_failed', severity: 'error', dedupSeconds: 24 * HOUR, scope: null },
]

const ALL_EVENTS: readonly string[] = TRIGGERS.map((trigger) => trigger.event)

/* -------------------------------------------------------------------------- */
/* Channel shape                                                               */
/* -------------------------------------------------------------------------- */

const FIELD_KEYS = [
  'url',
  'group',
  'secret',
  'token',
  'chatId',
  'host',
  'port',
  'sender',
  'recipients',
  'username',
  'password',
] as const
type FieldKey = (typeof FIELD_KEYS)[number]

interface FieldSpec {
  key: FieldKey
  required?: boolean
  /** Rendered as a password input and masked in the table. */
  secret?: boolean
  /** Must be an https URL; the server refuses anything else anyway. */
  url?: boolean
  hint?: boolean
  narrow?: boolean
}

/** Which descriptor fields each channel type carries (src/dtk/ops/channels.py). */
const FIELDS: Record<ChannelType, readonly FieldSpec[]> = {
  webhook: [{ key: 'url', required: true, url: true, hint: true }],
  bark: [
    { key: 'url', required: true, url: true, hint: true },
    { key: 'group', hint: true },
  ],
  wecom: [{ key: 'url', required: true, url: true, hint: true }],
  dingtalk: [
    { key: 'url', required: true, url: true, hint: true },
    { key: 'secret', secret: true, hint: true },
  ],
  telegram: [
    { key: 'token', required: true, secret: true },
    { key: 'chatId', required: true },
  ],
  smtp: [
    { key: 'host', required: true },
    { key: 'port', narrow: true },
    { key: 'sender', required: true },
    { key: 'recipients', required: true, hint: true },
    { key: 'username' },
    { key: 'password', secret: true },
  ],
}

/** Console field name to descriptor key. Descriptor keys are the wire contract. */
const DESCRIPTOR_KEY: Record<FieldKey, string> = {
  url: 'url',
  group: 'group',
  secret: 'secret',
  token: 'token',
  chatId: 'chat_id',
  host: 'host',
  port: 'port',
  sender: 'sender',
  recipients: 'recipients',
  username: 'username',
  password: 'password',
}

interface ChannelDraft {
  type: ChannelType
  name: string
  language: '' | Language
  enabled: boolean
  /** Empty means every event; the descriptor omits the key in that case. */
  events: string[]
  fields: Record<FieldKey, string>
  ssl: boolean
  starttls: boolean
}

function emptyFields(): Record<FieldKey, string> {
  return {
    url: '',
    group: '',
    secret: '',
    token: '',
    chatId: '',
    host: '',
    port: '587',
    sender: '',
    recipients: '',
    username: '',
    password: '',
  }
}

function emptyDraft(): ChannelDraft {
  return {
    type: 'webhook',
    name: '',
    language: '',
    enabled: true,
    events: [],
    fields: emptyFields(),
    ssl: false,
    starttls: true,
  }
}

function readString(record: Record<string, unknown>, key: string, fallback = ''): string {
  const value = record[key]
  if (value === null || value === undefined) return fallback
  if (Array.isArray(value)) return value.map((item) => String(item)).join(', ')
  return String(value)
}

function readBoolean(record: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const value = record[key]
  return typeof value === 'boolean' ? value : fallback
}

function isChannelType(value: string): value is ChannelType {
  return (CHANNEL_TYPES as readonly string[]).includes(value)
}

function isLanguageValue(value: string): value is Language {
  return (LANGUAGES as readonly string[]).includes(value)
}

function toDraft(record: Record<string, unknown>): ChannelDraft {
  const type = readString(record, 'type')
  const language = readString(record, 'language')
  const events = Array.isArray(record['events']) ? record['events'].map((item) => String(item)) : []

  const fields = emptyFields()
  for (const key of FIELD_KEYS) {
    const raw = readString(record, DESCRIPTOR_KEY[key])
    if (raw) fields[key] = raw
  }

  return {
    type: isChannelType(type) ? type : 'webhook',
    name: readString(record, 'name', type),
    language: isLanguageValue(language) ? language : '',
    enabled: readBoolean(record, 'enabled', true),
    events: events.filter((event) => ALL_EVENTS.includes(event)),
    fields,
    ssl: readBoolean(record, 'ssl', false),
    starttls: readBoolean(record, 'starttls', true),
  }
}

function toDescriptor(draft: ChannelDraft): Record<string, unknown> {
  const descriptor: Record<string, unknown> = {
    type: draft.type,
    name: draft.name.trim(),
    enabled: draft.enabled,
  }
  if (draft.language) descriptor['language'] = draft.language
  if (draft.events.length > 0 && draft.events.length < ALL_EVENTS.length) {
    descriptor['events'] = draft.events
  }

  for (const spec of FIELDS[draft.type]) {
    const value = draft.fields[spec.key].trim()
    if (!value) continue
    if (spec.key === 'port') descriptor['port'] = Number(value) || 587
    else if (spec.key === 'recipients') {
      descriptor['recipients'] = value
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
    } else descriptor[DESCRIPTOR_KEY[spec.key]] = value
  }

  if (draft.type === 'smtp') {
    descriptor['ssl'] = draft.ssl
    descriptor['starttls'] = draft.starttls
  }
  return descriptor
}

function missingFields(draft: ChannelDraft): FieldKey[] {
  return FIELDS[draft.type]
    .filter((spec) => spec.required && draft.fields[spec.key].trim().length === 0)
    .map((spec) => spec.key)
}

function badUrlFields(draft: ChannelDraft): FieldKey[] {
  return FIELDS[draft.type]
    .filter((spec) => spec.url && !/^https:\/\//i.test(draft.fields[spec.key].trim()))
    .map((spec) => spec.key)
}

/** A bot URL usually carries its credential in the path or the query string. */
function maskUrl(url: string): string {
  try {
    const parsed = new URL(url)
    const tail = `${parsed.pathname}${parsed.search}`.replace(/^\//, '')
    return `${parsed.protocol}//${parsed.host}/${tail ? `••••${tail.slice(-4)}` : ''}`
  } catch {
    return url.length <= 8 ? '••••' : `${url.slice(0, 8)}••••`
  }
}

function channelTarget(draft: ChannelDraft): string | null {
  if (draft.type === 'smtp') return `${draft.fields.host}:${draft.fields.port}`
  if (draft.type === 'telegram') {
    return draft.fields.token ? `${draft.fields.token.slice(0, 4)}••••` : null
  }
  return draft.fields.url ? maskUrl(draft.fields.url) : null
}

/* -------------------------------------------------------------------------- */
/* Small presentational pieces                                                 */
/* -------------------------------------------------------------------------- */

function SeverityTag({ severity }: { severity: TriggerSpec['severity'] }) {
  const { t } = useTranslation('console')
  const Icon = severity === 'error' ? AlertIcon : DotIcon
  return (
    <span
      className="u-nowrap"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        color: severity === 'error' ? 'var(--danger)' : 'var(--warning)',
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
      }}
    >
      <Icon size={10} />
      {t(`notifications.severity.${severity}`)}
    </span>
  )
}

function EnabledTag({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('common')
  const Icon = enabled ? DotIcon : CrossIcon
  return (
    <span
      className="u-nowrap"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        color: enabled ? 'var(--success)' : 'var(--neutral)',
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
      }}
    >
      <Icon size={10} />
      {enabled ? t('value.enabled') : t('value.disabled')}
    </span>
  )
}

function Banner({ tone, icon, children }: { tone: 'accent' | 'caution'; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 'var(--space-2)',
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius)',
        border: `1px solid var(--${tone === 'accent' ? 'border' : 'caution'})`,
        background: tone === 'accent' ? 'var(--accent-subtle)' : 'transparent',
        color: 'var(--text-secondary)',
        fontSize: 'var(--text-sm)',
        lineHeight: 'var(--leading-sm)',
      }}
    >
      <span style={{ color: `var(--${tone})`, flex: '0 0 auto', marginTop: '2px' }}>{icon}</span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Editor                                                                      */
/* -------------------------------------------------------------------------- */

interface EditorProps {
  open: boolean
  draft: ChannelDraft
  existingNames: readonly string[]
  saving: boolean
  onChange: (draft: ChannelDraft) => void
  onClose: () => void
  onSubmit: () => void
}

function ChannelEditor({ open, draft, existingNames, saving, onChange, onClose, onSubmit }: EditorProps) {
  const { t } = useTranslation(['console', 'common'])
  const formatters = useFormatters()
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) setTouched(false)
  }, [open])

  const patch = (part: Partial<ChannelDraft>): void => {
    onChange({ ...draft, ...part })
  }
  const patchField = (key: FieldKey, value: string): void => {
    onChange({ ...draft, fields: { ...draft.fields, [key]: value } })
  }

  const nameTaken = existingNames.includes(draft.name.trim())
  const nameInvalid = draft.name.trim().length === 0 || nameTaken
  const missing = new Set(missingFields(draft))
  const badUrls = new Set(badUrlFields(draft))
  const invalid = nameInvalid || missing.size > 0 || badUrls.size > 0

  const fieldError = (key: FieldKey): string | undefined => {
    if (!touched) return undefined
    if (missing.has(key)) return t('notifications.error.required')
    if (badUrls.has(key)) return t('notifications.error.urlHttps')
    return undefined
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={t('notifications.editor.title')}
      description={t('notifications.editor.description')}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={saving}
            onClick={() => {
              setTouched(true)
              if (!invalid) onSubmit()
            }}
          >
            {t('common:action.save')}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        <div className="u-row u-wrap" style={{ alignItems: 'flex-start', gap: 'var(--space-3)' }}>
          <Select
            label={t('notifications.field.type')}
            value={draft.type}
            fieldClassName="u-grow"
            description={t('notifications.field.typeHint')}
            onChange={(event) => {
              const value = event.target.value
              if (isChannelType(value)) patch({ type: value })
            }}
            options={CHANNEL_TYPES.map((type) => ({ value: type, label: type }))}
          />
          <Input
            label={t('notifications.field.name')}
            value={draft.name}
            fieldClassName="u-grow"
            required
            description={t('notifications.field.nameHint')}
            error={
              touched && nameInvalid
                ? nameTaken
                  ? t('notifications.error.nameTaken')
                  : t('notifications.error.nameRequired')
                : undefined
            }
            onChange={(event) => {
              patch({ name: event.target.value })
            }}
          />
        </div>

        {FIELDS[draft.type].map((spec) => (
          <Input
            key={spec.key}
            label={t(`notifications.field.${spec.key}`)}
            value={draft.fields[spec.key]}
            mono
            required={spec.required}
            showOptional={!spec.required}
            type={spec.secret ? 'password' : 'text'}
            autoComplete={spec.secret ? 'off' : undefined}
            inputMode={spec.key === 'port' ? 'numeric' : undefined}
            placeholder={spec.url ? 'https://' : undefined}
            style={spec.narrow ? { maxWidth: '140px' } : undefined}
            description={
              // A masked value looks like a real one in a password field, so
              // the page has to say that leaving it alone keeps the stored
              // credential rather than saving the mask over it.
              looksMasked(draft.fields[spec.key])
                ? t('notifications.field.maskedHint')
                : spec.hint
                  ? t(`notifications.field.${spec.key}Hint`)
                  : undefined
            }
            error={fieldError(spec.key)}
            onChange={(event) => {
              patchField(spec.key, event.target.value)
            }}
          />
        ))}

        {draft.type === 'smtp' ? (
          <div className="u-row u-wrap" style={{ gap: 'var(--space-4)' }}>
            <Checkbox
              checked={draft.ssl}
              label={t('notifications.field.ssl')}
              onChange={(event) => {
                patch({ ssl: event.target.checked })
              }}
            />
            <Checkbox
              checked={draft.starttls}
              label={t('notifications.field.starttls')}
              onChange={(event) => {
                patch({ starttls: event.target.checked })
              }}
            />
          </div>
        ) : null}

        <Select
          label={t('notifications.field.language')}
          value={draft.language}
          description={t('notifications.field.languageHint')}
          onChange={(event) => {
            const value = event.target.value
            patch({ language: isLanguageValue(value) ? value : '' })
          }}
          options={[
            { value: '', label: t('notifications.field.languageInherit') },
            ...LANGUAGES.map((language) => ({ value: language, label: t(`common:language.${language}`) })),
          ]}
        />

        <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
          <legend
            style={{
              fontSize: 'var(--text-sm)',
              lineHeight: 'var(--leading-sm)',
              color: 'var(--text)',
              padding: 0,
              marginBottom: 'var(--space-1)',
            }}
          >
            {t('notifications.field.events')}
          </legend>
          <p className="u-xs u-muted" style={{ margin: '0 0 var(--space-2)' }}>
            {t('notifications.field.eventsHint')}
          </p>
          <div className="u-stack-sm">
            {TRIGGERS.map((trigger) => {
              const checked = draft.events.length === 0 || draft.events.includes(trigger.event)
              return (
                <Checkbox
                  key={trigger.event}
                  checked={checked}
                  label={<span className="u-mono">{trigger.event}</span>}
                  hint={t('notifications.event.dedupHint', {
                    window: formatters.duration(trigger.dedupSeconds * 1000),
                    scope: trigger.scope ?? t('notifications.event.scopeGlobal'),
                  })}
                  onChange={(event) => {
                    const selected = draft.events.length === 0 ? [...ALL_EVENTS] : draft.events
                    patch({
                      events: event.target.checked
                        ? [...new Set([...selected, trigger.event])]
                        : selected.filter((item) => item !== trigger.event),
                    })
                  }}
                />
              )
            })}
          </div>
        </fieldset>

        <Switch
          checked={draft.enabled}
          label={t('notifications.field.enabled')}
          hint={t('notifications.field.enabledHint')}
          onChange={(event) => {
            patch({ enabled: event.target.checked })
          }}
        />
      </div>
    </Modal>
  )
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

/** Wire shape of the notify.test task result. Status is a code, never translated. */
interface TestResult {
  channel?: string | null
  status?: 'sent' | 'partial' | 'failed' | 'disabled'
  sent?: boolean
  delivered?: string[]
  failed?: Record<string, string>
}

/**
 * Whether a field holds the server's mask rather than a credential.
 *
 * The API masks a stored credential on read and merges it back on write, so the
 * editor shows something that is not the value. Recognising it is what lets the
 * page explain itself; the server does the actual deciding.
 */
function looksMasked(value: string | undefined): boolean {
  return typeof value === 'string' && value.includes('***')
}

export default function Notifications() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const session = useSession()

  const query = useApiQuery<SettingsResponse>({
    key: SETTINGS_KEY,
    path: paths.settings.list,
    poll: POLL.slow,
  })

  const [editing, setEditing] = useState<{ draft: ChannelDraft; index: number | null } | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [testing, setTesting] = useState<string | null>(null)

  const role = session.data?.role ?? null
  const canWrite = role === null || role !== 'viewer'

  const rows = useMemo<SettingRow[]>(() => query.data?.settings ?? [], [query.data])
  const valueOf = (key: string): unknown => rows.find((row) => row.key === key)?.value

  const enabled = valueOf(KEY_ENABLED) === true
  const rawLanguage = valueOf(KEY_LANGUAGE)
  const defaultLanguage: Language =
    typeof rawLanguage === 'string' && isLanguageValue(rawLanguage) ? rawLanguage : 'en'

  const channels = useMemo<ChannelDraft[]>(() => {
    const raw = rows.find((row) => row.key === KEY_CHANNELS)?.value
    if (!Array.isArray(raw)) return []
    return raw
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map(toDraft)
  }, [rows])

  const saveSetting = useApiMutation<unknown, { key: string; value: unknown }>(
    (variables) => apiPut(paths.settings.byKey(variables.key), { value: variables.value, confirm: false }),
    {
      onSuccess: () => {
        void invalidate(SETTINGS_KEY)
      },
      onError: (error) => {
        toast.apiError(error, t('notifications.toast.saveFailed'))
      },
    },
  )

  const saveChannels = (next: ChannelDraft[], message: string): void => {
    saveSetting.mutate(
      { key: KEY_CHANNELS, value: next.map(toDescriptor) },
      {
        onSuccess: () => {
          setEditing(null)
          setDeleting(null)
          toast.success(message)
        },
      },
    )
  }

  const sendTest = useApiMutation<TestResult | null, string | null>(
    async (channel) => {
      const accepted = await apiPost<{ task_id?: string }>(paths.notifications.test, { channel })
      if (accepted?.task_id) {
        return waitForTask<TestResult>(accepted.task_id, { timeoutMs: 60_000 })
      }
      return null
    },
    {
      // A task that finished is not a delivery that worked. The worker reports
      // an attempted-and-refused channel as a DONE task carrying status
      // "failed", so treating every completion as success told the operator the
      // channel was fine - which is the one thing this button exists to answer.
      onSuccess: (result) => {
        setTesting(null)
        const failed = result?.failed ?? {}
        const names = Object.keys(failed)
        if (result?.status === 'disabled') {
          toast.info(t('notifications.toast.testDisabled'))
        } else if (names.length > 0) {
          toast.error(
            t('notifications.toast.testRefused', {
              channels: names.join(', '),
              reason: failed[names[0] as keyof typeof failed] ?? '',
            }),
          )
        } else {
          toast.success(t('notifications.toast.testSent'))
        }
      },
      onError: (error) => {
        setTesting(null)
        toast.apiError(error, t('notifications.toast.testFailed'))
      },
    },
  )

  const subscribers = (event: string): number =>
    channels.filter(
      (channel) => channel.enabled && (channel.events.length === 0 || channel.events.includes(event)),
    ).length

  const channelColumns: Array<Column<ChannelDraft>> = [
    {
      id: 'name',
      header: t('notifications.column.name'),
      cell: (row) => <span className="u-truncate">{row.name}</span>,
      sortValue: (row) => row.name,
    },
    {
      id: 'type',
      header: t('notifications.column.type'),
      mono: true,
      cell: (row) => row.type,
      sortValue: (row) => row.type,
      width: '110px',
    },
    {
      id: 'target',
      header: t('notifications.column.target'),
      cell: (row) => <MaskedSecret value={channelTarget(row)} />,
    },
    {
      id: 'language',
      header: t('notifications.column.language'),
      cell: (row) =>
        row.language ? (
          t(`common:language.${row.language}`)
        ) : (
          <span className="u-muted">{t('notifications.field.languageInherit')}</span>
        ),
      width: '120px',
    },
    {
      id: 'events',
      header: t('notifications.column.events'),
      align: 'right',
      cell: (row) => (
        <span className="u-mono">
          {row.events.length === 0 ? ALL_EVENTS.length : row.events.length}/{ALL_EVENTS.length}
        </span>
      ),
      width: '110px',
    },
    {
      id: 'status',
      header: t('notifications.column.status'),
      cell: (row) => <EnabledTag enabled={row.enabled} />,
      sortValue: (row) => String(row.enabled),
      width: '110px',
    },
    {
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      align: 'right',
      cell: (row) => (
        <span className="u-row" style={{ justifyContent: 'flex-end' }}>
          <Button
            size="sm"
            variant="ghost"
            loading={testing === row.name && sendTest.isPending}
            disabled={!canWrite || sendTest.isPending}
            onClick={() => {
              setTesting(row.name)
              sendTest.mutate(row.name)
            }}
          >
            {t('common:action.test')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!canWrite}
            onClick={() => {
              setEditing({ draft: row, index: channels.indexOf(row) })
            }}
          >
            {t('common:action.edit')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!canWrite}
            onClick={() => {
              setDeleting(row.name)
            }}
          >
            {t('common:action.delete')}
          </Button>
        </span>
      ),
      width: '210px',
    },
  ]

  const triggerColumns: Array<Column<TriggerSpec>> = [
    {
      id: 'event',
      header: t('notifications.column.event'),
      mono: true,
      cell: (row) => row.event,
      sortValue: (row) => row.event,
    },
    {
      id: 'severity',
      header: t('notifications.column.severity'),
      cell: (row) => <SeverityTag severity={row.severity} />,
      width: '120px',
    },
    {
      id: 'dedup',
      header: t('notifications.column.dedup'),
      align: 'right',
      cell: (row) => <span className="u-mono">{formatters.duration(row.dedupSeconds * 1000)}</span>,
      sortValue: (row) => row.dedupSeconds,
      width: '140px',
    },
    {
      id: 'scope',
      header: t('notifications.column.scope'),
      mono: true,
      cell: (row) => row.scope ?? <span className="u-muted">{t('notifications.event.scopeGlobal')}</span>,
      width: '140px',
    },
    {
      id: 'subscribers',
      header: t('notifications.column.subscribers'),
      align: 'right',
      cell: (row) => <span className="u-mono">{subscribers(row.event)}</span>,
      width: '130px',
    },
    {
      id: 'description',
      header: t('notifications.column.description'),
      cell: (row) => <span className="u-secondary">{t(`notifications.eventText.${row.event}`)}</span>,
      hideOnMobile: true,
    },
  ]

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.notifications.title')}
        description={t('page.notifications.description')}
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              icon={<BellIcon />}
              disabled={!canWrite || sendTest.isPending}
              loading={testing === null && sendTest.isPending}
              onClick={() => {
                setTesting(null)
                sendTest.mutate(null)
              }}
            >
              {t('notifications.action.testAll')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!canWrite}
              onClick={() => {
                setEditing({ draft: emptyDraft(), index: null })
              }}
            >
              {t('notifications.action.add')}
            </Button>
          </>
        }
      />

      <Banner tone="accent" icon={<BellIcon size={14} />}>
        {t('notifications.dedupExplainer')}
      </Banner>

      {query.isError ? (
        <Card>
          <ErrorState
            error={query.error}
            onRetry={() => {
              void query.refetch()
            }}
          />
        </Card>
      ) : query.isLoading ? (
        <Card title={<Skeleton width={160} height={16} />}>
          <div className="u-stack-sm">
            <Skeleton height={38} />
            <Skeleton height={120} />
          </div>
        </Card>
      ) : (
        <>
          <Card title={t('notifications.delivery.title')} description={t('notifications.delivery.description')}>
            <div className="u-stack">
              <Switch
                checked={enabled}
                disabled={!canWrite || saveSetting.isPending}
                label={t('notifications.delivery.enabled')}
                hint={t('notifications.delivery.enabledHint')}
                onChange={(event) => {
                  saveSetting.mutate(
                    { key: KEY_ENABLED, value: event.target.checked },
                    {
                      onSuccess: () => {
                        toast.success(t('notifications.toast.saved'))
                      },
                    },
                  )
                }}
              />
              <Select
                label={t('notifications.delivery.language')}
                description={t('notifications.delivery.languageHint')}
                value={defaultLanguage}
                disabled={!canWrite || saveSetting.isPending}
                style={{ maxWidth: '260px' }}
                onChange={(event) => {
                  saveSetting.mutate(
                    { key: KEY_LANGUAGE, value: event.target.value },
                    {
                      onSuccess: () => {
                        toast.success(t('notifications.toast.saved'))
                      },
                    },
                  )
                }}
                options={LANGUAGES.map((language) => ({
                  value: language,
                  label: t(`common:language.${language}`),
                }))}
              />
              <p className="u-xs u-muted" style={{ margin: 0 }}>
                <span className="u-mono">{KEY_ENABLED}</span>
                {' · '}
                <span className="u-mono">{KEY_LANGUAGE}</span>
                {' · '}
                <span className="u-mono">{KEY_CHANNELS}</span>
              </p>
            </div>
          </Card>

          <Card
            title={t('notifications.channels.title')}
            description={t('notifications.channels.description')}
            flush
            actions={
              <Button
                size="sm"
                variant="ghost"
                icon={<RefreshIcon />}
                loading={query.isFetching && !query.isLoading}
                onClick={() => {
                  void invalidate(SETTINGS_KEY)
                }}
              >
                {t('common:action.refresh')}
              </Button>
            }
          >
            <DataTable
              columns={channelColumns}
              rows={channels}
              getRowId={(row) => row.name}
              storageKey="notification-channels"
              flashValue={(row) => String(row.enabled)}
              emptyTitle={t('notifications.empty.title')}
              emptyDescription={t('notifications.empty.description')}
              emptyAction={
                <Button
                  variant="primary"
                  size="sm"
                  disabled={!canWrite}
                  onClick={() => {
                    setEditing({ draft: emptyDraft(), index: null })
                  }}
                >
                  {t('notifications.action.add')}
                </Button>
              }
              caption={t('notifications.channels.title')}
            />
          </Card>

          <Card title={t('notifications.triggers.title')} description={t('notifications.triggers.description')} flush>
            <DataTable
              columns={triggerColumns}
              rows={[...TRIGGERS]}
              getRowId={(row) => row.event}
              storageKey="notification-triggers"
              caption={t('notifications.triggers.title')}
            />
          </Card>
        </>
      )}

      {editing ? (
        <ChannelEditor
          open
          draft={editing.draft}
          saving={saveSetting.isPending}
          existingNames={channels.filter((_, index) => index !== editing.index).map((channel) => channel.name)}
          onChange={(draft) => {
            setEditing({ draft, index: editing.index })
          }}
          onClose={() => {
            setEditing(null)
          }}
          onSubmit={() => {
            const next =
              editing.index === null
                ? [...channels, editing.draft]
                : channels.map((channel, index) => (index === editing.index ? editing.draft : channel))
            saveChannels(next, t('notifications.toast.channelSaved', { name: editing.draft.name }))
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        danger
        loading={saveSetting.isPending}
        title={t('notifications.confirm.deleteTitle')}
        description={t('notifications.confirm.deleteDescription', { name: deleting ?? '' })}
        confirmLabel={t('common:action.delete')}
        onCancel={() => {
          setDeleting(null)
        }}
        onConfirm={() => {
          saveChannels(
            channels.filter((channel) => channel.name !== deleting),
            t('notifications.toast.channelDeleted', { name: deleting ?? '' }),
          )
        }}
      />
    </div>
  )
}
