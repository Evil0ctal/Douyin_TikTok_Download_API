/**
 * Spacing for a Latin word dropped into a Chinese sentence.
 *
 * Chinese sets a Latin word off from the characters around it with a space, a
 * Chinese word not at all, and puts no space against its own full-width
 * punctuation - so "No TikTok identity yet" wants a space on each side of the
 * name, the same sentence about Douyin wants none, and "serves: TikTok." none
 * either, beside its full-width colon and stop. A string cannot get that right
 * by itself, because which name fills `{platform}` is only known once it is
 * filled - so the strings were written with spaces or without, and each was
 * wrong for some platform. Padding the name instead was wrong beside
 * punctuation, because the name cannot see what it lands next to.
 *
 * So the word is marked when it is passed in, and the finished string is
 * spaced after interpolation, where both neighbours are known. The pass runs on
 * every translation as an i18next post-processor and touches only marked
 * words, so it never respaces a title, a nickname or anything else a string
 * carries in.
 */

/** U+2063 INVISIBLE SEPARATOR: renders as nothing should one ever escape. */
const MARK = '⁣'

const HAN = /\p{Script=Han}/u
const LATIN_START = /^[A-Za-z0-9]/
const LATIN_END = /[A-Za-z0-9]$/

/** A word to be spaced against the sentence it is interpolated into. */
export function marked(word: string): string {
  return `${MARK}${word}${MARK}`
}

/** Space every marked word against its neighbours and drop the marks. */
export function spaceMarked(text: string): string {
  if (!text.includes(MARK)) return text
  const parts = text.split(MARK)
  // An unpaired mark means the text was cut or joined somewhere it should not
  // have been. Dropping the marks is safer than guessing which word they meant.
  if (parts.length % 2 === 0) return parts.join('')
  let out = parts[0] ?? ''
  for (let index = 1; index < parts.length; index += 2) {
    const word = parts[index] ?? ''
    const after = parts[index + 1] ?? ''
    const before = out.slice(-1)
    const next = after.slice(0, 1)
    out += `${HAN.test(before) && LATIN_START.test(word) ? ' ' : ''}${word}`
    out += `${HAN.test(next) && LATIN_END.test(word) ? ' ' : ''}${after}`
  }
  return out
}

/** Registered in lib/i18n.ts and named in its `postProcess` option. */
export const SPACING_POST_PROCESSOR = {
  type: 'postProcessor' as const,
  name: 'spaceMarked',
  // A `returnObjects` lookup comes through here too, as the object it found.
  process: (value: unknown): unknown => (typeof value === 'string' ? spaceMarked(value) : value),
}
