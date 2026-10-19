import { useTranslation } from 'react-i18next'

import { Button, CompactProgress } from '@/components'
import { usePlatformInSentence } from '@/hooks'
import { apiRequest, errorCodeOf, isApiError, type QueryParams } from '@/lib/api'
import { loginIdentitiesHref } from '@/lib/contentLinks'
import type { Platform } from '@/lib/types'

import styles from './Downloads.module.css'

/**
 * The two Downloads modes that walk a list: an author's posts, a post's comments.
 *
 * Both used to be one spinner over a loop that threw on the first failure.
 * Two things went wrong with that, and they are why this is a module now.
 *
 * **A failure late in the walk discarded everything before it.** Douyin pages
 * an author's posts past the first only for a signed-in identity, and so does
 * Bilibili for comments and replies: page two answers 503
 * IDENTITY_LOGIN_REQUIRED. The loop threw, the posts from page one were thrown
 * away with it, and nothing was queued - for an author whose first twenty
 * posts had been read successfully. A walk now ends where it can, keeps what
 * it has, and says why it stopped.
 *
 * **Two hundred requests behind one spinner.** Every post queued is a request
 * and the operator could not see how far along it was, stop it, or learn that
 * five of them had failed - the failures were swallowed. The run reports each
 * step as it happens and takes a Stop.
 */

/** One page of a platform list, as the read endpoints return it. */
export interface ListPage<T> {
  items: T[]
  cursor: string | null
  has_more: boolean
}

/** Why a walk ended before the list did. */
export type WalkStop =
  | { reason: 'login'; page: number }
  | { reason: 'error'; page: number; message: string }
  | { reason: 'stopped' }

/** Set by the Stop button; checked between requests, never in the middle of one. */
export interface StopFlag {
  stopped: boolean
}

export interface Walked<T> {
  items: T[]
  /** Any page was answered by a guest identity, which may have been given less. */
  guest: boolean
  stop: WalkStop | null
}

function messageOf(error: unknown): string {
  if (isApiError(error)) return error.message || error.code
  return error instanceof Error ? error.message : String(error)
}

/**
 * Read one page, and whether a guest answered it.
 *
 * The flag is in the envelope's `meta` when the read finished inside the
 * request, and in the task's `result_meta` when the client had to poll for
 * it - the same report, in whichever place the answer came back through.
 */
export async function readPage<T>(
  path: string,
  params: QueryParams,
): Promise<{ page: ListPage<T>; guest: boolean }> {
  const response = await apiRequest<ListPage<T>>(path, { params })
  const guest =
    response.meta['guest_fallback'] === true || response.resultMeta?.['guest_fallback'] === true
  return { page: response.data, guest }
}

/**
 * Walk a list page by page until it has `want` items, runs out, or cannot go on.
 *
 * A first page that fails is the run failing - there is nothing to keep, so
 * that one throws. A later one ends the walk with what was read so far and a
 * reason, because the reason is usually that the rest needs a signed-in
 * identity, and the pages already read are no less real for it.
 */
export async function walkPages<T>({
  path,
  params,
  want,
  maxPages,
  flag,
  onPage,
}: {
  path: string
  params: QueryParams
  want: number
  maxPages: number
  flag: StopFlag
  onPage: (page: number, found: number) => void
}): Promise<Walked<T>> {
  const items: T[] = []
  let guest = false
  let cursor: string | null = null
  for (let page = 1; page <= maxPages && items.length < want; page += 1) {
    if (flag.stopped) return { items, guest, stop: { reason: 'stopped' } }
    onPage(page, items.length)
    let read: { page: ListPage<T>; guest: boolean }
    try {
      read = await readPage<T>(path, { ...params, ...(cursor ? { cursor } : {}) })
    } catch (error) {
      if (page === 1) throw error
      const stop: WalkStop =
        errorCodeOf(error) === 'IDENTITY_LOGIN_REQUIRED'
          ? { reason: 'login', page }
          : { reason: 'error', page, message: messageOf(error) }
      return { items, guest, stop }
    }
    items.push(...read.page.items)
    guest = guest || read.guest
    if (!read.page.has_more || !read.page.cursor) break
    cursor = read.page.cursor
  }
  return { items: items.slice(0, want), guest, stop: null }
}

/** What happened to each item a run was asked to act on. */
export interface Tally {
  done: number
  skipped: number
  failed: number
  /** The first failure's message, so "2 failed" can say what failed. */
  firstError: string | null
}

export const NO_TALLY: Tally = { done: 0, skipped: 0, failed: 0, firstError: null }

/**
 * Act on each item in turn, counting what happened.
 *
 * Sequential on purpose: each call spends the identity pool or a queue slot,
 * and a burst of two hundred is what the scheduler would then have to undo.
 * One failure does not stop the rest - one post with nothing fetchable says
 * nothing about the next - but it is counted, and the first reason kept.
 */
