import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { AlertIcon, Button, Card, ErrorState, LockIcon, Skeleton, Switch, useToast } from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import type { Platform } from '@/lib/types'

import styles from '../identities.module.css'
import { usePlatformInSentence, usePlatformName } from './shared'
import { POOL_KEY, type MintActivity, type PoolLevel, type PoolPlatform } from './types'

/** The value a per-platform setting stores to follow the global one. */
const INHERIT = -1

/**
 * One platform's row as the operator is editing it.
 *
 * Numbers are held as text, not as numbers: an input the operator has emptied
 * on the way to typing "12" is not the number zero, and coercing it would
 * fight them mid-keystroke.
 */
interface MarksDraft {
  auto: boolean
  minimum: string
  target: string
}

type Drafts = Partial<Record<Platform, MarksDraft>>

function draftOf(row: PoolPlatform): MarksDraft {
  return { auto: row.auto, minimum: String(row.min_size), target: String(row.target_size) }
}

/**
 * The numbers a draft asks for, or null when it cannot be saved.
 *
 * With automatic minting on, the mark must be at least one: zero is how "off"
 * is stored, and a switch that reads "on" over a mark of zero would be lying.
 * The target may not sit under the mark, because the worker would clamp it -
 * and a field that silently means something else is worse than one that will
 * not save. With minting off the target is not asked about and stays as it
 * was, so switching back on restores it.
 */
function parseDraft(
  draft: MarksDraft,
  row: PoolPlatform,
): { minimum: number; target: number } | null {
  if (!draft.auto) return { minimum: 0, target: row.target_size }
  const minimum = Number(draft.minimum)
  const target = Number(draft.target)
  if (draft.minimum.trim() === '' || draft.target.trim() === '') return null
  if (!Number.isInteger(minimum) || !Number.isInteger(target)) return null
  if (minimum < 1 || target < minimum) return null
  return { minimum, target }
}

/**
 * What to store for one mark: the number, or INHERIT when it equals the global
 * value. Storing a copy of the global number would pin this platform to it, so
 * a later change on the scheduler page would silently stop applying here.
 */
function storedValue(value: number, global: number): number {
  return value === global ? INHERIT : value
}

/**
 * What the refill job is doing, on the page where an operator looks for it.
 *
 * The job has always existed - the worker checks every platform once a minute
 * and mints one identity at a time back up to the target - and nothing on this
 * board said so, which made it indistinguishable from not existing.
 *
 * Each platform has its own switch and marks. They used to be one shared pair,
 * which meant a deployment that only serves Douyin kept minting TikTok
 * identities it had no route to, and failing, every minute (issue 763).
 *
 * `usable` rather than a row count, because that is the number the job compares
 * against the mark: an identity that fails every request stays live, so a pool
 * counted by rows sits at its target while serving nothing.
 *
 * Each platform also says how many of its logins rotation can reach. Douyin
 * pages an author's posts, and Bilibili a video's comments, only for a login;
 * with none in rotation those answer IDENTITY_LOGIN_REQUIRED, and until this
 * line the error was the first anybody heard of it.
 */
