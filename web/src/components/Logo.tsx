export interface LogoProps {
  size?: number
  className?: string
}

/**
 * The mark, carried over from the 2022 logo.
 *
 * That one was a pair of curly braces around two clusters of dots - an API
 * that returns objects, said in one glyph - and the braces are the part people
 * recognise. They stay. What sits between them does not: the dot clusters were
 * five and four dots at 500px, and at the 22px this renders in the sidebar they
 * collapse into a smudge. A downward arrow survives the size and says the other
 * half of what this is, which the dots never did.
 *
 * Drawn in `currentColor` on purpose. The brand mark is the one place a fixed
 * colour is most tempting and least right: it sits on the accent in the
 * sidebar, on the page background in the empty states, and would need a third
 * value the moment somebody puts it on a toast.
 */
export function Logo({ size = 24, className }: LogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {/* The braces. Drawn as two symmetric curves rather than a font glyph so
          they keep their weight next to the arrow at any size. */}
      <path d="M9.2 3.8c-2 0-2 1.9-2 3.6 0 1.8-1.9 2.9-2.9 3.3v.6c1 .4 2.9 1.5 2.9 3.3 0 1.7 0 3.6 2 3.6" />
      <path d="M14.8 3.8c2 0 2 1.9 2 3.6 0 1.8 1.9 2.9 2.9 3.3v.6c-1 .4-2.9 1.5-2.9 3.3 0 1.7 0 3.6-2 3.6" />
      {/* What the braces are holding: something on its way down. */}
      <path d="M12 7.6v6.1" />
      <path d="M9.7 11.5 12 13.8l2.3-2.3" />
      <path d="M9.9 16.6h4.2" />
    </svg>
  )
}
