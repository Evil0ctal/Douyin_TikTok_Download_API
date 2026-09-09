/**
 * Inline icon set.
 *
 * Icons are structural here, not decoration: every status shows colour + icon +
 * text so the meaning survives a screenshot, a colour-blind reader and a copy
 * into a chat window (docs/design/12-design-system.md).
 *
 * All icons inherit currentColor and size from the font, so they line up with
 * the label beside them without per-call-site tuning.
 */

import type { ReactElement, SVGProps } from 'react'

import type { StatusIconName } from '@/lib/status'

export type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Svg({ size = 14, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  )
}

/* Status glyphs -------------------------------------------------------------- */

export function DotIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="3.5" fill="currentColor" stroke="none" />
    </Svg>
  )
}

/** Half-filled dot: cooling down, partially available. */
export function HalfIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="3.75" />
      <path d="M8 4.25a3.75 3.75 0 0 1 0 7.5z" fill="currentColor" stroke="none" />
    </Svg>
  )
}

export function AlertIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.6 14.2 13H1.8z" />
      <path d="M8 6.6v3" />
      <path d="M8 11.4h.01" />
    </Svg>
  )
}

export function CrossIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Svg>
  )
}

export function CheckIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 8.5 6.5 12 13 4.5" />
    </Svg>
  )
}

export function ClockIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M8 4.75V8l2.25 1.5" />
    </Svg>
  )
}

export function MinusIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3.5 8h9" />
    </Svg>
  )
}

export function PauseIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 3.5v9M10 3.5v9" />
    </Svg>
  )
}

export function SpinnerIcon({ size = 14, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
      style={{ animation: 'dtk-spin 900ms linear infinite' }}
      {...rest}
    >
      <circle cx="8" cy="8" r="5.75" opacity="0.25" />
      <path d="M8 2.25A5.75 5.75 0 0 1 13.75 8" />
    </svg>
  )
}

export const STATUS_ICONS: Record<StatusIconName, (props: IconProps) => ReactElement> = {
  dot: DotIcon,
  half: HalfIcon,
  alert: AlertIcon,
  cross: CrossIcon,
  check: CheckIcon,
  clock: ClockIcon,
  spinner: SpinnerIcon,
  minus: MinusIcon,
  pause: PauseIcon,
}

/* Interface icons ------------------------------------------------------------ */

export function ChevronDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 6.5 8 10.5l4-4" />
    </Svg>
  )
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6.5 4 10.5 8l-4 4" />
    </Svg>
  )
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M9.5 4 5.5 8l4 4" />
    </Svg>
  )
}

export function ArrowUpIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 12.5v-9M4.5 7 8 3.5 11.5 7" />
    </Svg>
  )
}

export function ArrowDownIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 3.5v9M4.5 9 8 12.5 11.5 9" />
    </Svg>
  )
}

export function SortIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 6.5 8 3.5l3 3" />
      <path d="M5 9.5 8 12.5l3-3" />
    </Svg>
  )
}

export function CopyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5v-1a1.5 1.5 0 0 0-1.5-1.5H4a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 4 11h1" />
    </Svg>
  )
}

export function MenuIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
    </Svg>
  )
}

export function ColumnsIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.5" y="3" width="11" height="10" rx="1.5" />
      <path d="M6.2 3v10M9.8 3v10" />
    </Svg>
  )
}

export function DensityIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
    </Svg>
  )
}

export function SearchIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="7" cy="7" r="4.25" />
      <path d="M10.2 10.2 13.5 13.5" />
    </Svg>
  )
}

export function SunIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5 3.4 3.4" />
    </Svg>
  )
}

export function MoonIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" />
    </Svg>
  )
}

export function MonitorIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2" y="3" width="12" height="8" rx="1.5" />
      <path d="M6 13.5h4" />
    </Svg>
  )
}

export function GlobeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="5.75" />
      <path d="M2.4 6.4h11.2M2.4 9.6h11.2" />
      <path d="M8 2.25c1.6 1.7 2.4 3.6 2.4 5.75S9.6 12.05 8 13.75C6.4 12.05 5.6 10.15 5.6 8s.8-4.05 2.4-5.75z" />
    </Svg>
  )
}

export function LockIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3.5" y="7" width="9" height="6" rx="1.5" />
      <path d="M5.75 7V5.25a2.25 2.25 0 0 1 4.5 0V7" />
    </Svg>
  )
}

