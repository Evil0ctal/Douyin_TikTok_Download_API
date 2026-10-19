import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useApiMutation, useApiQuery, useInvalidate } from '@/hooks'
import { apiPost, apiPut, errorCodeOf } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import { Button } from './Button'
import { Card } from './Card'
import { ConfirmDialog } from './ConfirmDialog'
import { CopyableId } from './CopyableId'
import { ErrorState } from './ErrorState'
import { AlertIcon, InfoIcon, LockIcon } from './Icons'
import { Banner } from './SettingEditor'
import { Skeleton } from './Skeleton'
import { useToast } from './Toast'

/**
 * The public demo account, on the Settings page.
 *
 * `demo.enabled` is an ordinary setting, but it is edited here and nowhere
 * else, because turning it on is the only moment the password and the API key
 * exist in readable form: the response to that one request carries both,
 * nothing stores them, and a generic boolean editor would drop them on the
 * floor. The settings list below points here instead of offering the switch.
 *
 * Everything shown afterwards comes from `GET /admin/demo`, which returns the
 * account name, the key prefix and the scopes and never the secrets. A lost
 * demo password is rotated, not recovered.
 *
 * Every role that can open Settings may read that state; only an
 * administrator may change it. The read used to be administrator-only, and the
 * card took the refusal for "off" - so an operator saw a running demo drawn as
 * switched off, beside a disabled "Turn demo mode on".
 */

interface DemoState {
  enabled: boolean
  provisioned: boolean
  username?: string
  password?: string | null
  api_key?: string | null
  api_key_prefix?: string
  scopes?: string[]
  /** Scopes this release publishes that the live key was minted without. */
  missing_scopes?: string[]
}

interface ToggleResponse {
  demo?: DemoState
}

/** What the confirmation is about to do. Each one cuts somebody off. */
type Pending = 'on' | 'off' | 'rotate'

export interface DemoCardProps {
  /** Whether this session may write SENSITIVE settings, i.e. is an admin. */
  canWrite: boolean
}

