import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { CodeBlock } from './CodeBlock'
import { Disclosure } from './Disclosure'
import {
  ActivityIcon,
  AlertIcon,
  CheckIcon,
  IdCardIcon,
  LinkIcon,
  LockIcon,
  MinusIcon,
} from './Icons'
import { toneClass } from './StatusBadge'
import { cn } from '@/lib/cn'
import type { Tone } from '@/lib/status'
import styles from './signing.module.css'

/**
 * The signing pipeline, unflattened.
 *
 * `signed_url` is the answer and `params` is that same answer as one blob;
 * neither says which layer put what into it, and that is the question a caller
 * whose request was refused actually has. Each layer is one row here, in the
 * order the layers ran, and a layer that contributed nothing gets a row too: an
 * empty `websign` is *why* the signature headers are missing, and a diagram with
 * a hole where that row belongs is how a reader learns to look at their jar.
 *
 * Rows for layers that ran arrive closed. What a reader wants on landing is
 * "did it work, and if not, where", and every row answers that in its header.
 */

/** Wire values, in the order the layers run. The endpoint keeps them stable for this diagram. */
type StageName = 'business' | 'session' | 'signature' | 'websign'
type MsTokenSource = 'url' | 'cookies' | 'generated' | 'absent'
type ParamRole = 'seal' | 'environment' | 'constant'

interface StageBase {
  name: StageName
  ran: boolean
  skipped_reason: string | null
  params: Record<string, string>
  headers: Record<string, string>
}

interface BusinessStage extends StageBase {
  name: 'business'
}

interface SessionStage extends StageBase {
  name: 'session'
  ms_token_source: MsTokenSource
}

interface SignatureStage extends StageBase {
  name: 'signature'
  /** The parameter that actually seals the request. On TikTok it is not the one `algorithm` names. */
  seal_param: string
  roles: Record<string, ParamRole>
  input_reconstructible: boolean
}

interface WebsignStage extends StageBase {
  name: 'websign'
  /** Both absent on a skipped layer; `preimage` is null when it did not reproduce the signature. */
  salt?: string
  preimage?: string | null
}

export type SigningStage =
  | BusinessStage
  | SessionStage
  | SignatureStage
  | WebsignStage

/*
 * Enum values this page has copy for. Anything else prints as itself: a slug a
 * caller can look up beats a translation key rendered where a sentence goes.
 */
const KNOWN_REASONS = new Set<string>(['no_uifid_cookie', 'no_ms_token_supplied'])
const KNOWN_SOURCES = new Set<string>(['url', 'cookies', 'generated', 'absent'])
const KNOWN_ROLES = new Set<string>(['seal', 'environment', 'constant'])

/** `generated` is the only value here the caller did not supply, so it is the only one flagged. */
const SOURCE_TONE: Record<string, Tone> = {
  url: 'neutral',
  cookies: 'neutral',
  generated: 'caution',
  absent: 'muted',
}

/** The seal takes the accent. The constant is muted because it seals nothing. */
const ROLE_TONE: Record<string, Tone> = {
  seal: 'accent',
  environment: 'neutral',
  constant: 'muted',
}

function SourceIcon({ source }: { source: string }) {
  switch (source) {
    case 'url':
      return <LinkIcon size={12} />
    case 'cookies':
      return <IdCardIcon size={12} />
    case 'generated':
      return <AlertIcon size={12} />
    default:
      return <MinusIcon size={12} />
  }
}

function RoleIcon({ role }: { role: string }) {
  switch (role) {
    case 'seal':
      return <LockIcon size={12} />
    case 'environment':
      return <ActivityIcon size={12} />
    default:
      return <MinusIcon size={12} />
  }
}

/** Colour plus icon plus text, like every other status in the console. */
function Tag({ tone, icon, children }: { tone: Tone; icon: ReactNode; children: ReactNode }) {
  return (
    <span className={cn(styles.tag, toneClass(tone))}>
      <span className={styles.tagIcon}>{icon}</span>
      {children}
    </span>
  )
}

function RoleCell({ role }: { role: string | undefined }) {
  const { t } = useTranslation('console')
  if (!role) return null
  if (!KNOWN_ROLES.has(role)) return <code className={styles.wire}>{role}</code>

  return (
    <Tag tone={ROLE_TONE[role] ?? 'neutral'} icon={<RoleIcon role={role} />}>
      {t(`tools.sign.stages.role.${role}`)}
    </Tag>
  )
}