export function RefillCard({
  canManage,
  onImport,
}: {
  /** Settings writes take the operator role; a viewer reads the numbers. */
  canManage: boolean
  /** Open the import dialog preset to this platform. */
  onImport: (platform: Platform) => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const invalidate = useInvalidate()

  const query = useApiQuery<PoolLevel>({
    key: POOL_KEY,
    path: paths.identities.pool,
    // A mint takes about five seconds. On the slow beat the panel showed every
    // result and none of the acts, which is the half an operator watches for
    // after retiring a platform's identities.
    poll: POLL.fast,
  })

  const [drafts, setDrafts] = useState<Drafts>({})

  /** `only` clears one platform's draft, so a reset keeps the other's edits. */
  const afterWrite = (message: string, only?: Platform) => {
    setDrafts((current) => {
      if (only === undefined) return {}
      const { [only]: _dropped, ...rest } = current
      return rest
    })
    void invalidate(POOL_KEY)
    // The scheduler page renders the same settings, so it must not be left
    // showing the numbers this card just replaced.
    void invalidate(['admin', 'settings'])
    toast.success(message)
  }

  const writeSetting = (key: string, value: number) =>
    apiPut(paths.settings.byKey(key), { value, confirm: false })

  const save = useApiMutation<unknown, Array<{ key: string; value: number }>>(
    async (writes) => {
      // Sequential, and only what changed: saving one number must not stamp
      // the others with identical values and move them from "default" to "set
      // by hand" in the settings audit.
      for (const { key, value } of writes) await writeSetting(key, value)
      return null
    },
    {
      onSuccess: () => {
        afterWrite(t('console:identity.refill.saved'))
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.refill.saveFailed'))
      },
    },
  )

  // Back to following the global pair. Its own action rather than "type the
  // global value back in", which cannot express it when an override happens
  // to equal the global number: that row is unchanged, so nothing was saved,
  // and it stayed pinned to a value the scheduler page no longer moves.
  const reset = useApiMutation<unknown, PoolPlatform>(
    async (row) => {
      if (!row.min_inherited) await writeSetting(`pool.${row.platform}.min_size`, INHERIT)
      if (!row.target_inherited) await writeSetting(`pool.${row.platform}.target_size`, INHERIT)
      return null
    },
    {
      onSuccess: (_data, row) => {
        afterWrite(t('console:identity.refill.resetDone'), row.platform)
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.refill.saveFailed'))
      },
    },
  )

  if (query.isLoading) {
    return (
      <Card flush>
        <div className={styles.refill}>
          <Skeleton height={20} width={280} />
        </div>
      </Card>
    )
  }
  // Said rather than dropped. The card used to vanish when this query failed,
  // which reads as "this instance has no refill" - the opposite of the truth,
  // and on the one page where an operator goes to check it.
  if (query.isError || !query.data) {
    return (
      <Card flush>
        <div className={styles.refill}>
          <span className={styles.refillTitle}>{t('console:identity.refill.title')}</span>
        </div>
        <div className={styles.refillRows}>
          <ErrorState
            error={query.error}
            compact
            title={t('console:identity.refill.loadFailed')}
            onRetry={() => {
              void query.refetch()
            }}
          />
        </div>
      </Card>
    )
  }

  const { can_mint: canMint, platforms } = query.data
  // The browser decides Douyin and TikTok; Bilibili is minted without one, so
  // "can anything be minted here" is per platform now.
  const rowCanMint = (row: PoolPlatform): boolean => row.can_mint ?? canMint
  const anyCanMint = platforms.some(rowCanMint)
  const global = { minimum: query.data.min_size, target: query.data.target_size }

  const writes: Array<{ key: string; value: number }> = []
  let valid = true
  for (const row of platforms) {
    const draft = drafts[row.platform]
    if (!draft) continue
    const parsed = parseDraft(draft, row)
    if (!parsed) {
      valid = false
      continue
    }
    if (parsed.minimum !== row.min_size) {
      writes.push({
        key: `pool.${row.platform}.min_size`,
        // Off is stored as a literal zero, never as "inherit", even when the
        // global mark is zero too: inheriting would switch this platform back
        // on the day someone raised the global mark on the scheduler page.
        value: parsed.minimum === 0 ? 0 : storedValue(parsed.minimum, global.minimum),
      })
    }
    if (parsed.target !== row.target_size) {
      writes.push({
        key: `pool.${row.platform}.target_size`,
        value: storedValue(parsed.target, global.target),
      })
    }
  }
  const dirty = valid && writes.length > 0

  const update = (row: PoolPlatform, patch: Partial<MarksDraft>) => {
    setDrafts((current) => ({
      ...current,
      [row.platform]: { ...(current[row.platform] ?? draftOf(row)), ...patch },
    }))
  }

  return (
    <Card flush>
      <div className={styles.refill}>
        <span className={styles.refillTitle}>{t('console:identity.refill.title')}</span>
        <span className={styles.refillSpacer} />
        {anyCanMint && canManage ? (
          <>
            {Object.keys(drafts).length > 0 ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={save.isPending}
                onClick={() => {
                  setDrafts({})
                }}
              >
                {t('common:action.cancel')}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="primary"
              disabled={!dirty}
              loading={save.isPending}
              onClick={() => {
                save.mutate(writes)
              }}
            >
              {t('common:action.save')}
            </Button>
          </>
        ) : null}
      </div>

      <div className={styles.refillRows}>
        {platforms.map((row) => (
          <div key={row.platform} className={styles.refillPlatform}>
            <RefillRow
              row={row}
              draft={drafts[row.platform] ?? draftOf(row)}
              canMint={rowCanMint(row)}
              canManage={canManage}
              globalMinimum={global.minimum}
              disabled={!canManage || save.isPending || reset.isPending}
              resetting={reset.isPending && reset.variables.platform === row.platform}
              onChange={(patch) => {
                update(row, patch)
              }}
              onReset={() => {
                reset.mutate(row)
              }}
            />
            <div className={styles.refillMeta}>
              <LoginLine row={row} canManage={canManage} onImport={onImport} />
              <PlatformActivity
                activity={query.data.activity}
                platform={row.platform}
                canMint={rowCanMint(row)}
              />
            </div>
          </div>
        ))}
      </div>

      <p className={styles.refillHint}>
        {platforms.every(rowCanMint)
          ? t('console:identity.refill.description', {
              minimum: global.minimum,
              target: global.target,
            })
          : anyCanMint
            ? t('console:identity.refill.nativeOnly')
            : t('console:identity.refill.noBrowser')}{' '}
        {t('console:identity.refill.usableHint', { streak: query.data.max_fail_streak })}
      </p>
    </Card>
  )
}

/**
 * One platform: its level, whether it is minted for at all, and its marks.
 *
 * The "default" tag says whether a number is this platform's own or the global
 * one. Without it, a platform that silently follows the scheduler page looks
 * exactly like one somebody set by hand, and changing the global value then
 * moves one row and not the other for no reason the card admits to.
 */
function RefillRow({
  row,
  draft,
  canMint,
  canManage,
  globalMinimum,
  disabled,
  resetting,
  onChange,
  onReset,
}: {
  row: PoolPlatform
  draft: MarksDraft
  canMint: boolean
  canManage: boolean
  /** Where a platform switched back on from zero starts. */
  globalMinimum: number
  disabled: boolean
  resetting: boolean
  onChange: (patch: Partial<MarksDraft>) => void
  onReset: () => void
}) {
  const { t } = useTranslation(['console', 'common'])
  const platformName = usePlatformName()
  const platformPhrase = usePlatformInSentence()
  const invalid = draft.auto && parseDraft(draft, row) === null

  const title = !canMint
    ? t('console:identity.refill.manualOnly')
    : !row.auto
      ? t('console:identity.refill.offHint')
      : row.minting > 0
        ? t('console:identity.refill.minting', { count: row.minting })
        : row.below_minimum
          ? t('console:identity.refill.below', { minimum: row.min_size })
          : t('console:identity.refill.satisfied', { minimum: row.min_size })

  return (
    <div className={styles.refillRow} data-off={canMint && !draft.auto}>
      <span
        className={styles.pool}
        data-low={canMint && row.auto && row.below_minimum}
        title={title}
      >
        <span>{platformName(row.platform)}</span>
        <b>{row.usable}</b>
        {canMint && row.minting > 0 ? <span className="u-xs u-muted">+{row.minting}</span> : null}
      </span>

      {canMint ? (
        <>
          <Switch
            checked={draft.auto}
            disabled={disabled}
            aria-label={t('console:identity.refill.autoFor', {
              platform: platformPhrase(row.platform),
            })}
            label={<span className="u-xs">{t('console:identity.refill.auto')}</span>}
            onChange={(event) => {
              const auto = event.target.checked
              // Switching back on from a stored zero needs a mark to start
              // from; the global one is the number the operator last chose.
              onChange(
                auto && Number(draft.minimum) < 1
                  ? { auto, minimum: String(Math.max(1, globalMinimum)) }
                  : { auto },
              )
            }}
          />

          {draft.auto ? (
            <>
              {(['minimum', 'target'] as const).map((field) => {
                const inherited = field === 'minimum' ? row.min_inherited : row.target_inherited
                return (
                  <label key={field} className={styles.refillField}>
                    <span className="u-xs u-muted">{t(`console:identity.refill.${field}`)}</span>
                    <input
                      type="number"
                      min={field === 'minimum' ? 1 : 0}
                      max={999}
                      inputMode="numeric"
                      className={styles.refillInput}
                      aria-invalid={invalid || undefined}
                      title={invalid ? t('console:identity.refill.invalid') : undefined}
                      disabled={disabled}
                      value={draft[field]}
                      onChange={(event) => {
                        onChange({ [field]: event.target.value })
                      }}
                    />
                    {inherited ? (
                      <span
                        className={styles.refillInherited}
                        title={t('console:identity.refill.inheritedHint')}
                      >
                        {t('console:identity.refill.inherited')}
                      </span>
                    ) : null}
                  </label>
                )
              })}
            </>
          ) : (
            <span className="u-xs u-muted">{t('console:identity.refill.offHint')}</span>
          )}

          {/* Only when there is something to reset, and only for somebody who
              may. A platform switched off is an override too (a stored zero),
              so this is also how it goes back to following the global marks -
              switched on, if they are. */}
          {!canManage || (row.min_inherited && row.target_inherited) ? null : (
            <Button
              size="sm"
              variant="ghost"
              disabled={disabled}
              loading={resetting}
              title={t('console:identity.refill.resetHint')}
              onClick={onReset}
            >
              {t('console:identity.refill.reset')}
            </Button>
          )}
        </>
      ) : null}
    </div>
  )
}

/**
 * How many of this platform's logins rotation can reach, and what that costs
 * when it is none.
 *
 * Warned only where some endpoint refuses a guest outright - Douyin's author
 * posts, Bilibili's comments and replies, past the first page. TikTok has no
 * such endpoint, so zero logins there is a fact rather than a fault.
 */
function LoginLine({
  row,
  canManage,
  onImport,
}: {
  row: PoolPlatform
  canManage: boolean
  onImport: (platform: Platform) => void
}) {
  const { t } = useTranslation('console')
  const logins = row.logins
  if (!logins) return null

  const needed = row.login_required_for ?? []
  const missing = logins.rotating === 0 && needed.length > 0
  const parts = [
    logins.reserved > 0 ? t('identity.logins.reserved', { count: logins.reserved }) : null,
    logins.cooling > 0 ? t('identity.logins.cooling', { count: logins.cooling }) : null,
    logins.limited > 0 ? t('identity.logins.limited', { count: logins.limited }) : null,
  ].filter((part): part is string => part !== null)

  return (
    <span className={styles.logins} data-missing={missing}>
      {missing ? <AlertIcon size={12} /> : <LockIcon size={11} />}
      <span>{t('identity.logins.inRotation', { count: logins.rotating })}</span>
      {parts.length > 0 ? (
        <span className="u-muted">{t('identity.logins.breakdown', { parts: parts.join(' · ') })}</span>
      ) : null}
      {missing ? (
        <span title={t('identity.logins.noneHint')}>
          {t('identity.logins.none', { endpoints: needed.join(', ') })}
        </span>
      ) : null}
      {missing && canManage ? (
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            onImport(row.platform)
          }}
        >
          {t('identity.logins.import')}
        </Button>
      ) : null}
    </span>
  )
}

