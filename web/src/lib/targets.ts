/**
 * Reading what someone pasted as a post or an author: a link, or a bare id.
 *
 * Shared by Downloads and the Watchlist, which take the same two inputs and
 * used to disagree about them - the watchlist took a raw id only, so the link
 * the share sheet hands you had to be taken apart by hand first.
 *
 * A link is never fetched from here. It goes to /tools/parse-url, which runs
 * the same host allowlist as every other endpoint, so an unrecognised host
 * never becomes a request; a short link goes on to /parse, whose task follows
 * it through an identity.
 */

import { apiGet, apiPost } from './api'
import { API_V1, paths } from './endpoints'
import type { Platform } from './types'

/**
 * A bare author id, as Douyin and TikTok spell it.
 *
 * Douyin's `sec_user_id` and TikTok's `secUid` are the same base64-ish shape
 * and both start `MS4wLjABAAAA`. Matching the prefix rather than the whole
 * string keeps this a recognition test and not a validator - the platform
 * decides whether the id exists, and it says so plainly when asked.
 */
export const SEC_UID = /^MS4wLjABAAAA[\w-]+$/

/**
 * A bare Bilibili video id: `BV1` and nine base-58 characters, or an av number.
 *
 * Either one names its platform by its shape, which a run of digits does not.
 * The box used to send anything that was not digits to the link parser, which
 * has no link to parse in `BV1xx411c7mD` and called it unrecognised - while
 * the API it would have gone to takes both spellings as a content id.
 */
export const BVID = /^BV1[1-9A-HJ-NP-Za-km-z]{9}$/
export const AVID = /^av\d{1,16}$/i
export const DIGITS = /^\d+$/

export interface ParsedLink {
  allowed: boolean
  platform: string | null
  resource: string
  resource_id: string | null
  /** Canonical web URL, or null when the input is not an allowed target. */
  url?: string | null
  /** A short link: it names nothing until it is followed. */
  needs_expansion?: boolean
}

/** Why a link could not be read as the thing that was asked for. */
export type TargetProblem = 'unrecognised' | 'shortLink' | 'expectedVideo' | 'expectedUser'

export class TargetError extends Error {
  readonly problem: TargetProblem

  constructor(problem: TargetProblem) {
    super(problem)
    this.problem = problem
  }
}

/**
 * The post a short link turns out to be.
 *
 * A short link names nothing until it is followed, and following it is a
 * request that has to leave through an identity like any other - the parse
 * task's job, not this module's. So the link is parsed and the post read off
 * the answer: the two steps the batch tab takes for a pasted share text. The
 * single-post form skipped them and handed the share text straight to the
 * download endpoint, which refuses a short link, and the reader got a bare
 * INVALID_PARAM from a form whose hint invites share text (issue 823).
 *
 * A profile's short link parses to an author, which is not a post.
 */
export async function expandPostLink(text: string): Promise<{ platform: Platform; id: string }> {
  const content = await apiPost<{ platform?: string | null; content_id?: unknown }>(paths.parse, {
    url: text,
    include_raw: false,
  })
  if (!content?.platform || typeof content.content_id !== 'string') {
    throw new TargetError('expectedVideo')
  }
  return { platform: content.platform as Platform, id: content.content_id }
}

/**
 * Turn a pasted link into a platform and the id the API keys on.
 *
 * One request beyond the parser, and only for a TikTok profile: its link names
 * the @handle, while the post list and the watchlist take only the `secUid`,
 * so the profile is read once to get it. The API refused the handle by name -
 * "needs the author's stable id" - which is true and no help to somebody who
 * pasted the only link the app gives them.
 */
export async function resolveLink(
  text: string,
  wanted: 'video' | 'user',
): Promise<{ platform: Platform; id: string }> {
  const kind = await apiGet<ParsedLink>(paths.tools.parseUrl, { params: { url: text } })
  if (kind.needs_expansion) {
    // Posts only. A profile's short link parses to an author, and reading the
    // id the post list and the watchlist key on off that answer is not wired
    // up, so an author link still has to be pasted in full.
    if (wanted === 'video') return expandPostLink(text)
    throw new TargetError('shortLink')
  }
  if (!kind.allowed || !kind.platform || !kind.resource_id) throw new TargetError('unrecognised')
  if (kind.resource !== wanted) {
    throw new TargetError(wanted === 'user' ? 'expectedUser' : 'expectedVideo')
  }
  const platform = kind.platform as Platform
  if (wanted === 'user' && platform === 'tiktok' && !SEC_UID.test(kind.resource_id)) {
    const profile = await apiGet<{ sec_uid?: string | null }>(`${API_V1}/tiktok/user`, {
      params: { url: kind.url ?? text },
    })
    if (!profile.sec_uid) throw new TargetError('unrecognised')
    return { platform, id: profile.sec_uid }
  }
  return { platform, id: kind.resource_id }
}