export function DemoCard({ canWrite }: DemoCardProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const invalidate = useInvalidate()

  /**
   * Held in component state and never refetched. The server cannot return
   * these a second time, so dropping them on a re-render would mean rotating
   * the credentials just to read them.
   */
  const [secrets, setSecrets] = useState<DemoState | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)

  const state = useApiQuery<DemoState>({ key: ['admin', 'demo'], path: paths.demo.show })

  const refresh = (): void => {
    void invalidate(['admin', 'demo'])
    void invalidate(['admin', 'settings'])
    void invalidate(['admin', 'api-keys'])
  }

  const toggle = useApiMutation<ToggleResponse, boolean>(
    (enabled) => apiPut(paths.settings.byKey('demo.enabled'), { value: enabled, confirm: true }),
    {
      onSuccess: (data, enabled) => {
        setPending(null)
        setSecrets(enabled ? (data?.demo ?? null) : null)
        toast.success(t(enabled ? 'settings.demo.toast.on' : 'settings.demo.toast.off'))
        refresh()
      },
      // There was no handler here, and no global one behind it: a refused
      // switch looked exactly like a click that had not registered.
      onError: (error, enabled) => {
        setPending(null)
        toast.apiError(error, t(enabled ? 'settings.demo.toast.onFailed' : 'settings.demo.toast.offFailed'))
      },
    },
  )

  const rotate = useApiMutation<DemoState, void>(() => apiPost(paths.demo.rotate, {}), {
    onSuccess: (data) => {
      setPending(null)
      setSecrets(data)
      toast.success(t('settings.demo.toast.rotated'))
      refresh()
    },
    onError: (error) => {
      setPending(null)
      toast.apiError(error, t('settings.demo.toast.rotateFailed'))
    },
  })

  const data = state.data
  const enabled = data?.enabled ?? false
  const busy = toggle.isPending || rotate.isPending
  const missing = enabled && data?.provisioned ? (data.missing_scopes ?? []) : []

  const confirm = (): void => {
    if (pending === 'rotate') rotate.mutate()
    else if (pending) toggle.mutate(pending === 'on')
  }

  return (
    <Card title={t('settings.demo.title')} description={t('settings.demo.body')}>
      <div className="u-stack">
        {state.isLoading ? (
          <Skeleton height={32} width={220} />
        ) : state.isError ? (
          // Not "off": an answer the console could not read is not an answer.
          errorCodeOf(state.error) === 'FORBIDDEN_SCOPE' ? (
            <Banner tone="caution" icon={<LockIcon size={14} />}>
              {t('settings.demo.cannotRead')}
            </Banner>
          ) : (
            <ErrorState
              error={state.error}
              compact
              onRetry={() => {
                void state.refetch()
              }}
            />
          )
        ) : canWrite ? (
          <div className="u-row" style={{ gap: 'var(--space-2)', flexWrap: 'wrap' }}>
            <Button
              variant={enabled ? 'ghost' : 'primary'}
              disabled={busy}
              loading={toggle.isPending}
              onClick={() => {
                setPending(enabled ? 'off' : 'on')
              }}
            >
              {enabled ? t('settings.demo.turnOff') : t('settings.demo.turnOn')}
            </Button>
            {enabled ? (
              <Button
                variant="ghost"
                disabled={busy}
                loading={rotate.isPending}
                onClick={() => {
                  setPending('rotate')
                }}
              >
                {t('settings.demo.rotate')}
              </Button>
            ) : null}
          </div>
        ) : (
          <p className="u-secondary" style={{ margin: 0 }}>
            {enabled ? t('settings.demo.stateOn') : t('settings.demo.stateOff')}
            <span className="u-xs u-muted"> · {t('settings.demo.adminOnly')}</span>
          </p>
        )}

        {/* On with nothing behind it is a real state rather than an impossible
            one: the setting is stored before the account is minted, so a
            database that was briefly unreachable leaves exactly this. */}
        {enabled && data && !data.provisioned ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('settings.demo.enabledButMissing')}
          </Banner>
        ) : null}

        {/* A key keeps the scopes of the release that minted it, so a demo
            published before a platform was added goes on refusing it. The
            upgrade notes ask for one reissue; this is where that shows. */}
        {missing.length > 0 ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {canWrite
              ? t('settings.demo.missingScopes', { scopes: missing.join(', ') })
              : t('settings.demo.missingScopesReadOnly', { scopes: missing.join(', ') })}
          </Banner>
        ) : null}

        {enabled && data?.provisioned ? (
          <dl className="u-stack-sm">
            <dt className="u-xs u-muted">{t('settings.demo.username')}</dt>
            <dd>
              <CopyableId value={data.username ?? 'demo'} label={t('settings.demo.username')} />
            </dd>
            <dt className="u-xs u-muted">{t('settings.demo.keyPrefix')}</dt>
            <dd className="u-mono u-xs">{data.api_key_prefix}</dd>
            <dt className="u-xs u-muted">{t('settings.demo.scopes')}</dt>
            <dd className="u-mono u-xs">{(data.scopes ?? []).join(' · ')}</dd>
          </dl>
        ) : null}

        {secrets?.password ? (
          <Banner tone="accent" icon={<InfoIcon size={14} />}>
            <div className="u-stack-sm">
              <strong>{t('settings.demo.shownOnce')}</strong>
              <span className="u-xs u-muted">{t('settings.demo.password')}</span>
              <CopyableId value={secrets.password} label={t('settings.demo.password')} wrap />
              <span className="u-xs u-muted">{t('settings.demo.apiKey')}</span>
              <CopyableId value={secrets.api_key ?? ''} label={t('settings.demo.apiKey')} wrap />
            </div>
          </Banner>
        ) : null}

        <p className="u-xs u-muted" style={{ margin: 0 }}>
          {t('settings.demo.note')}
        </p>
      </div>

      <ConfirmDialog
        open={pending !== null}
        danger={pending !== 'on'}
        loading={busy}
        title={
          pending === 'rotate'
            ? t('settings.demo.confirm.rotateTitle')
            : pending === 'off'
              ? t('settings.demo.confirm.offTitle')
              : t('settings.demo.confirm.onTitle')
        }
        description={
          pending === 'rotate'
            ? t('settings.demo.confirm.rotateBody')
            : pending === 'off'
              ? t('settings.demo.confirm.offBody')
              : t('settings.demo.confirm.onBody')
        }
        confirmLabel={
          pending === 'rotate'
            ? t('settings.demo.rotate')
            : pending === 'off'
              ? t('settings.demo.turnOff')
              : t('settings.demo.turnOn')
        }
        onCancel={() => {
          setPending(null)
        }}
        onConfirm={confirm}
      />
    </Card>
  )
}
