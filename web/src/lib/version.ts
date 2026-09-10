/**
 * Comparing this instance's version with the newest published release.
 *
 * Lives here rather than in the page that checks, because two surfaces ask the
 * question now - the System page on a click, and the notice that appears after
 * signing in - and two implementations of "is that newer" would eventually
 * disagree about the same pair of strings.
 */

/** A version split into numbers and whatever pre-release suffix followed. */
interface Parsed {
  parts: number[]
  /** `dev0`, `rc1`, … Empty for a final release. */
  pre: string
}

/**
 * Accepts what the project actually produces: `v5.0.0`, `5.0.0`,
 * `5.0.0.dev0`, `5.1.0rc1`. Anything unparseable comes back as all zeroes,
 * which makes it compare as older than everything rather than throwing on a
 * page that is only trying to be helpful.
 */
export function parseVersion(raw: string | null | undefined): Parsed {
  const text = (raw ?? '').trim().replace(/^v/i, '')
  const match = /^(\d+(?:\.\d+)*)(.*)$/.exec(text)
  const numbers = match?.[1]
  if (!numbers) return { parts: [0], pre: '' }
  return {
    parts: numbers.split('.').map((n) => Number.parseInt(n, 10) || 0),
    pre: (match?.[2] ?? '').replace(/^[.\-_]/, '').trim(),
  }
}

function compareParts(a: number[], b: number[]): number {
  const width = Math.max(a.length, b.length)
  for (let i = 0; i < width; i += 1) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0)
    if (diff !== 0) return diff < 0 ? -1 : 1
  }
  return 0
}

/**
 * Whether `candidate` is a release worth telling the operator about.
 *
 * Two rules beyond the numbers, and both exist because the alternative is a
 * console that cries wolf:
 *
 * * A pre-release suffix makes a version *older* than the same numbers without
 *   one, so `5.0.0.dev0` is behind `5.0.0` and the notice appears. Running the
 *   tagged `5.0.0` against a latest of `5.0.0` is silence, which is the whole
 *   point.
 * * Equal numbers with equal suffixes is not newer. This used to be an equality
 *   check that treated *anything* different as newer, so an instance built from
 *   `main` at `5.1.0.dev0` was told to upgrade to the older `5.0.0`.
 */
export function isNewerRelease(
  candidate: string | null | undefined,
  current: string | null | undefined,
): boolean {
  if (!candidate || !current) return false
  const a = parseVersion(candidate)
  const b = parseVersion(current)
  const byNumber = compareParts(a.parts, b.parts)
  if (byNumber !== 0) return byNumber > 0
  // Same numbers: a final release beats a pre-release of those numbers.
  if (a.pre === b.pre) return false
  if (a.pre === '') return true
  if (b.pre === '') return false
  // Two different pre-releases of the same version: compare as text, which
  // orders dev0 < dev1 < rc1 well enough for a notice.
  return a.pre > b.pre
}
