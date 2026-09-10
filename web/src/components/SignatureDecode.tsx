import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { CodeBlock } from './CodeBlock'
import { Disclosure } from './Disclosure'
import {
  ActivityIcon,
  AlertIcon,
  CheckIcon,
  ClockIcon,
  CrossIcon,
  LockIcon,
  MinusIcon,
  SearchIcon,
} from './Icons'
import { toneClass } from './StatusBadge'
import { cn } from '@/lib/cn'
import type { Tone } from '@/lib/status'
import styles from './signing.module.css'

/**
 * A signature, read back.
 *
 * The whole design problem here is one of honesty, not layout. Three quite
 * different things arrive looking like the same table row - a value that came
 * out of the payload, a hash whose input is gone, and a token the platform
 * issued and nobody computed - and a reader who cannot tell them apart will
 * take the second for the first. So every row carries its kind, and the kinds
 * are drawn differently rather than merely labelled.
 *
 * The checks are the other half. A hash does not run backwards, but a candidate
 * can be tested against it, and that is what turns this from a curiosity into
 * the thing to reach for when a hand-built request is refused: it says whether
 * the signature you are sending was computed over the URL you are sending it
 * with. Three statuses, and the third is load-bearing - `not_supplied` is not a
 * failure, and drawing it in the same colour as `differs` would report a fault
 * every time somebody left a field blank.
 */

/** Wire values. The endpoint keeps them stable so this component can key off them. */
export type DecodedKind =
  | 'plain'
  | 'time'
  | 'digest'
  | 'checksum'
  | 'environment'
  | 'opaque'

export type CheckStatus = 'match' | 'differs' | 'not_supplied'

export interface DecodedField {
  name: string
  value: string
  kind: DecodedKind
  detail: string | null
}

export interface DecodedCheck {
  name: string
  status: CheckStatus
  bits: number
  /** Only ever present on a match: a preimage that does not verify would be derived from. */
  covered: string | null
}

export interface DecodedParameter {
  parameter: string
  platform: string | null
  algorithm: string
  recovered: boolean
  reason: string | null
  fields: readonly DecodedField[]
  checks: readonly DecodedCheck[]
  notes: readonly string[]
}

/*
 * Enum values this component has copy for. Anything else prints as itself - a
 * slug a reader can search for beats a translation key rendered where a
 * sentence belongs, and a newer API is allowed to add values.
 */
const KNOWN_KINDS = new Set<string>([
  'plain',
  'time',
  'digest',
  'checksum',
  'environment',
  'opaque',
])
const KNOWN_NOTES = new Set<string>([
  'checksum_verified',
  'checksum_disagrees',
  'mixed_verified',
  'mixed_disagrees',
  'noise_not_recoverable',
  'key_travels_in_the_envelope',
  'constant_placeholder',
  'superseded_by_a_bogus',
  'issued_by_the_platform',
])
const KNOWN_REASONS = new Set<string>([
  'one_way',
  'not_computed',
  'unknown_parameter',
])

/**
 * `digest` is the one a reader must not mistake for a recovered value, so it is
 * the one that is coloured. `plain` is deliberately neutral: the ordinary case
 * should not shout.
 */
const KIND_TONE: Record<string, Tone> = {
  plain: 'neutral',
  time: 'neutral',
  digest: 'caution',
  checksum: 'accent',
  environment: 'neutral',
  opaque: 'muted',
}

/** `not_supplied` is muted, never red: nothing is wrong because you did not send it. */
const CHECK_TONE: Record<CheckStatus, Tone> = {
  match: 'success',
  differs: 'danger',
  not_supplied: 'muted',
}

function KindIcon({ kind }: { kind: string }) {
  switch (kind) {
    case 'time':
      return <ClockIcon size={12} />
    case 'digest':
      return <LockIcon size={12} />
    case 'checksum':
      return <CheckIcon size={12} />
    case 'environment':
      return <ActivityIcon size={12} />
    case 'opaque':
      return <MinusIcon size={12} />
    default:
      return <SearchIcon size={12} />
  }
}

function CheckIconFor({ status }: { status: CheckStatus }) {
  switch (status) {
    case 'match':
      return <CheckIcon size={12} />
    case 'differs':
      return <CrossIcon size={12} />
    default:
      return <MinusIcon size={12} />
  }
}

function Tag({ tone, icon, children }: { tone: Tone; icon: ReactNode; children: ReactNode }) {
  return (
    <span className={cn(styles.tag, toneClass(tone))}>
      <span className={styles.tagIcon}>{icon}</span>
      {children}
    </span>
  )
}

/**
 * Whether the inputs you named are the ones this signature sealed.
 *
 * The bit count is shown on a match and not on the other two, because it is
 * only ever an answer to "how sure are you". On a mismatch there is nothing to
 * be sure about, and on an unsupplied input nothing was compared at all.
 */
