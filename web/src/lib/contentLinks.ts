/**
 * Links between the content pages: Downloads, Library and Transcriptions.
 *
 * The three pages describe the same post three ways - the file kept, the post
 * seen, the text made from it - and each used to know only its own: a drawer
 * showed a download id as copyable text and left the reader to find the page
 * it belonged to. Every page reads its filters and its open drawer from the
 * URL, so these are plain links that also work pasted into a new tab.
 *
 * The shapes here are a contract other pages link to (C4 in the console UX
 * notes); change one and grep for its callers.
 */

import type { Platform } from './types'

/** Opens that download's drawer. */
export function downloadHref(downloadId: string): string {
  return `/downloads?id=${encodeURIComponent(downloadId)}`
}

/** Opens that transcription's drawer. */
export function transcriptHref(transcriptionId: string): string {
  return `/transcriptions?id=${encodeURIComponent(transcriptionId)}`
}

/** Opens that post's drawer in the library. Both halves: neither is unique alone. */
export function libraryPostHref(platform: string, contentId: string): string {
  const query = new URLSearchParams({ platform, content_id: contentId })
  return `/library?${query.toString()}`
}

/** The library narrowed to one author, by the platform's stable id. */
export function libraryAuthorHref(authorUid: string): string {
  return `/library?${new URLSearchParams({ author: authorUid }).toString()}`
}

/** The library narrowed to posts carrying one tag. */
export function libraryTagHref(tag: string): string {
  return `/library?${new URLSearchParams({ tag }).toString()}`
}

/**
 * The identities page, narrowed to signed-in identities of one platform (C3).
 * Where to send someone whose request needed a logged-in cookie.
 */
export function loginIdentitiesHref(platform: Platform | string): string {
  return `/identities?${new URLSearchParams({ platform, login: 'yes' }).toString()}`
}

/**
 * A copy of `params` with `patch` applied: a value sets, null or '' removes.
 *
 * For wouter's `setSearchParams`, which takes the whole next query. Writing
 * the patch out each time is how a filter change used to drop the drawer's id
 * - or keep it, pointing at a row the new filter no longer shows.
 */
export function patchParams(
  params: URLSearchParams,
  patch: Readonly<Record<string, string | null | undefined>>,
): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || value === '') next.delete(key)
    else next.set(key, value)
  }
  return next
}

/** Whether a URL value names one of the platforms this build knows. */
export function asPlatform(value: string | null, known: readonly Platform[]): Platform | '' {
  return value !== null && (known as readonly string[]).includes(value) ? (value as Platform) : ''
}
