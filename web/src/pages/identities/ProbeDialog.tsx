import { useTranslation } from 'react-i18next'

import {
  Button,
  CodeBlock,
  ErrorState,
  IdCardIcon,
  Modal,
  Skeleton,
  StatusBadge,
} from '@/components'
import { useFormatters } from '@/hooks'
import { MISSING } from '@/lib/format'

import { Fact } from './shared'
import type { IdentityRow, ProbeState } from './types'

interface ProbeDialogProps {
  open: boolean
  identity: IdentityRow | null
  state: ProbeState | null
  onClose: () => void
}

/** The answer to a Test pressed in the table, where there is no drawer to put it in. */
export function ProbeDialog({ open, identity, state, onClose }: ProbeDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  if (!identity) return null
  const result = state?.result

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('console:identity.test.title')}
      description={identity.id}
      footer={
        <Button variant="secondary" onClick={onClose}>
          {t('common:action.close')}
        </Button>
      }
    >
      <div className="u-stack">
        {state?.running ? <Skeleton height={80} radius="var(--radius)" /> : null}
        {state?.error ? <ErrorState error={state.error} /> : null}
        {result ? (
          <>
            <div className="u-row u-wrap">
              <StatusBadge kind="health" value={result.ok ? 'healthy' : 'unhealthy'} flash={false} />
              {result.outcome ? (
                <StatusBadge kind="outcome" value={result.outcome} flash={false} />
              ) : null}
            </div>
            <div className="u-row u-wrap">
              <Fact label={t('console:field.httpStatus')} value={result.status ?? MISSING} mono />
              <Fact label={t('console:field.latency')} value={format.latency(result.latency_ms)} mono />
              <Fact label={t('console:identity.test.rule')} value={result.rule ?? MISSING} mono />
            </div>
            {result.detail ? <CodeBlock code={result.detail} language="text" /> : null}
            {/* The note explains the verdict, so it has to depend on it. It
                used to say "a business error counts as a pass" under every
                result, including a plain success - which reads as the console
                telling you the post is gone when the platform said nothing of
                the kind. */}
            <p className="u-xs u-muted">
              {t(`console:identity.test.note.${result.outcome ?? 'unknown'}`, {
                defaultValue: t('console:identity.test.note.unknown'),
              })}
            </p>
          </>
        ) : null}
        {!state?.running && !result && !state?.error ? (
          <p className="u-row u-muted">
            <IdCardIcon size={14} />
            {t('console:identity.test.idle')}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}
