/**
 * Locale-aware formatting, built on Intl.
 *
 * The count-suffix split is a formatter concern, not a translated string:
 * English scales by thousands (K/M/B) and Chinese by ten-thousands, and CLDR
 * already knows both. Asking Intl for compact notation gets that for free and
 * keeps the suffixes - and therefore CJK - out of the sources (docs/design/14).
 *
 * Missing values render as an em dash. None and zero are different facts here
 * (docs/design/11-data-contracts.md) and must not look the same.
 */

import { currentLanguage, INTL_LOCALE, type Language } from './language'

export const MISSING = '—'

export type DateInput = string | number | Date | null | undefined

export interface TimeZoneInfo {
  /** IANA name, e.g. Asia/Shanghai. Shown on chart axes so UTC storage and local display are both explicit. */
  iana: string
  /** Short label, e.g. GMT+8. */
  short: string
}

export interface Formatters {
  readonly language: Language
  readonly locale: string
  number: (value: number | null | undefined, fractionDigits?: number) => string
  compact: (value: number | null | undefined) => string
  percent: (ratio: number | null | undefined, fractionDigits?: number) => string
  bytes: (value: number | null | undefined) => string
  date: (value: DateInput) => string
  time: (value: DateInput) => string
  dateTime: (value: DateInput) => string
  /** Full precision, for log rows and audit trails. Render in a mono face. */
  timestamp: (value: DateInput) => string
  relative: (value: DateInput, now?: Date) => string
  duration: (milliseconds: number | null | undefined) => string
  latency: (milliseconds: number | null | undefined) => string
  timeZone: () => TimeZoneInfo
}

function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined) return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const RELATIVE_UNITS: ReadonlyArray<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['week', 7 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

const BYTE_UNITS: ReadonlyArray<[string, number]> = [
  ['terabyte', 1024 ** 4],
  ['gigabyte', 1024 ** 3],
  ['megabyte', 1024 ** 2],
  ['kilobyte', 1024],
  ['byte', 1],
]

function createFormatters(language: Language): Formatters {
  const locale = INTL_LOCALE[language]

  const plain = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 })
  // Chinese scales by 10^4, English by 10^3. Two fraction digits keep 12800
  // readable at the ten-thousand scale instead of rounding it to one digit.
  const compact = new Intl.NumberFormat(locale, {
    notation: 'compact',
    compactDisplay: 'short',
    maximumFractionDigits: language === 'zh' ? 2 : 1,
  })
  const dateOnly = new Intl.DateTimeFormat(locale, { dateStyle: 'medium' })
  const timeOnly = new Intl.DateTimeFormat(locale, { timeStyle: 'medium' })
  const dateAndTime = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' })
  const fullTimestamp = new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })

  const unit = (value: number, unitName: string, fractionDigits = 0): string =>
    new Intl.NumberFormat(locale, {
      style: 'unit',
      unit: unitName,
      unitDisplay: 'narrow',
      maximumFractionDigits: fractionDigits,
    }).format(value)

  return {
    language,
    locale,

    number(value, fractionDigits) {
      if (value === null || value === undefined || !Number.isFinite(value)) return MISSING
      if (fractionDigits === undefined) return plain.format(value)
      return new Intl.NumberFormat(locale, {
        minimumFractionDigits: fractionDigits,
        maximumFractionDigits: fractionDigits,
      }).format(value)
    },

    compact(value) {
      if (value === null || value === undefined || !Number.isFinite(value)) return MISSING
      return compact.format(value)
    },

    percent(ratio, fractionDigits = 1) {
      if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return MISSING
      return new Intl.NumberFormat(locale, {
        style: 'percent',
        minimumFractionDigits: fractionDigits,
        maximumFractionDigits: fractionDigits,
      }).format(ratio)
    },

    bytes(value) {
      if (value === null || value === undefined || !Number.isFinite(value)) return MISSING
      const magnitude = Math.abs(value)
      const match = BYTE_UNITS.find(([, size]) => magnitude >= size) ?? BYTE_UNITS[BYTE_UNITS.length - 1]
      if (!match) return MISSING
      const [unitName, size] = match
      const scaled = value / size
      return unit(scaled, unitName, unitName === 'byte' ? 0 : scaled < 10 ? 1 : 0)
    },

    date(value) {
      const date = toDate(value)
      return date ? dateOnly.format(date) : MISSING
    },

    time(value) {
      const date = toDate(value)
      return date ? timeOnly.format(date) : MISSING
    },

    dateTime(value) {
      const date = toDate(value)
      return date ? dateAndTime.format(date) : MISSING
    },

    timestamp(value) {
      const date = toDate(value)
      return date ? fullTimestamp.format(date) : MISSING
    },

    relative(value, now) {
      const date = toDate(value)
      if (!date) return MISSING
      const deltaSeconds = (date.getTime() - (now ?? new Date()).getTime()) / 1000
      const magnitude = Math.abs(deltaSeconds)
      for (const [unitName, seconds] of RELATIVE_UNITS) {
        if (magnitude >= seconds || unitName === 'second') {
          return relative.format(Math.round(deltaSeconds / seconds), unitName)
        }
      }
      return relative.format(0, 'second')
    },

    duration(milliseconds) {
      if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
        return MISSING
      }
      const ms = Math.max(0, milliseconds)
      if (ms < 1000) return unit(Math.round(ms), 'millisecond')

      const totalSeconds = ms / 1000
      if (totalSeconds < 60) return unit(totalSeconds, 'second', totalSeconds < 10 ? 1 : 0)

      const minutes = Math.floor(totalSeconds / 60)
      if (minutes < 60) {
        const seconds = Math.round(totalSeconds % 60)
        return seconds === 0
          ? unit(minutes, 'minute')
          : `${unit(minutes, 'minute')} ${unit(seconds, 'second')}`
      }

      const hours = Math.floor(minutes / 60)
      if (hours < 24) {
        const remainder = minutes % 60
        return remainder === 0
          ? unit(hours, 'hour')
          : `${unit(hours, 'hour')} ${unit(remainder, 'minute')}`
      }

      const days = Math.floor(hours / 24)
      const remainderHours = hours % 24
      return remainderHours === 0
        ? unit(days, 'day')
        : `${unit(days, 'day')} ${unit(remainderHours, 'hour')}`
    },

    latency(milliseconds) {
      if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
        return MISSING
      }
      return unit(Math.round(milliseconds), 'millisecond')
    },

    timeZone() {
      const resolved = new Intl.DateTimeFormat(locale).resolvedOptions()
      const parts = new Intl.DateTimeFormat(locale, { timeZoneName: 'short' }).formatToParts(
        new Date(),
      )
      const short = parts.find((part) => part.type === 'timeZoneName')?.value ?? resolved.timeZone
      return { iana: resolved.timeZone, short }
    },
  }
}

const cache = new Map<Language, Formatters>()

/** Memoised formatter bundle. Intl constructors are expensive; tables call these per cell. */
export function formatters(language: Language = currentLanguage()): Formatters {
  const cached = cache.get(language)
  if (cached) return cached
  const created = createFormatters(language)
  cache.set(language, created)
  return created
}
