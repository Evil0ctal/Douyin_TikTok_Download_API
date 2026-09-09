import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './Button'
import { ConfirmDialog } from './ConfirmDialog'
import { AlertIcon, DotIcon, LockIcon, MinusIcon, RefreshIcon, ServerIcon } from './Icons'
import { Input } from './Input'
import { Select } from './Select'
import { Switch } from './Switch'
import { Textarea } from './Textarea'
import { useToast } from './Toast'
import { useApiMutation, useFormatters } from '@/hooks'
import { apiDelete, apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'

/**
 * One editable runtime setting.
 *
 * Extracted from the settings page when the scheduler got a page of its own:
 * two surfaces now edit the same rows, and the thing that must never differ
 * between them is where a value comes from. Once a key is stored in the
 * database it wins over the .env seed, and "I edited .env, restarted, and
 * nothing changed" is the question that follows any surface which hides that
 * (docs/design/10-configuration.md).
 *
 * SENSITIVE keys are not a styling variation: the URL allowlist is the only
 * SSRF defence this service has, so widening one takes an administrator, a
 * typed confirmation and an audit row.
 */

/** Wire shape of GET /api/v1/admin/settings. Keys and scopes are never translated. */
export interface SettingRow {
  key: string
  value: unknown
  default: unknown
  scope: string
  type: string
  description: string
  /** Present only for a setting whose value is one of a fixed set. */
  choices: string[] | null
  sensitive: boolean
  source: string
  env_var: string
  updated_at: string | null
  updated_by: string | null
}

type EditorKind = 'bool' | 'choice' | 'int' | 'float' | 'str' | 'list' | 'json'

/** Group order follows the registry in src/dtk/core/config.py, not the alphabet. */
function editorKind(row: SettingRow): EditorKind {
  // A fixed set of values outranks the declared type: offering the three
  // signing modes as a picker is the difference between choosing one and
  // guessing at its spelling, and the server rejects a typo either way.
  if (row.choices && row.choices.length > 0) return 'choice'
  switch (row.type) {
    case 'bool':
      return 'bool'
    case 'int':
      return 'int'
    case 'float':
      return 'float'
    case 'str':
      return 'str'
    case 'list':
      return 'list'
    default:
      return 'json'
  }
}

/** The server says "environment"; the console also accepts the shorter "env". */
export type SourceKind = 'database' | 'environment' | 'default'

export function sourceOf(row: SettingRow): SourceKind {
  if (row.source === 'database') return 'database'
  if (row.source === 'env' || row.source === 'environment') return 'environment'
  return 'default'
}

function toDraft(value: unknown, kind: EditorKind): string {
  if (kind === 'list') {
    return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
  }
  if (kind === 'json') {
    try {
      return JSON.stringify(value ?? null, null, 2)
    } catch {
      return String(value)
    }
  }
  if (value === null || value === undefined) return ''
  return String(value)
}

interface ParseOk {
  ok: true
  value: unknown
}
interface ParseFail {
  ok: false
  reason: 'integer' | 'number' | 'json'
}

function parseDraft(draft: string, kind: EditorKind): ParseOk | ParseFail {
  switch (kind) {
    case 'int': {
      const parsed = Number(draft.trim())
      if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) return { ok: false, reason: 'integer' }
      return { ok: true, value: parsed }
    }
    case 'float': {
      const parsed = Number(draft.trim())
      if (!Number.isFinite(parsed)) return { ok: false, reason: 'number' }
      return { ok: true, value: parsed }
    }
    case 'list':
      return {
        ok: true,
        value: draft
          .split('\n')
          .map((line) => line.trim())
          .filter((line) => line.length > 0),
      }
    case 'json':
      try {
        return { ok: true, value: JSON.parse(draft.trim() === '' ? 'null' : draft) }
      } catch {
        return { ok: false, reason: 'json' }
      }
    default:
      return { ok: true, value: draft }
  }
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

/** Colour plus icon plus text, so the source survives a screenshot and a paste. */
function SourceTag({ source }: { source: SourceKind }) {
  const { t } = useTranslation('console')
  const colour =
    source === 'database' ? 'var(--accent)' : source === 'environment' ? 'var(--text-secondary)' : 'var(--text-muted)'
  const Icon = source === 'database' ? DotIcon : source === 'environment' ? ServerIcon : MinusIcon

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        padding: '1px var(--space-2)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-full)',
        color: colour,
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
        whiteSpace: 'nowrap',
      }}
      title={t(`settings.source.${source}Hint`)}
    >
      <Icon size={10} />
      {t(`settings.source.${source}`)}
    </span>
  )
}