function Checks({ checks }: { checks: readonly DecodedCheck[] }) {
  const { t } = useTranslation('console')
  if (checks.length === 0) return null

  const matched = checks.filter((check) => check.status === 'match' && check.covered)

  return (
    <div className="u-stack-sm">
      <span className={styles.sectionLabel}>{t('tools.decode.checks.title')}</span>
      <div className={styles.checkRow}>
        {checks.map((check) => (
          <Tag
            key={check.name}
            tone={CHECK_TONE[check.status] ?? 'neutral'}
            icon={<CheckIconFor status={check.status} />}
          >
            {t(`tools.decode.check.${check.name}`, { defaultValue: check.name })}
            {check.status === 'match' ? (
              <span className={styles.bits}>{t('tools.decode.checks.bits', { count: check.bits })}</span>
            ) : null}
          </Tag>
        ))}
      </div>
      {/* The covered string is the one thing on this page a reader can verify
          for themselves, so it gets room. It is published only when it
          reproduced the signature beside it. */}
      {matched.map((check) => (
        <div key={`covered-${check.name}`} className="u-stack-sm">
          <span className={styles.note}>
            {t('tools.decode.checks.covered', {
              input: t(`tools.decode.check.${check.name}`, { defaultValue: check.name }),
            })}
          </span>
          <CodeBlock code={check.covered ?? ''} language="text" />
        </div>
      ))}
    </div>
  )
}

/** What is inside. One row per value, each declaring which kind of value it is. */
function FieldTable({ fields }: { fields: readonly DecodedField[] }) {
  const { t } = useTranslation('console')
  if (fields.length === 0) return null

  return (
    <div className="u-scroll-x">
      <table className={styles.table}>
        <thead>
          <tr>
            <th>{t('tools.decode.column.field')}</th>
            <th>{t('tools.decode.column.value')}</th>
            <th>{t('tools.decode.column.kind')}</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.name}>
              <td className={styles.paramName}>
                <div className="u-stack-sm">
                  <span>{field.name}</span>
                  {field.detail ? <span className={styles.note}>{field.detail}</span> : null}
                </div>
              </td>
              <td className={styles.paramValue}>
                {field.value === '' ? (
                  <span className="u-muted">{t('common:value.none')}</span>
                ) : (
                  field.value
                )}
              </td>
              <td>
                {KNOWN_KINDS.has(field.kind) ? (
                  <Tag tone={KIND_TONE[field.kind] ?? 'neutral'} icon={<KindIcon kind={field.kind} />}>
                    {t(`tools.decode.kind.${field.kind}`)}
                  </Tag>
                ) : (
                  <code className={styles.wire}>{field.kind}</code>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Why a parameter has no plaintext under it.
 *
 * A malformed value carries the structural test it failed after a colon, which
 * is worth showing verbatim: "header magic" and "checksum" mean quite different
 * things about what the reader is holding.
 */
function Reason({ reason }: { reason: string }) {
  const { t } = useTranslation('console')
  const [slug, detail] = reason.split(/:(.*)/s)

  if (slug === 'malformed') {
    return (
      <p className={styles.reason}>
        {t('tools.decode.reason.malformed')}
        {detail ? <code className={styles.wire}> {detail}</code> : null}
      </p>
    )
  }
  if (KNOWN_REASONS.has(reason)) {
    return <p className={styles.reason}>{t(`tools.decode.reason.${reason}`)}</p>
  }
  return <code className={styles.wire}>{reason}</code>
}

function ParameterRow({ entry, index }: { entry: DecodedParameter; index: number }) {
  const { t } = useTranslation('console')
  const failed = entry.checks.some((check) => check.status === 'differs')

  return (
    <Disclosure
      defaultOpen={index === 0 || failed}
      title={
        <span className={styles.title}>
          <span className={styles.index}>{index + 1}</span>
          <code className={styles.paramName}>{entry.parameter}</code>
        </span>
      }
      meta={
        <span className={styles.meta}>
          {failed ? (
            <Tag tone="danger" icon={<AlertIcon size={12} />}>
              {t('tools.decode.mismatch')}
            </Tag>
          ) : null}
          <span className={styles.wire}>{entry.algorithm}</span>
        </span>
      }
    >
      <div className="u-stack">
        {entry.reason ? <Reason reason={entry.reason} /> : null}
        <Checks checks={entry.checks} />
        <FieldTable fields={entry.fields} />
        {entry.notes.map((note) =>
          KNOWN_NOTES.has(note) ? (
            <p key={note} className={styles.note}>
              {t(`tools.decode.note.${note}`)}
            </p>
          ) : (
            <code key={note} className={styles.wire}>
              {note}
            </code>
          ),
        )}
      </div>
    </Disclosure>
  )
}

export interface SignatureDecodeProps {
  parameters: readonly DecodedParameter[]
}

export function SignatureDecode({ parameters }: SignatureDecodeProps) {
  return (
    <>
      {parameters.map((entry, index) => (
        <ParameterRow key={`${entry.parameter}-${index}`} entry={entry} index={index} />
      ))}
    </>
  )
}