/**
 * What the refill job has been doing for this platform.
 *
 * Its own line per platform rather than one strip for the card. The strip
 * mixed every platform's attempts into one row of bars and showed one backoff
 * for all of them, when the worker rests the browser and plain HTTP
 * separately - so a down browser read as Bilibili failing too.
 *
 * The attempts come from the worker through Redis, because a failed mint writes
 * no identity row. That asymmetry is the whole reason this exists: a success is
 * already visible as a number going up, and a failure used to be visible only
 * in `docker compose logs`.
 */
function PlatformActivity({
  activity,
  platform,
  canMint,
}: {
  activity: MintActivity
  platform: Platform
  canMint: boolean
}) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const recent = activity.recent.filter((entry) => entry.platform === platform)
  const current = activity.current?.platform === platform ? activity.current : null
  const backoff = activity.backoffs?.[platform] ?? null
  if (!canMint || (recent.length === 0 && !current && !backoff)) return null

  const failures = recent.filter((entry) => !entry.ok).length

  return (
    <span className={styles.mintRow}>
      {current ? (
        <span className={styles.minting}>
          {/* A pulsing dot rather than a spinner: the line is one row tall and
              this is the only thing on the page that has to read as "right
              now" rather than "recently". */}
          <span className={styles.pulse} aria-hidden="true" />
          {t('console:identity.mintLog.now')}
        </span>
      ) : backoff ? (
        // Louder than a failed attempt: the job is not merely failing, it has
        // stopped trying until this passes.
        <span className={styles.backoff}>
          {t('console:identity.mintLog.backoff', {
            failures: backoff.failures,
            // A clock time: the sentence says "until", and "until in 12
            // minutes" is what a relative time made of it. The backoff is
            // capped at an hour, so the day is never in doubt.
            when: format.time(backoff.until),
          })}
        </span>
      ) : null}

      {recent.length > 0 ? (
        <>
          <span className={styles.attempts}>
            {recent.map((entry, index) => (
              <span
                key={`${entry.ts}:${index}`}
                className={styles.attempt}
                data-ok={entry.ok}
                // Everything about one attempt in the tooltip: the row is a
                // shape to scan, and a run of failures sharing one reason is a
                // different story from an alternating one.
                title={[
                  entry.ok
                    ? t('console:identity.mintLog.ok')
                    : t(`console:identity.mintLog.reason.${entry.reason}`, {
                        defaultValue: entry.reason,
                      }),
                  format.dateTime(entry.ts),
                  entry.error ?? '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              />
            ))}
          </span>
          <span className="u-muted">
            {failures === 0
              ? t('console:identity.mintLog.allOk', { count: recent.length })
              : t('console:identity.mintLog.someFailed', {
                  count: recent.length,
                  failures,
                })}
          </span>
        </>
      ) : null}
    </span>
  )
}
