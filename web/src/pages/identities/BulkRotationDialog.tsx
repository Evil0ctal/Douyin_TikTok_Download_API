import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Modal } from '@/components'

import { RotationEditor, endpointsOf, type RotationDraft } from './rotation'
import { usePlatformInSentence } from './shared'
import type { IdentityRow, PoolLevel } from './types'

/**
 * One rotation choice for several identities at once.
 *
 * Three logins bought for the author feed are three trips through three
 * drawers otherwise, each with the same five clicks. A list of endpoints only
 * means something on one platform, so it is offered only when every selected
 * identity is on the same one.
 */
export function BulkRotationDialog({
  targets,
  pool,
  busy,
  onCancel,
  onApply,
}: {
  /** Null when closed. Retired identities are left out by the caller. */
  targets: IdentityRow[] | null
  pool: PoolLevel | undefined
  busy: boolean
  onCancel: () => void
  onApply: (endpoints: string[] | null) => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const platformPhrase = usePlatformInSentence()
  const [draft, setDraft] = useState<RotationDraft>({ mode: 'limited', picked: [] })

  const rows = targets ?? []
  const platforms = [...new Set(rows.map((row) => row.platform))]
  const platform = platforms.length === 1 ? (platforms[0] ?? null) : null
  // Limited is the reason this dialog exists, so it is where it opens; with
  // more than one platform selected it cannot be chosen at all.
  const value: RotationDraft =
    platform === null && draft.mode === 'limited' ? { ...draft, mode: 'all' } : draft
  const next = endpointsOf(value)
  const losses = lostLogins(rows, pool, next?.endpoints ?? null)

  return (
    <Modal
      open={targets !== null}
      onClose={onCancel}
      title={t('console:identity.rotation.bulkTitle', { count: rows.length })}
      description={t('console:identity.rotation.bulkDescription')}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onCancel}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant={losses.length > 0 ? 'danger' : 'primary'}
            disabled={next === null}
            loading={busy}
            onClick={() => {
              if (next !== null) onApply(next.endpoints)
            }}
          >
            {t('console:identity.rotation.bulkApply', { count: rows.length })}
          </Button>
        </>
      }
    >
      <RotationEditor platform={platform} value={value} onChange={setDraft} disabled={busy} />
      {losses.map((loss) => (
        <p key={loss.platform} className="u-xs" style={{ margin: 0, color: 'var(--caution)' }}>
          {t('console:identity.rotation.bulkLoses', {
            platform: platformPhrase(loss.platform),
            endpoints: loss.endpoints.join(', '),
          })}
        </p>
      ))}
    </Modal>
  )
}

/**
 * Per platform, the endpoints needing a login that would be left with none.
 *
 * The same approximation as the single-identity question: the pool counts a
 * platform's logins in rotation for any endpoint needing one, and this warns
 * when every one of them is in the selection and the new list leaves them out.
 */
function lostLogins(
  targets: IdentityRow[],
  pool: PoolLevel | undefined,
  next: readonly string[] | null,
): Array<{ platform: IdentityRow['platform']; endpoints: string[] }> {
  if (!pool) return []
  const losses: Array<{ platform: IdentityRow['platform']; endpoints: string[] }> = []
  for (const row of pool.platforms) {
    const needed = row.login_required_for ?? []
    if (!row.logins || needed.length === 0 || row.logins.rotating === 0) continue
    const leaving = needed.filter((endpoint) => next !== null && !next.includes(endpoint))
    if (leaving.length === 0) continue
    const logins = targets.filter(
      (target) =>
        target.platform === row.platform &&
        target.authenticated &&
        (target.state === 'active' || target.state === 'degraded') &&
        leaving.some(
          (endpoint) =>
            target.rotation_endpoints == null || target.rotation_endpoints.includes(endpoint),
        ),
    ).length
    if (logins >= row.logins.rotating) losses.push({ platform: row.platform, endpoints: leaving })
  }
  return losses
}
