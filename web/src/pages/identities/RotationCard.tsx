import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Card, ConfirmDialog, Switch, useToast } from '@/components'
import { useApiMutation } from '@/hooks'
import { apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import { lastLoginEndpoints, usePlatformName } from './shared'
import type { IdentityRow, PoolLevel } from './types'

/**
 * Whether rotation may hand this identity out (issue 789).
 *
 * The switch covers the one case the console is for - keep an account for the
 * requests that name it - and an endpoint list set through the API is shown
 * as it is rather than flattened, with the same switch putting it back.
 *
 * Reserving the last login a platform has in rotation asks first. The
 * scheduler skips a reserved login even for an endpoint that requires one, so
 * the switch that reads "keep my account safe" also reads "every later page of
 * an author's posts now fails" - which nobody would guess from the switch.
 */
export function RotationCard({
  identity,
  canManage,
  pool,
  onChanged,
}: {
  identity: IdentityRow
  canManage: boolean
  pool: PoolLevel | undefined
  onChanged: () => Promise<void>
}) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const platformName = usePlatformName()
  const [confirming, setConfirming] = useState<string[] | null>(null)

  const save = useApiMutation<{ rotation_endpoints: string[] | null }, string[] | null>(
    (next) =>
      apiPost<{ rotation_endpoints: string[] | null }>(paths.identities.rotation(identity.id), {
        endpoints: next,
      }),
    {
      onSuccess: async () => {
        setConfirming(null)
        toast.success(t('console:identity.rotation.saved'))
        await onChanged()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.rotation.failed'))
      },
    },
  )

  const endpoints = identity.rotation_endpoints ?? null
  const limited = endpoints !== null && endpoints.length > 0

  if (!canManage) {
    return (
      <Card title={t('console:identity.rotation.title')}>
        <p className="u-secondary" style={{ margin: 0 }}>
          {endpoints === null
            ? t('console:identity.rotation.everywhere')
            : limited
              ? t('console:identity.rotation.limited', { endpoints: endpoints.join(', ') })
              : t('console:identity.rotation.reservedHint')}
        </p>
      </Card>
    )
  }

  return (
    <Card title={t('console:identity.rotation.title')}>
      <div className="u-stack-sm">
        <Switch
          checked={endpoints !== null}
          disabled={save.isPending}
          label={
            limited
              ? t('console:identity.rotation.limited', { endpoints: endpoints.join(', ') })
              : t('console:identity.rotation.reserve')
          }
          hint={
            limited
              ? t('console:identity.rotation.limitedHint')
              : t('console:identity.rotation.reserveHint')
          }
          onChange={(event) => {
            if (!event.target.checked) {
              save.mutate(null)
              return
            }
            const losing = lastLoginEndpoints(identity, pool)
            if (losing) setConfirming(losing)
            else save.mutate([])
          }}
        />
      </div>
      <ConfirmDialog
        open={confirming !== null}
        danger
        loading={save.isPending}
        title={t('console:identity.logins.lastTitle', { platform: platformName(identity.platform) })}
        description={t('console:identity.logins.lastBody', {
          platform: platformName(identity.platform),
          endpoints: (confirming ?? []).join(', '),
        })}
        confirmLabel={t('console:identity.logins.reserveAnyway')}
        onCancel={() => {
          setConfirming(null)
        }}
        onConfirm={() => {
          save.mutate([])
        }}
      />
    </Card>
  )
}
