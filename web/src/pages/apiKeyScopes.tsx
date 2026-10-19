import { useTranslation } from 'react-i18next'

import { Button, Checkbox } from '@/components'
import { PLATFORMS, SCOPES, type Platform, type Scope } from '@/lib/types'

/**
 * The scope picker, shared by creating a key and editing one.
 *
 * Two dialogs offering the same choice drift the moment one gains a preset the
 * other lacks, and the edit dialog exists mostly to add a scope a release
 * introduced - which is exactly when the two lists would disagree.
 */

/** Scope values are wire identifiers and are never translated; these name their copy keys. */
export const SCOPE_COPY: Record<Scope, string> = {
  'douyin:read': 'douyinRead',
  'tiktok:read': 'tiktokRead',
  'bilibili:read': 'bilibiliRead',
  'identity:manage': 'identityManage',
  'archive:read': 'archiveRead',
  'archive:export': 'archiveExport',
  'media:read': 'mediaRead',
  'media:write': 'mediaWrite',
  'transcription:read': 'transcriptionRead',
  'transcription:write': 'transcriptionWrite',
  admin: 'admin',
}

/**
 * One read scope per platform, derived the way PLATFORM_READ_SCOPES is in
 * src/dtk/core/types.py, so a platform added to PLATFORMS joins "all platform
 * reads" without anyone remembering this list.
 */
export const PLATFORM_READ_SCOPES: readonly Scope[] = PLATFORMS.map(
  (platform: Platform): `${Platform}:read` => `${platform}:read`,
)

const READ_ONLY_SCOPES: readonly Scope[] = [
  ...PLATFORM_READ_SCOPES,
  'archive:read',
  'media:read',
  'transcription:read',
]

/**
 * Starting points, not roles. Each replaces the ticked set, and the boxes stay
 * editable afterwards. Nothing that writes or administers is in one: a preset
 * that granted `media:write` would be one click from filling a disk.
 */
const PRESETS: ReadonlyArray<{ id: string; scopes: readonly Scope[] }> = [
  { id: 'platformReads', scopes: PLATFORM_READ_SCOPES },
  { id: 'readOnly', scopes: READ_ONLY_SCOPES },
]

/**
 * Whether a key reads a platform but not Bilibili.
 *
 * Keys keep the scopes they were minted with, so every key from before 5.1.6
 * reads Douyin and TikTok and is refused on Bilibili until somebody adds the
 * scope. `admin` is excluded because it already reaches everything.
 */
export function lacksBilibili(scopes: readonly Scope[]): boolean {
  if (scopes.includes('admin') || scopes.includes('bilibili:read')) return false
  return scopes.some((scope) => PLATFORM_READ_SCOPES.includes(scope))
}

export function sameScopes(a: readonly Scope[], b: readonly Scope[]): boolean {
  return a.length === b.length && a.every((scope) => b.includes(scope))
}

export interface ScopePickerProps {
  /** Distinguishes two pickers' hint ids when both dialogs are mounted. */
  id: string
  value: readonly Scope[]
  onChange: (scopes: Scope[]) => void
  error?: string
}

export function ScopePicker({ id, value, onChange, error }: ScopePickerProps) {
  const { t } = useTranslation('console')
  const hintId = `${id}-hint`

  const toggle = (scope: Scope): void => {
    onChange(value.includes(scope) ? value.filter((entry) => entry !== scope) : [...value, scope])
  }

  return (
    <fieldset style={{ border: 0, margin: 0, padding: 0 }} aria-describedby={hintId}>
      <legend
        style={{
          fontSize: 'var(--text-sm)',
          fontWeight: 'var(--weight-medium)',
          padding: 0,
          marginBottom: 'var(--space-1)',
        }}
      >
        {t('field.scopes')}
      </legend>
      <p id={hintId} className="u-xs u-muted" style={{ margin: '0 0 var(--space-2)' }}>
        {t('apiKeys.scopesHint')}
      </p>
      <div className="u-row u-wrap" style={{ gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
        <span className="u-xs u-muted">{t('apiKeys.preset.label')}</span>
        {PRESETS.map((preset) => (
          <Button
            key={preset.id}
            type="button"
            size="sm"
            variant="secondary"
            aria-pressed={sameScopes(value, preset.scopes)}
            title={preset.scopes.join(' ')}
            onClick={() => {
              onChange([...preset.scopes])
            }}
          >
            {t(`apiKeys.preset.${preset.id}`)}
          </Button>
        ))}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={value.length === 0}
          onClick={() => {
            onChange([])
          }}
        >
          {t('apiKeys.preset.clear')}
        </Button>
      </div>
      <div className="u-stack-sm">
        {SCOPES.map((scope) => (
          <Checkbox
            key={scope}
            checked={value.includes(scope)}
            onChange={() => {
              toggle(scope)
            }}
            label={
              <span className="u-row">
                <span className="u-mono">{scope}</span>
                <span className="u-muted u-xs">{t(`apiKeys.scope.${SCOPE_COPY[scope]}`)}</span>
              </span>
            }
            hint={t(`apiKeys.scopeHint.${SCOPE_COPY[scope]}`)}
          />
        ))}
      </div>
      {error ? (
        <p className="u-xs" role="alert" style={{ color: 'var(--danger)', margin: 'var(--space-2) 0 0' }}>
          {error}
        </p>
      ) : null}
    </fieldset>
  )
}
