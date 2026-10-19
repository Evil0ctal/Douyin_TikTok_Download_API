import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, ErrorState, Input, Modal, Select, useToast } from '@/components'
import { useApiMutation, useApiQuery } from '@/hooks'
import { apiPost, isApiError, waitForTask } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { PLATFORMS, type Platform } from '@/lib/types'

import { usePlatformName } from './shared'
import { POOL_KEY, type PoolLevel, type ProxyOption } from './types'

/** The platforms minted without a browser; see NATIVE_MINT_PLATFORMS. */
const NATIVE: ReadonlySet<Platform> = new Set<Platform>(['bilibili'])

const MAX_COUNT = 10

interface MintDialogProps {
  open: boolean
  proxies: ProxyOption[]
  onClose: () => void
  onDone: () => Promise<void>
}

/**
 * Mint guest identities by hand.
 *
 * Offers only what this instance can mint. It offered every platform, so an
 * install with no browser container - a supported way to run this, and the
 * documented default - answered "Mint Douyin" with a task that failed. The
 * pool already says which platforms can be minted; the refill card and the
 * setup wizard read it, and now this does too.
 *
 * One task per identity, waited out here, and one toast for the lot: asking
 * for ten against a browser that is down used to raise ten identical red
 * toasts, each with the error code and none with the reason.
 */
export function MintDialog({ open, proxies, onClose, onDone }: MintDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const platformName = usePlatformName()

  // Shares the refill card's cache entry, so opening this costs no request.
  const pool = useApiQuery<PoolLevel>({ key: POOL_KEY, path: paths.identities.pool })
  const mintable = useMemo<Platform[]>(() => {
    const level = pool.data
    if (!level) return []
    return level.platforms
      .filter((row) => row.can_mint ?? level.can_mint)
      .map((row) => row.platform)
  }, [pool.data])
  const browserOnly = PLATFORMS.filter((value) => !mintable.includes(value))

  const [chosen, setChosen] = useState<Platform | null>(null)
  const platform = chosen !== null && mintable.includes(chosen) ? chosen : (mintable[0] ?? null)
  const [count, setCount] = useState(1)
  const [proxyId, setProxyId] = useState('')
  const [progress, setProgress] = useState<{ finished: number; total: number } | null>(null)

  const mint = useApiMutation<{ task_ids: string[]; count: number }, Platform>(
    (target) =>
      apiPost<{ task_ids: string[]; count: number }>(paths.identities.mint, {
        platform: target,
        count,
        proxy_id: proxyId || null,
      }),
    {
      onSuccess: async (queued) => {
        // Minting is queued; wait it out here so the page reports a fact, not
        // a submission.
        const total = queued.task_ids.length
        setProgress({ finished: 0, total })
        let done = 0
        const reasons = new Map<string, number>()
        for (const [index, taskId] of queued.task_ids.entries()) {
          try {
            await waitForTask(taskId, { timeoutMs: 180_000 })
            done += 1
          } catch (error) {
            const label = failureReason(error)
            reasons.set(label, (reasons.get(label) ?? 0) + 1)
          }
          setProgress({ finished: index + 1, total })
        }
        setProgress(null)
        report(done, total - done, reasons)
        await onDone()
        onClose()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.mint.failed'))
      },
    },
  )

  /**
   * The worker's reason code, in the words the refill card uses for it. The
   * mint task fails with the code in `details.reason` - `rpc_unavailable`,
   * `no_free_proxy` - and the error's own message is the generic sentence for
   * its error code, which says that something failed and never why.
   */
  function failureReason(error: unknown): string {
    if (isApiError(error)) {
      const reason = error.details['reason']
      if (typeof reason === 'string') {
        return t(`console:identity.mintLog.reason.${reason}`, { defaultValue: reason })
      }
      return error.message
    }
    return error instanceof Error ? error.message : String(error)
  }

  function report(done: number, failed: number, reasons: Map<string, number>) {
    const why = [...reasons.entries()]
      .map(([reason, times]) =>
        times > 1 ? t('console:identity.mint.reasonTimes', { reason, count: times }) : reason,
      )
      .join('; ')
    if (failed === 0) {
      toast.success(t('console:identity.mint.done', { count: done }))
    } else if (done === 0) {
      toast.error(t('console:identity.mint.failed'), { description: why })
    } else {
      toast.warning(t('console:identity.mint.partial', { done, failed }), { description: why })
    }
  }

  const busy = mint.isPending || progress !== null
  const native = platform !== null && NATIVE.has(platform)

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('console:identity.mint.title')}
      description={
        platform === null
          ? t('console:identity.mint.description')
          : native
            ? t('console:identity.mint.descriptionNative', { platform: platformName(platform) })
            : t('console:identity.mint.descriptionBrowser', { platform: platformName(platform) })
      }
      closeOnBackdrop={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common:action.cancel')}
          </Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={platform === null}
            onClick={() => {
              if (platform !== null) mint.mutate(platform)
            }}
          >
            {t('console:identity.mint.submit', { count })}
          </Button>
        </>
      }
    >
      <div className="u-stack">
        {pool.isError ? <ErrorState error={pool.error} compact /> : null}
        {pool.isSuccess && mintable.length === 0 ? (
          <p className="u-secondary" style={{ margin: 0 }}>
            {t('console:identity.mint.none')}
          </p>
        ) : (
          <>
            <Select
              label={t('console:field.platform')}
              value={platform ?? ''}
              disabled={busy || mintable.length === 0}
              onChange={(event) => {
                setChosen(event.target.value as Platform)
              }}
              options={mintable.map((value) => ({ value, label: platformName(value) }))}
            />
            <Input
              label={t('console:identity.mint.count')}
              type="number"
              min={1}
              max={MAX_COUNT}
              mono
              disabled={busy}
              value={count}
              onChange={(event) => {
                const next = Number(event.target.value)
                setCount(
                  Number.isFinite(next) ? Math.min(MAX_COUNT, Math.max(1, Math.trunc(next))) : 1,
                )
              }}
            />
            <Select
              label={t('console:field.proxy')}
              description={t('console:identity.mint.proxyHint')}
              showOptional
              disabled={busy}
              value={proxyId}
              onChange={(event) => {
                setProxyId(event.target.value)
              }}
              options={[
                { value: '', label: t('console:identity.mint.anyProxy') },
                ...proxies.map((proxy) => ({
                  value: proxy.id,
                  label: proxy.label ?? proxy.url_masked ?? proxy.id,
                  disabled: !proxy.healthy,
                })),
              ]}
            />
          </>
        )}
        {browserOnly.length > 0 && pool.isSuccess ? (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('console:identity.mint.browserOnly', {
              platforms: browserOnly.map(platformName).join(', '),
            })}
          </p>
        ) : null}
        {progress ? (
          <p className="u-xs u-muted" role="status" style={{ margin: 0 }}>
            {t('console:identity.mint.progress', progress)}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}
