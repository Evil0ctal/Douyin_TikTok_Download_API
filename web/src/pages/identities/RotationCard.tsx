import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AlertIcon, Banner, Button, Card, useToast } from '@/components'
import { useApiMutation } from '@/hooks'
import { apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import {
  RotationEditor,
  draftOf,
  endpointsOf,
  loginEndpointsLost,
  sameRotation,
  useEndpointLabel,
  useRotationEndpoints,
  type RotationDraft,
} from './rotation'
import { usePlatformInSentence } from './shared'
import type { IdentityRow, PoolLevel } from './types'

/**
 * What rotation may hand this identity out for (issue 789).
 *
 * Every endpoint, a list of them, or only the requests that name it. The list
 * is the one the console never offered: it is how a login is kept for the
 * endpoints that need one without also being spent on everything else, which
 * is what somebody reaching for "reserve" almost always wanted.
 *
 * Taking the last login a platform has in rotation away from an endpoint that
 * requires one asks first. The scheduler skips it there even for a later page
 * of an author's posts, so a change that reads "keep my account safe" also
 * reads "every later page now fails" - which nobody would guess from it.
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
  const label = useEndpointLabel()
  const { rows } = useRotationEndpoints(identity.platform)
  const stored = identity.rotation_endpoints ?? null
  // Null while untouched, so a save elsewhere - the bulk dialog, another tab -
  // shows here instead of being held back by a draft nobody edited.
  const [draft, setDraft] = useState<RotationDraft | null>(null)
  const [confirming, setConfirming] = useState<string[] | null>(null)
  const current = draft ?? draftOf(stored)
  const next = endpointsOf(current)
  const dirty = draft !== null && next !== null && !sameRotation(next.endpoints, stored)

  const save = useApiMutation<{ rotation_endpoints: string[] | null }, string[] | null>(
    (endpoints) =>
      apiPost<{ rotation_endpoints: string[] | null }>(paths.identities.rotation(identity.id), {
        endpoints,
      }),
    {
      onSuccess: async () => {
        setConfirming(null)
        setDraft(null)
        toast.success(t('console:identity.rotation.saved'))
        await onChanged()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.rotation.failed'))
      },
    },
  )

  if (!canManage) {
    const named = (stored ?? []).map((endpoint) => {
      const row = rows.find((entry) => entry.endpoint === endpoint)
      return row ? label(row) : endpoint
    })
    return (
      <Card title={t('console:identity.rotation.title')}>
        <p className="u-secondary" style={{ margin: 0 }}>
          {stored === null
            ? t('console:identity.rotation.everywhere')
            : stored.length > 0
              ? t('console:identity.rotation.limited', { endpoints: named.join(', ') })
              : t('console:identity.rotation.reservedHint')}
        </p>
      </Card>
    )
  }

  const apply = () => {
    if (next === null) return
    const lost = loginEndpointsLost(identity, pool, next.endpoints)
    if (lost) setConfirming(lost)
    else save.mutate(next.endpoints)
  }

  return (
    <Card title={t('console:identity.rotation.title')}>
      <div className="u-stack-sm">
        <RotationEditor
          platform={identity.platform}
          value={current}
          disabled={save.isPending}
          identityId={identity.id}
          onChange={(value) => {
            setConfirming(null)
            setDraft(value)
          }}
        />
        {draft !== null ? (
          <span className="u-row u-wrap">
            <Button
              size="sm"
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                setDraft(null)
                setConfirming(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!dirty}
              loading={save.isPending && confirming === null}
              onClick={apply}
            >
              {t('common:action.save')}
            </Button>
          </span>
        ) : null}
        {/* Asked here, in the card, rather than in a dialog over the drawer:
            the two overlays both answer Escape, so cancelling the question
            closed the drawer it was asked from. */}
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
                    if (next !== null) save.mutate(next.endpoints)
                  }}
                >
                  {t('console:identity.logins.changeAnyway')}
                </Button>
              </span>
            </div>
          </Banner>
        ) : null}
      </div>
    </Card>
  )
}
