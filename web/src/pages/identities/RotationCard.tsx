import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AlertIcon, Banner, Button, Card, Switch, useToast } from '@/components'
import { useApiMutation } from '@/hooks'
import { apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import { lastLoginEndpoints, usePlatformInSentence } from './shared'
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
  const platformPhrase = usePlatformInSentence()
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
        {/* Asked here, in the card, rather than in a dialog over the drawer:
            the two overlays both answer Escape, so cancelling the question
            closed the drawer it was asked from. Reserving is undone by the
            same switch, so a question in place is enough. */}
        {confirming !== null ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            <div className="u-stack-sm">
              <strong>
                {t('console:identity.logins.lastTitle', {
                  platform: platformPhrase(identity.platform),
                })}
              </strong>
              <span>
                {t('console:identity.logins.lastBody', {
                  platform: platformPhrase(identity.platform),
                  endpoints: confirming.join(', '),
                })}
              </span>
              <span className="u-row u-wrap">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={save.isPending}
                  onClick={() => {
                    setConfirming(null)
                  }}
                >
                  {t('common:action.cancel')}
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  loading={save.isPending}
                  onClick={() => {
                    save.mutate([])
                  }}
                >
                  {t('console:identity.logins.reserveAnyway')}
                </Button>
              </span>
            </div>
          </Banner>
        ) : null}
      </div>
    </Card>
  )
}