export function ExternalIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M9 3.5h3.5V7" />
      <path d="M12.5 3.5 7.5 8.5" />
      <path d="M12 9.5v2a1.5 1.5 0 0 1-1.5 1.5h-6A1.5 1.5 0 0 1 3 11.5v-6A1.5 1.5 0 0 1 4.5 4h2" />
    </Svg>
  )
}

export function RefreshIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 8a5 5 0 1 1-1.6-3.7" />
      <path d="M13.2 2.5V5h-2.5" />
    </Svg>
  )
}

export function LogoutIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6.5 13.5H4A1.5 1.5 0 0 1 2.5 12V4A1.5 1.5 0 0 1 4 2.5h2.5" />
      <path d="M10 11l3-3-3-3" />
      <path d="M13 8H6" />
    </Svg>
  )
}

export function GaugeIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 11.5a5.5 5.5 0 1 1 11 0" />
      <path d="M8 11.5 10.5 7" />
    </Svg>
  )
}

export function IdCardIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <circle cx="5.75" cy="7.5" r="1.4" />
      <path d="M3.6 10.6c.4-.9 1.2-1.4 2.15-1.4s1.75.5 2.15 1.4" />
      <path d="M9.75 6.75h2.75M9.75 9.25h2.75" />
    </Svg>
  )
}

export function UsersIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="6.25" cy="6" r="2.25" />
      <path d="M2.5 12.75c.5-2 1.9-3 3.75-3s3.25 1 3.75 3" />
      <path d="M10.5 4.1a2.1 2.1 0 0 1 0 4.05" />
      <path d="M11.5 9.9c1.15.35 1.85 1.2 2.15 2.6" />
    </Svg>
  )
}

export function KeyIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="5.25" cy="10.75" r="2.5" />
      <path d="M7 9 13 3" />
      <path d="M11 5l1.5 1.5M9.5 6.5 11 8" />
    </Svg>
  )
}

export function TerminalIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M4.75 6.5 6.75 8l-2 1.5" />
      <path d="M8.5 10h3" />
    </Svg>
  )
}

export function LinkIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6.75 9.25a2.5 2.5 0 0 0 3.5 0l1.75-1.75a2.475 2.475 0 0 0-3.5-3.5L7.6 4.9" />
      <path d="M9.25 6.75a2.5 2.5 0 0 0-3.5 0L4 8.5a2.475 2.475 0 0 0 3.5 3.5l.9-.9" />
    </Svg>
  )
}

export function ListIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5.5 4.5h8M5.5 8h8M5.5 11.5h8" />
      <path d="M2.75 4.5h.01M2.75 8h.01M2.75 11.5h.01" />
    </Svg>
  )
}

export function SlidersIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2.5 5h4M9.5 5h4M2.5 11h4M9.5 11h4" />
      <circle cx="7.75" cy="5" r="1.5" />
      <circle cx="8.25" cy="11" r="1.5" />
    </Svg>
  )
}

export function BellIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 11V7.25a4 4 0 0 1 8 0V11l1 1.25H3z" />
      <path d="M6.75 13.25a1.4 1.4 0 0 0 2.5 0" />
    </Svg>
  )
}

export function ServerIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2.5" y="2.75" width="11" height="4.5" rx="1.25" />
      <rect x="2.5" y="8.75" width="11" height="4.5" rx="1.25" />
      <path d="M5 5h.01M5 11h.01" />
    </Svg>
  )
}

export function ActivityIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M1.75 8h2.9l1.6-4.5 2.6 9L10.6 8h3.65" />
    </Svg>
  )
}

export function ArchiveIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="2" y="3" width="12" height="3" rx="1" />
      <path d="M3.25 6.5v5.75c0 .7.55 1.25 1.25 1.25h7c.7 0 1.25-.55 1.25-1.25V6.5" />
      <path d="M6.5 9h3" />
    </Svg>
  )
}

export function DownloadIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2.5v7.5" />
      <path d="M5 7.25 8 10.25l3-3" />
      <path d="M3 12.5h10" />
    </Svg>
  )
}

/** Filled, unlike its neighbours: a hollow triangle at 10px reads as a chevron. */
export function PlayIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 3.5v9l7.5-4.5z" fill="currentColor" />
    </Svg>
  )
}

export function InfoIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.25v4" />
      <path d="M8 4.9h.01" />
    </Svg>
  )
}

export function BookIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 3.5h4.25c.7 0 1.25.55 1.25 1.25v8c0-.7-.55-1.25-1.25-1.25H3z" />
      <path d="M13 3.5H8.75c-.7 0-1.25.55-1.25 1.25v8c0-.7.55-1.25 1.25-1.25H13z" />
    </Svg>
  )
}
