import { useTranslation } from 'react-i18next'

import { Button, CopyableId, type Column } from '@/components'
import { useFormatters } from '@/hooks'
import { MISSING } from '@/lib/format'

import { HealthCell, KindCell, SessionCell, StateCell } from './cells'
import { needsReset, remainingMs, usePlatformName } from './shared'
import type { IdentityRow, ProbeState } from './types'

interface ColumnActions {
  /** Whether the row's own buttons are there at all: they are all operator writes. */
  canManage: boolean
  probes: Record<string, ProbeState>
  onTest: (identity: IdentityRow) => void
  onReset: (identity: IdentityRow) => void
  onRetire: (identity: IdentityRow) => void
}

/** The identity table's columns, in the order a row is read. */
export function useIdentityColumns({
  canManage,
  probes,
  onTest,
  onReset,
  onRetire,
}: ColumnActions): Array<Column<IdentityRow>> {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const platformName = usePlatformName()

  const columns: Array<Column<IdentityRow>> = [
    {
      id: 'state',
      header: t('console:field.state'),
      width: '120px',
      sortValue: (row) => row.state,
      cell: (row) => <StateCell identity={row} />,
    },
    {
      id: 'health',
      header: (
        <span title={t('console:identity.columnHint.health')}>
          {t('console:identity.column.health')}
        </span>
      ),
      width: '155px',
      // The measured score only. A 0..1 success ratio and a count of failures
      // are different quantities, and folding the second in as a negative
      // number sorted an identity with no traffic and no failures above one
      // measured at 100%. Unmeasured rows return null, which the table sorts as
      // unmeasured rather than as either end of the scale.
      sortValue: (row) => row.health ?? null,
      cell: (row) => <HealthCell identity={row} />,
    },
    {
      id: 'platform',
      header: t('console:field.platform'),
      width: '90px',
      sortValue: (row) => row.platform,
      cell: (row) => platformName(row.platform),
    },
    {
      // The column that tells "retire this identity" from "the signer is
      // broken". Both look like an empty response from the outside.
      id: 'session',
      header: (
        <span title={t('console:identity.columnHint.session')}>
          {t('console:identity.column.session')}
        </span>
      ),
      width: '165px',
      sortValue: (row) => row.session?.verdict ?? 'unknown',
      cell: (row) => <SessionCell identity={row} />,
    },
    {
      id: 'id',
      header: t('console:field.identityId'),
      // A floor, not the whole id. It was 292px - every character of a uuid -
      // on a table that also has to fit a proxy, a kind and four buttons, and
      // a phone scrolled sideways to reach any of them. The cell ellipsises
      // what does not fit, copies the whole value on a click, and the drawer
      // prints it in full.
      minWidth: '140px',
      mono: true,
      // Not hideable. Pinning a request names one identity by its id, and this
      // cell is where that id gets copied from; a column preference persisted
      // in localStorage could otherwise leave the page permanently unable to
      // answer the question it is now most often opened for.
      hideable: false,
      sortValue: (row) => row.id,
      cell: (row) => <CopyableId value={row.id} />,
    },
    {
      // A column of its own beside the id. It started as a small line under
      // the id, which read as part of the id and went unnoticed: the operator
      // who asked for notes opened each drawer to find them.
      id: 'note',
      header: t('console:identity.note.label'),
      sortValue: (row) => row.note ?? null,
      cell: (row) =>
        row.note ? (
          // Capped, because the table lays out automatically and one 200
          // character note would otherwise widen the column for every row.
          <span
            className="u-truncate"
            title={row.note}
            style={{ display: 'block', maxWidth: '220px' }}
          >
            {row.note}
          </span>
        ) : (
          <span className="u-muted">{MISSING}</span>
        ),
    },
    {
      id: 'proxy',
      header: t('console:field.proxy'),
      // Wide enough for the "no proxy" sentence. Without it the column
      // collapses to the width of one character and renders that phrase
      // vertically, one glyph per line.
      width: '150px',
      sortValue: (row) => row.proxy_label ?? row.proxy_id ?? '',
      // The name someone gave the egress when there is one, the id when there
      // is not: the serializer sends `proxy_label: null` for an unnamed proxy,
      // for an identity with no proxy, and for a proxy row that went away
      // underneath the page. The id stays reachable on hover either way,
      // because the label is what an operator recognises and the id is what
      // they have to quote.
      cell: (row) =>
        row.proxy_id ? (
          row.proxy_label ? (
            <span className="u-truncate" title={row.proxy_id}>
              {row.proxy_label}
            </span>
          ) : (
            <CopyableId value={row.proxy_id} />
          )
        ) : (
          <span className="u-muted u-truncate">{t('console:identity.noProxy')}</span>
        ),
    },
    {
      id: 'kind',
      header: t('console:identity.column.kind'),
      width: '130px',
      // Logins first, soonest to lapse first among them: the order somebody
      // re-importing logins works through them in.
      sortValue: (row) =>
        row.authenticated
          ? row.expires_at
            ? new Date(row.expires_at).getTime()
            : Number.MAX_SAFE_INTEGER
          : null,
      cell: (row) => <KindCell identity={row} />,
    },
    {
      id: 'cooldown',
      header: t('console:identity.column.cooldown'),
      align: 'right',
      mono: true,
      sortValue: (row) => remainingMs(row.cooldown_until) ?? -1,
      cell: (row) => {
        const remaining = remainingMs(row.cooldown_until)
        return remaining === null ? (
          <span className="u-muted">{MISSING}</span>
        ) : (
          format.duration(remaining)
        )
      },
    },
    {
      id: 'lastUsed',
      header: t('console:field.lastUsedAt'),
      sortValue: (row) => row.last_used_at ?? '',
      cell: (row) =>
        row.last_used_at ? (
          <span title={format.timestamp(row.last_used_at)}>{format.relative(row.last_used_at)}</span>
        ) : (
          <span className="u-muted">{t('common:time.never')}</span>
        ),
    },
    {
      id: 'fails',
      header: t('console:field.consecutiveFails'),
      align: 'right',
      mono: true,
      defaultHidden: true,
      sortValue: (row) => row.consecutive_fails,
      cell: (row) => format.number(row.consecutive_fails),
    },
    {
      id: 'minted',
      header: t('console:identity.column.minted'),
      defaultHidden: true,
      sortValue: (row) => row.minted_at,
      cell: (row) => (
        <span title={format.timestamp(row.minted_at)}>{format.relative(row.minted_at)}</span>
      ),
    },
  ]

  if (canManage) {
    columns.push({
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      width: '160px',
      cell: (row) => (
        <span className="u-row">
          <Button
            size="sm"
            variant="secondary"
            loading={probes[row.id]?.running ?? false}
            disabled={row.state === 'retired'}
            onClick={(event) => {
              event.stopPropagation()
              onTest(row)
            }}
          >
            {t('common:action.test')}
          </Button>
          {/* Only where it does something. An active identity with no streak
              has nothing to clear, and a button that is always there teaches
              people to press it as a ritual. */}
          {needsReset(row) ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={(event) => {
                event.stopPropagation()
                onReset(row)
              }}
            >
              {t('console:identity.reset.action')}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            disabled={row.state === 'retired'}
            onClick={(event) => {
              event.stopPropagation()
              onRetire(row)
            }}
          >
            {t('console:identity.retire')}
          </Button>
        </span>
      ),
    })
  }
  return columns
}
