import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useApiMutation, useApiQuery, useInvalidate } from '@/hooks'
import { apiPost, apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import { Button } from './Button'
import { Card } from './Card'
import { CopyableId } from './CopyableId'
import { AlertIcon, InfoIcon } from './Icons'
import { Banner } from './SettingEditor'
import { useToast } from './Toast'

/**
 * The public demo account, on the Settings page.
 *
 * `demo.enabled` is an ordinary setting and the generic editor below this card
 * could flip it. It gets its own card anyway, because turning it on is the only
 * moment the password and the API key exist in readable form: the response to
 * that one request carries both, nothing stores them, and a generic boolean
 * editor would drop them on the floor. So the switch lives here, next to the
 * place its output is shown.
 *
 * Everything shown afterwards comes from `GET /admin/demo`, which returns the
 * account name, the key prefix and the scopes and never the secrets - because
 * it cannot. A lost demo password is rotated, not recovered.
 */

interface DemoState {
  enabled: boolean
  provisioned: boolean
  username?: string
  password?: string | null
  api_key?: string | null
  api_key_prefix?: string
  scopes?: string[]
}

interface ToggleResponse {
  demo?: DemoState
}

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

  const state = useApiQuery<DemoState>({ key: ['admin', 'demo'], path: paths.demo.show })

  const toggle = useApiMutation<ToggleResponse, boolean>(
    (enabled) => apiPut(paths.settings.byKey('demo.enabled'), { value: enabled, confirm: true }),
    {
      onSuccess: (data, enabled) => {
        setSecrets(enabled ? (data?.demo ?? null) : null)
        toast.success(t(enabled ? 'settings.demo.toast.on' : 'settings.demo.toast.off'))
        void invalidate(['admin', 'demo'])
        void invalidate(['admin', 'settings'])
      },
    },
  )

  const rotate = useApiMutation<DemoState, void>(() => apiPost(paths.demo.rotate, {}), {
    onSuccess: (data) => {
      setSecrets(data)
      toast.success(t('settings.demo.toast.rotated'))
      void invalidate(['admin', 'demo'])
    },
  })

  const enabled = state.data?.enabled ?? false
  const busy = toggle.isPending || rotate.isPending

  return (
    <Card title={t('settings.demo.title')} description={t('settings.demo.body')}>
      <div className="u-stack">
        <div className="u-row" style={{ gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <Button
            variant={enabled ? 'ghost' : 'primary'}
            disabled={!canWrite || busy}
            onClick={() => {
              toggle.mutate(!enabled)
            }}
          >
            {enabled ? t('settings.demo.turnOff') : t('settings.demo.turnOn')}
          </Button>
          {enabled ? (
            <Button
              variant="ghost"
              disabled={!canWrite || busy}
              onClick={() => {
                rotate.mutate()
              }}
            >
              {t('settings.demo.rotate')}
            </Button>
          ) : null}
        </div>

        {/* On with nothing behind it is a real state rather than an impossible
            one: the setting is stored before the account is minted, so a
            database that was briefly unreachable leaves exactly this. */}
        {enabled && state.data && !state.data.provisioned ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('settings.demo.enabledButMissing')}
          </Banner>
        ) : null}

        {enabled && state.data?.provisioned ? (
          <dl className="u-stack-sm">
            <dt className="u-xs u-muted">{t('settings.demo.username')}</dt>
            <dd>
              <CopyableId
                value={state.data.username ?? 'demo'}
                label={t('settings.demo.username')}
              />
            </dd>
            <dt className="u-xs u-muted">{t('settings.demo.keyPrefix')}</dt>
            <dd className="u-mono u-xs">{state.data.api_key_prefix}</dd>
            <dt className="u-xs u-muted">{t('settings.demo.scopes')}</dt>
            <dd className="u-mono u-xs">{(state.data.scopes ?? []).join(' · ')}</dd>
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
    </Card>
  )
}