/** What one layer added. The role column appears only where the roles differ per parameter. */
function ContributionTable({
  values,
  roles,
}: {
  values: Record<string, string>
  roles?: Record<string, ParamRole>
}) {
  const { t } = useTranslation('console')
  const entries = Object.entries(values)
  if (entries.length === 0) return <p className={styles.note}>{t('tools.sign.stages.noParams')}</p>

  return (
    <div className="u-scroll-x">
      <table className={styles.table}>
        <thead>
          <tr>
            <th>{t('tools.sign.stages.column.param')}</th>
            <th>{t('tools.sign.stages.column.value')}</th>
            {roles ? <th>{t('tools.sign.stages.column.role')}</th> : null}
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, value]) => (
            <tr key={name}>
              <td className={styles.paramName}>{name}</td>
              <td className={styles.paramValue}>
                {value === '' ? <span className="u-muted">{t('common:value.none')}</span> : value}
              </td>
              {roles ? (
                <td>
                  <RoleCell role={roles[name]} />
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Where the session token came from.
 *
 * `absent` is the same condition as the skip reason above it, so its sentence is
 * left to that one; the three sources a token can actually have are what this
 * explains, and `generated` - Douyin inventing one, which TikTok never does - is
 * the one a reader must not mistake for something they sent.
 */
function SessionDetail({ stage }: { stage: SessionStage }) {
  const { t } = useTranslation('console')
  const source = stage.ms_token_source

  return (
    <div className="u-stack-sm">
      <div className={styles.fact}>
        <span className={styles.factLabel}>{t('tools.sign.stages.source.label')}</span>
        <code className={styles.paramName}>{source}</code>
      </div>
      {stage.ran && KNOWN_SOURCES.has(source) ? (
        <p className={styles.reason}>{t(`tools.sign.stages.source.${source}`)}</p>
      ) : null}
    </div>
  )
}

function SignatureDetail({ stage }: { stage: SignatureStage }) {
  const { t } = useTranslation('console')

  return (
    <div className="u-stack-sm">
      <p className={styles.reason}>
        {t('tools.sign.stages.sealParam', { param: stage.seal_param })}
      </p>
      {stage.input_reconstructible ? null : (
        <p className={styles.note}>{t('tools.sign.stages.inputNotReconstructible')}</p>
      )}
    </div>
  )
}

/** The md5 preimage gets a CodeBlock: it runs to several hundred characters and is the
 *  one thing on this page a reader can check for themselves. */
function WebsignDetail({ stage }: { stage: WebsignStage }) {
  const { t } = useTranslation('console')

  return (
    <div className="u-stack-sm">
      {stage.salt ? (
        <div className={styles.fact}>
          <span className={styles.factLabel}>{t('tools.sign.stages.salt')}</span>
          <code className={styles.paramName}>{stage.salt}</code>
        </div>
      ) : null}
      {stage.preimage ? (
        <>
          <CodeBlock
            code={stage.preimage}
            language="text"
            title={t('tools.sign.stages.preimage')}
          />
          <p className={styles.note}>{t('tools.sign.stages.preimageHint')}</p>
        </>
      ) : (
        <p className={styles.note}>{t('tools.sign.stages.preimageUnverified')}</p>
      )}
    </div>
  )
}

function StageRow({ stage, index }: { stage: SigningStage; index: number }) {
  const { t } = useTranslation('console')
  const paramCount = Object.keys(stage.params).length
  const headerCount = Object.keys(stage.headers).length
  const reason = stage.skipped_reason

  return (
    <Disclosure
      // A layer that ran is reference material. A layer that did not is the answer.
      defaultOpen={!stage.ran}
      title={
        <span className={styles.title}>
          <span className={styles.index} aria-hidden="true">
            {index + 1}
          </span>
          <span className="u-truncate">{t(`tools.sign.stages.name.${stage.name}`)}</span>
          <code className={styles.wire}>{stage.name}</code>
        </span>
      }
      meta={
        <span className={styles.meta}>
          {stage.ran ? (
            <span className={styles.counts}>
              {t('tools.sign.stages.paramCount', { count: paramCount })}
            </span>
          ) : null}
          {stage.ran && headerCount > 0 ? (
            <span className={styles.counts}>
              {t('tools.sign.stages.headerCount', { count: headerCount })}
            </span>
          ) : null}
          {stage.name === 'session' ? (
            <Tag
              tone={SOURCE_TONE[stage.ms_token_source] ?? 'neutral'}
              icon={<SourceIcon source={stage.ms_token_source} />}
            >
              <code>{stage.ms_token_source}</code>
            </Tag>
          ) : null}
          {/* The seal, in the header: on TikTok it is X-Gnarly, and `algorithm` says X-Bogus. */}
          {stage.name === 'signature' ? (
            <Tag tone="accent" icon={<LockIcon size={12} />}>
              <code>{stage.seal_param}</code>
            </Tag>
          ) : null}
          {stage.ran ? (
            <Tag tone="success" icon={<CheckIcon size={12} />}>
              {t('tools.sign.stages.ran')}
            </Tag>
          ) : (
            <Tag tone="caution" icon={<AlertIcon size={12} />}>
              {t('tools.sign.stages.skipped')}
            </Tag>
          )}
        </span>
      }
    >
      <div className="u-stack">
        {stage.ran ? null : (
          <div className="u-stack-sm">
            {reason && KNOWN_REASONS.has(reason) ? (
              <p className={styles.reason}>{t(`tools.sign.stages.reason.${reason}`)}</p>
            ) : null}
            {reason ? <code className={styles.wire}>{reason}</code> : null}
          </div>
        )}
        {stage.name === 'session' ? <SessionDetail stage={stage} /> : null}
        {stage.name === 'signature' ? <SignatureDetail stage={stage} /> : null}
        {stage.ran ? (
          <ContributionTable
            values={stage.params}
            roles={stage.name === 'signature' ? stage.roles : undefined}
          />
        ) : null}
        {headerCount > 0 ? (
          <div className="u-stack-sm">
            <div className={styles.sectionLabel}>{t('tools.sign.stages.headersAdded')}</div>
            <ContributionTable values={stage.headers} />
          </div>
        ) : null}
        {stage.name === 'websign' && stage.ran ? <WebsignDetail stage={stage} /> : null}
      </div>
    </Disclosure>
  )
}

export interface SigningStagesProps {
  stages: readonly SigningStage[]
}

/**
 * The rows, without a container. Tools frames them in a Card and the playground
 * in a Disclosure; both want the same diagram and neither should have to know
 * how the other one draws its box.
 */
export function SigningStages({ stages }: SigningStagesProps) {
  // Direct siblings in a bare container: two rows meet on one hairline, not across a gap.
  return (
    <div>
      {stages.map((stage, index) => (
        // The verdict is part of the key so a row remounts when it changes. `defaultOpen` is
        // read once, and signing again without a jar has to reopen the websign row - that
        // second run is exactly when a reader needs it open.
        <StageRow key={`${stage.name}:${String(stage.ran)}`} stage={stage} index={index} />
      ))}
    </div>
  )
}