export async function eachInTurn<T>(
  items: readonly T[],
  call: (item: T) => Promise<'done' | 'skipped'>,
  flag: StopFlag,
  onProgress: (tally: Tally) => void,
): Promise<{ tally: Tally; stopped: boolean }> {
  let tally = NO_TALLY
  for (const item of items) {
    if (flag.stopped) return { tally, stopped: true }
    try {
      const outcome = await call(item)
      tally =
        outcome === 'skipped'
          ? { ...tally, skipped: tally.skipped + 1 }
          : { ...tally, done: tally.done + 1 }
    } catch (error) {
      tally = { ...tally, failed: tally.failed + 1, firstError: tally.firstError ?? messageOf(error) }
    }
    onProgress(tally)
  }
  return { tally, stopped: false }
}

/** A walk in progress or finished, as the page shows it. */
export interface RunState {
  kind: 'author' | 'comments'
  platform: Platform
  phase: 'reading' | 'queueing' | 'finished'
  /** The page being read, while reading. */
  page: number
  /** Items the walk has found so far. */
  found: number
  /** Items being acted on, once reading is over. */
  total: number
  tally: Tally
  guest: boolean
  stop: WalkStop | null
  /** The run failed outright on its first page because it needed a login. */
  needsLogin?: boolean
}

export interface RunReportProps {
  run: RunState
  onStop: () => void
  onDismiss: () => void
  stopping: boolean
}

/**
 * The way to add a signed-in identity, in a new tab.
 *
 * Not an in-app link: leaving Downloads stops the run, and this note shows
 * while the posts the first pages gave are still being queued.
 */
function SignInLink({ platform }: { platform: Platform }) {
  const { t } = useTranslation(['console', 'common'])
  return (
    <a href={loginIdentitiesHref(platform)} target="_blank" rel="noreferrer noopener">
      {t('downloads.run.signIn')}
    </a>
  )
}

/** Which guest note fits: the two endpoints where a guest measurably gets less. */
function guestKey(run: RunState): string {
  if (run.kind === 'comments' && run.platform === 'bilibili') return 'bilibiliComments'
  if (run.kind === 'author' && run.platform === 'douyin') return 'douyinAuthor'
  return 'generic'
}

/**
 * The live account of a walk: where it is, what it has done, and why it ended.
 *
 * Kept on screen after it finishes rather than collapsed into a toast, because
 * the parts worth reading - "2 failed", "page 2 needs a signed-in identity" -
 * are exactly the parts a four-second toast took away.
 */
export function RunReport({ run, onStop, onDismiss, stopping }: RunReportProps) {
  const { t } = useTranslation(['console', 'common'])
  const platformPhrase = usePlatformInSentence()
  const live = run.phase !== 'finished'
  const acted = run.tally.done + run.tally.skipped + run.tally.failed

  const status =
    run.phase === 'reading'
      ? t('downloads.run.reading', { page: run.page, found: run.found })
      : run.kind === 'comments'
        ? t('downloads.run.commentsSaved', { count: run.found })
        : t('downloads.run.queued', {
            done: run.tally.done,
            total: run.total,
            failed: run.tally.failed,
            skipped: run.tally.skipped,
          })

  return (
    <div className={styles.run} role="status" aria-live="polite">
      <div className="u-row-between u-wrap">
        {run.phase === 'queueing' ? (
          <CompactProgress value={acted} max={run.total} label={status} />
        ) : (
          <span className="u-xs">{status}</span>
        )}
        {live ? (
          <Button size="sm" variant="secondary" loading={stopping} onClick={onStop}>
            {t('downloads.run.stop')}
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            {t('downloads.run.dismiss')}
          </Button>
        )}
      </div>

      {run.tally.firstError ? (
        <p className={styles.runNote}>
          {t('downloads.run.firstError', { message: run.tally.firstError })}
        </p>
      ) : null}

      {run.stop?.reason === 'stopped' ? (
        <p className={styles.runNote}>{t('downloads.run.stopped')}</p>
      ) : null}
      {run.stop?.reason === 'error' ? (
        <p className={styles.runNote}>
          {t('downloads.run.errorStop', { page: run.stop.page, message: run.stop.message })}
        </p>
      ) : null}
      {run.stop?.reason === 'login' || run.needsLogin ? (
        <p className={styles.runNote}>
          {run.needsLogin
            ? t('downloads.run.loginFirst', { platform: platformPhrase(run.platform) })
            : t(`downloads.run.loginStop.${run.kind}`, {
                page: run.stop?.reason === 'login' ? run.stop.page : 2,
                platform: platformPhrase(run.platform),
              })}{' '}
          <SignInLink platform={run.platform} />
        </p>
      ) : null}

      {/* Only when nothing above already said a signed-in identity is the fix:
          two notes and two links for one cause is noise. */}
      {run.guest && run.stop?.reason !== 'login' && !run.needsLogin ? (
        <p className={styles.runNote}>
          {t(`downloads.run.guest.${guestKey(run)}`)} <SignInLink platform={run.platform} />
        </p>
      ) : null}
    </div>
  )
}
