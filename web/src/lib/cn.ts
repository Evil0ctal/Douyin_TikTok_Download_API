/**
 * Joins class names, keeping only non-empty strings.
 *
 * Accepts anything so that `cond && styles.x` guards read naturally at call
 * sites, including when `cond` is a ReactNode rather than a boolean.
 */
export function cn(...parts: unknown[]): string {
  return parts.filter((part): part is string => typeof part === 'string' && part.length > 0).join(' ')
}