function SensitiveTag() {
  const { t } = useTranslation('console')
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        padding: '1px var(--space-2)',
        border: '1px solid var(--caution)',
        borderRadius: 'var(--radius-full)',
        color: 'var(--caution)',
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
        whiteSpace: 'nowrap',
      }}
    >
      <LockIcon size={10} />
      {t('settings.sensitive.tag')}
    </span>
  )
}

export function Banner({
  tone,
  icon,
  children,
}: {
  tone: 'accent' | 'caution'
  icon: ReactNode
  children: ReactNode
}) {
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

export interface RowProps {
  row: SettingRow
  canWrite: boolean
  canWriteSensitive: boolean
  onSaved: () => void
}

export function SettingEditor({ row, canWrite, canWriteSensitive, onSaved }: RowProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()

  const kind = editorKind(row)
  const source = sourceOf(row)
  const [draft, setDraft] = useState(() => toDraft(row.value, kind))
  const [touched, setTouched] = useState(false)
  const [invalid, setInvalid] = useState<ParseFail['reason'] | null>(null)
  const [confirming, setConfirming] = useState<'save' | 'reset' | null>(null)

  const locked = !canWrite || (row.sensitive && !canWriteSensitive)
  const current = toDraft(row.value, kind)
  // A poll must not fight the operator: an untouched row simply follows the
  // server, and a touched one keeps what was typed until it is saved.
  const shown = touched ? draft : current
  const parsed = parseDraft(shown, kind)
  const dirty = touched && (!parsed.ok || !sameValue(parsed.value, row.value))

  const save = useApiMutation<unknown, { value: unknown; confirm: boolean }>(
    (variables) => apiPut(paths.settings.byKey(row.key), variables),
    {
      onSuccess: () => {
        setTouched(false)
        setInvalid(null)
        setConfirming(null)
        toast.success(t('settings.toast.saved', { key: row.key }))
        onSaved()
      },
      onError: (error) => {
        toast.apiError(error, t('settings.toast.saveFailed', { key: row.key }))
      },
    },
  )

  const reset = useApiMutation<unknown, void>(
    () => apiDelete(paths.settings.byKey(row.key), { params: { confirm: true } }),
    {
      onSuccess: () => {
        setTouched(false)
        setInvalid(null)
        setConfirming(null)
        toast.success(t('settings.toast.reset', { key: row.key }))
        onSaved()
      },
      onError: (error) => {
        toast.apiError(error, t('settings.toast.resetFailed', { key: row.key }))
      },
    },
  )

  const submit = (): void => {
    if (!parsed.ok) {
      setInvalid(parsed.reason)
      return
    }
    setInvalid(null)
    if (row.sensitive) {
      setConfirming('save')
      return
    }
    save.mutate({ value: parsed.value, confirm: false })
  }

  const cancel = (): void => {
    setTouched(false)
    setDraft(current)
    setInvalid(null)
  }

  const errorText =
    invalid === 'integer'
      ? t('settings.invalid.integer')
      : invalid === 'number'
        ? t('settings.invalid.number')
        : invalid === 'json'
          ? t('settings.invalid.json')
          : undefined

  const control =
    kind === 'choice' ? (
      <Select
        value={shown}
        aria-label={row.key}
        disabled={locked || save.isPending}
        // Values are configuration keys, so they are shown verbatim; only the
        // explanation beside them is translated.
        options={(row.choices ?? []).map((choice) => ({
          value: choice,
          label: t(`settings.choice.${row.key}.${choice}`, { defaultValue: choice }),
        }))}
        onChange={(event) => {
          setTouched(true)
          setDraft(event.target.value)
          setInvalid(null)
        }}
      />
    ) : kind === 'bool' ? (
      <Switch
        checked={shown === 'true'}
        disabled={locked || save.isPending}
        aria-label={row.key}
        onChange={(event) => {
          setTouched(true)
          setDraft(event.target.checked ? 'true' : 'false')
        }}
        label={shown === 'true' ? t('common:value.enabled') : t('common:value.disabled')}
      />
    ) : kind === 'list' || kind === 'json' ? (
      <Textarea
        value={shown}
        rows={Math.min(8, Math.max(3, shown.split('\n').length + 1))}
        aria-label={row.key}
        disabled={locked || save.isPending}
        error={errorText}
        description={kind === 'list' ? t('settings.hint.onePerLine') : t('settings.hint.json')}
        onChange={(event) => {
          setTouched(true)
          setDraft(event.target.value)
          setInvalid(null)
        }}
      />
    ) : (
      <Input
        value={shown}
        mono
        inputMode={kind === 'int' || kind === 'float' ? 'decimal' : undefined}
        aria-label={row.key}
        disabled={locked || save.isPending}
        error={errorText}
        onChange={(event) => {
          setTouched(true)
          setDraft(event.target.value)
          setInvalid(null)
        }}
      />
    )

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 'var(--space-3)',
        padding: 'var(--space-3) 0',
        borderTop: '1px solid var(--border-subtle)',
      }}
    >
      <div style={{ flex: '1 1 260px', minWidth: 0 }} className="u-stack-sm">
        <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <code className="u-mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--text)' }}>
            {row.key}
          </code>
          <SourceTag source={source} />
          {row.sensitive ? <SensitiveTag /> : null}
        </div>
        {row.description ? <p className="u-xs u-secondary" style={{ margin: 0 }}>{row.description}</p> : null}
        <p className="u-xs u-muted" style={{ margin: 0 }}>
          <span className="u-mono">{row.env_var}</span>
          {' · '}
          {t('settings.row.default', { value: toDraft(row.default, kind === 'bool' ? 'str' : kind) })}
          {row.updated_at ? ` · ${t('settings.row.updated', { when: formatters.dateTime(row.updated_at) })}` : null}
        </p>
      </div>

      <div style={{ flex: '0 1 320px', minWidth: '220px' }} className="u-stack-sm">
        {control}
        <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <Button
            size="sm"
            variant={row.sensitive ? 'danger' : 'primary'}
            disabled={locked || !dirty}
            loading={save.isPending}
            onClick={submit}
          >
            {t('common:action.save')}
          </Button>
          {dirty ? (
            <Button size="sm" variant="ghost" onClick={cancel} disabled={save.isPending}>
              {t('common:action.cancel')}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            icon={<RefreshIcon />}
            disabled={locked || source !== 'database' || reset.isPending}
            loading={reset.isPending}
            title={source === 'database' ? undefined : t('settings.row.alreadyInherited')}
            onClick={() => {
              if (row.sensitive) setConfirming('reset')
              else reset.mutate()
            }}
          >
            {t('settings.row.reset')}
          </Button>
        </div>
        {locked ? (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {row.sensitive && canWrite ? t('settings.sensitive.adminOnly') : t('settings.readOnly')}
          </p>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirming !== null}
        danger
        loading={save.isPending || reset.isPending}
        confirmPhrase={row.key}
        title={confirming === 'reset' ? t('settings.confirm.resetTitle') : t('settings.confirm.saveTitle')}
        description={t('settings.confirm.description', { key: row.key })}
        confirmLabel={confirming === 'reset' ? t('settings.row.reset') : t('common:action.save')}
        onCancel={() => {
          setConfirming(null)
        }}
        onConfirm={() => {
          if (confirming === 'reset') {
            reset.mutate()
            return
          }
          const value = parseDraft(shown, kind)
          if (!value.ok) {
            setInvalid(value.reason)
            setConfirming(null)
            return
          }
          save.mutate({ value: value.value, confirm: true })
        }}
      >
        <div className="u-stack-sm">
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('settings.confirm.warning')}
          </Banner>
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('settings.confirm.audit')}
          </p>
        </div>
      </ConfirmDialog>
    </div>
  )
}
