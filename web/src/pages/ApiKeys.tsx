import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Button,
  Card,
  Checkbox,
  ConfirmDialog,
  DataTable,
  Input,
  KeyIcon,
  MaskedSecret,
  Modal,
  PageHeader,
  Select,
  StatusBadge,
  useToast,
  type Column,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate } from '@/hooks'
import { apiDelete, apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { SCOPES, type ApiKeySummary, type Scope } from '@/lib/types'

/**
 * API keys.
 *
 * Two things on this page are load bearing. The secret exists in exactly one
 * response - the one that creates it - so the reveal has to be impossible to
 * scroll past, and closing it is gated on an explicit acknowledgement rather
 * than a stray click on the backdrop (docs/design/06-api-auth-mcp.md).
 *
 * The other is the rate limit. It is abuse protection, not a billing quota:
 * this project has no plans, metering or tenancy, and the limit exists so that
 * one runaway script cannot drain the identity pool. The copy says so in every
 * place the number appears, because "rate limit" reads as "paid tier" to
 * anyone who has used a commercial API.
 */

const KEYS_QUERY = ['admin', 'api-keys'] as const

/** Key status as the server reports it; recomputed locally when it does not. */
type KeyStatus = 'active' | 'revoked' | 'expired'

interface ApiKeyRow extends ApiKeySummary {
  status?: KeyStatus
  user_id?: string
}

/**
 * The create response, and the only place a full key ever appears. The server
 * calls the field `key`; the console's own contract calls it `secret`. Accept
 * both rather than showing an empty box if one side is renamed.
 */
interface CreatedKey extends ApiKeyRow {
  secret?: string
  key?: string
  warning?: string
}

interface CreateBody {
  name: string
  scopes: Scope[]
  rate_limit: number | null
  expires_at: string | null
}

interface Draft {
  name: string
  scopes: Scope[]
  rateLimit: string
  expiresInDays: string
}

type DraftErrors = Partial<Record<'name' | 'scopes' | 'rateLimit', string>>

const EMPTY_DRAFT: Draft = { name: '', scopes: [], rateLimit: '', expiresInDays: '0' }

/** Scope values are wire identifiers and are never translated; these name their copy keys. */
const SCOPE_COPY: Record<Scope, string> = {
  'douyin:read': 'douyinRead',
  'tiktok:read': 'tiktokRead',
  'identity:manage': 'identityManage',
  admin: 'admin',
}

const EXPIRY_DAYS = ['0', '7', '30', '90', '365'] as const

const MIN_RATE_LIMIT = 1
const MAX_RATE_LIMIT = 100_000
const DAY_MS = 86_400_000

function keyStatus(row: ApiKeyRow, now: number): KeyStatus {
  if (row.status) return row.status
  if (row.revoked_at) return 'revoked'
  if (row.expires_at && new Date(row.expires_at).getTime() <= now) return 'expired'
  return 'active'
}

function secretOf(created: CreatedKey): string {
  return created.secret ?? created.key ?? ''
}

export default function ApiKeys() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const invalidate = useInvalidate()

  const keys = useApiQuery<ApiKeyRow[]>({
    key: KEYS_QUERY,
    path: paths.apiKeys.list,
    poll: POLL.slow,
  })

  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [errors, setErrors] = useState<DraftErrors>({})
  const [created, setCreated] = useState<CreatedKey | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [revoking, setRevoking] = useState<ApiKeyRow | null>(null)

  const create = useApiMutation<CreatedKey, CreateBody>(
    (body) => apiPost<CreatedKey>(paths.apiKeys.list, body),
    {
      onSuccess: (result) => {
        setCreated(result)
        setAcknowledged(false)
        setCreateOpen(false)
        setDraft(EMPTY_DRAFT)
        setErrors({})
        void invalidate(KEYS_QUERY)
      },
      onError: (error) => {
        toast.apiError(error, t('apiKeys.createFailed'))
      },
    },
  )

  const revoke = useApiMutation<unknown, string>((id) => apiDelete(paths.apiKeys.byId(id)), {
    onSuccess: () => {
      setRevoking(null)
      toast.success(t('apiKeys.revokedToast'))
      void invalidate(KEYS_QUERY)
    },
    onError: (error) => {
      toast.apiError(error, t('apiKeys.revokeFailed'))
    },
  })

  const submit = (): void => {
    const nextErrors: DraftErrors = {}
    const name = draft.name.trim()
    if (!name) nextErrors.name = t('apiKeys.nameRequired')
    if (draft.scopes.length === 0) nextErrors.scopes = t('apiKeys.scopesRequired')

    let rateLimit: number | null = null
    const rawRate = draft.rateLimit.trim()
    if (rawRate) {
      const parsed = Number(rawRate)
      if (!Number.isInteger(parsed) || parsed < MIN_RATE_LIMIT || parsed > MAX_RATE_LIMIT) {
        nextErrors.rateLimit = t('apiKeys.rateLimitInvalid', {
          min: MIN_RATE_LIMIT,
          max: MAX_RATE_LIMIT,
        })
      } else {
        rateLimit = parsed
      }
    }

    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    const days = Number(draft.expiresInDays)
    create.mutate({
      name,
      scopes: draft.scopes,
      rate_limit: rateLimit,
      expires_at: days > 0 ? new Date(Date.now() + days * DAY_MS).toISOString() : null,
    })
  }

  const toggleScope = (scope: Scope): void => {
    setDraft((current) => ({
      ...current,
      scopes: current.scopes.includes(scope)
        ? current.scopes.filter((entry) => entry !== scope)
        : [...current.scopes, scope],
    }))
  }

  const now = Date.now()

  const columns = useMemo<Array<Column<ApiKeyRow>>>(
    () => [
      {
        id: 'name',
        header: t('apiKeys.field.name'),
        cell: (row) => <span className="u-truncate">{row.name}</span>,
        sortValue: (row) => row.name,
        hideable: false,
      },
      {
        id: 'prefix',
        header: t('apiKeys.field.prefix'),
        mono: true,
        cell: (row) => <MaskedSecret value={`dtk_${row.prefix}_...`} />,
        sortValue: (row) => row.prefix,
      },
      {
        id: 'status',
        header: t('console:field.state'),
        cell: (row) => <StatusBadge kind="key" value={keyStatus(row, Date.now())} />,
        sortValue: (row) => keyStatus(row, Date.now()),
      },
      {
        id: 'scopes',
        header: t('console:field.scopes'),
        cell: (row) => (
          <span className="u-row u-wrap">
            {row.scopes.length === 0 ? (
              <span className="u-muted">{t('common:value.none')}</span>
            ) : (
              row.scopes.map((scope) => (
                <span
                  key={scope}
                  className="u-mono u-xs"
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    padding: '0 var(--space-1)',
                    color: 'var(--text-secondary)',
                  }}
                  title={t(`apiKeys.scopeHint.${SCOPE_COPY[scope]}`)}
                >
                  {scope}
                </span>
              ))
            )}
          </span>
        ),
      },
      {
        id: 'rateLimit',
        header: t('console:field.rateLimit'),
        align: 'right',
        cell: (row) =>
          row.rate_limit == null ? (
            <span className="u-muted">{t('apiKeys.rateLimitDefault')}</span>
          ) : (
            <span className="u-mono">
              {t('apiKeys.rateLimitValue', { value: format.number(row.rate_limit) })}
            </span>
          ),
        sortValue: (row) => row.rate_limit ?? null,
      },
      {
        id: 'expiresAt',
        header: t('console:field.expiresAt'),
        cell: (row) =>
          row.expires_at ? (
            <span className="u-mono" title={format.timestamp(row.expires_at)}>
              {format.date(row.expires_at)}
            </span>
          ) : (
            <span className="u-muted">{t('apiKeys.expiryNever')}</span>
          ),
        sortValue: (row) => row.expires_at ?? null,
      },
      {
        id: 'lastUsedAt',
        header: t('console:field.lastUsedAt'),
        cell: (row) =>
          row.last_used_at ? (
            <span title={format.timestamp(row.last_used_at)}>{format.relative(row.last_used_at)}</span>
          ) : (
            <span className="u-muted">{t('common:time.never')}</span>
          ),
        sortValue: (row) => row.last_used_at ?? null,
      },
      {
        id: 'createdAt',
        header: t('console:field.createdAt'),
        cell: (row) => (
          <span className="u-mono u-nowrap">{format.dateTime(row.created_at)}</span>
        ),
        sortValue: (row) => row.created_at,
        defaultHidden: true,
      },
      {
        id: 'actions',
        header: <span className="u-sr-only">{t('common:action.more')}</span>,
        align: 'right',
        hideable: false,
        cell: (row) => (
          <Button
            size="sm"
            variant="ghost"
            disabled={keyStatus(row, Date.now()) === 'revoked'}
            onClick={(event) => {
              event.stopPropagation()
              setRevoking(row)
            }}
          >
            {t('apiKeys.revoke')}
          </Button>
        ),
      },
    ],
    [t, format],
  )

  const rows = keys.data
  const activeCount = useMemo(
    () => (rows ?? []).filter((row) => keyStatus(row, now) === 'active').length,
    [rows, now],
  )

  return (
    <div className="u-stack-lg">
      <PageHeader
        title={t('page.apiKeys.title')}
        description={t('page.apiKeys.description')}
        badge={
          rows && rows.length > 0 ? (
            <span className="u-muted u-xs u-mono">
              {t('apiKeys.activeCount', { count: activeCount })}
            </span>
          ) : null
        }
        actions={
          <Button
            variant="primary"
            icon={<KeyIcon />}
            onClick={() => {
              setErrors({})
              setCreateOpen(true)
            }}
          >
            {t('apiKeys.create')}
          </Button>
        }
      />

      <Card title={t('apiKeys.notBillingTitle')} description={t('apiKeys.notBillingBody')}>
        <ul className="u-stack-sm u-secondary" style={{ margin: 0, paddingInlineStart: 'var(--space-4)' }}>
          <li>{t('apiKeys.noteScopes')}</li>
          <li>{t('apiKeys.notePrefix')}</li>
          <li>{t('apiKeys.noteRevoke')}</li>
        </ul>
      </Card>

      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        loading={keys.isLoading}
        error={keys.isError ? keys.error : undefined}
        onRetry={() => {
          void keys.refetch()
        }}
        storageKey="api-keys"
        defaultSort={{ columnId: 'createdAt', direction: 'desc' }}
        flashValue={(row) => keyStatus(row, Date.now())}
        emptyTitle={t('apiKeys.emptyTitle')}
        emptyDescription={t('apiKeys.emptyDescription')}
        emptyAction={
          <Button
            variant="primary"
            icon={<KeyIcon />}
            onClick={() => {
              setCreateOpen(true)
            }}
          >
            {t('apiKeys.create')}
          </Button>
        }
        caption={t('page.apiKeys.title')}
      />

      <Modal
        open={createOpen}
        onClose={() => {
          setCreateOpen(false)
        }}
        title={t('apiKeys.createTitle')}
        description={t('apiKeys.createDescription')}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setCreateOpen(false)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            <Button variant="primary" loading={create.isPending} onClick={submit}>
              {t('apiKeys.create')}
            </Button>
          </>
        }
      >
        <form
          className="u-stack"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <Input
            label={t('apiKeys.field.name')}
            description={t('apiKeys.nameHint')}
            required
            value={draft.name}
            error={errors.name}
            maxLength={128}
            autoComplete="off"
            onChange={(event) => {
              setDraft((current) => ({ ...current, name: event.target.value }))
            }}
          />

          <fieldset
            style={{ border: 0, margin: 0, padding: 0 }}
            aria-describedby="api-key-scopes-hint"
          >
            <legend
              style={{
                fontSize: 'var(--text-sm)',
                fontWeight: 'var(--weight-medium)',
                padding: 0,
                marginBottom: 'var(--space-1)',
              }}
            >
              {t('console:field.scopes')}
            </legend>
            <p id="api-key-scopes-hint" className="u-xs u-muted" style={{ margin: '0 0 var(--space-2)' }}>
              {t('apiKeys.scopesHint')}
            </p>
            <div className="u-stack-sm">
              {SCOPES.map((scope) => (
                <Checkbox
                  key={scope}
                  checked={draft.scopes.includes(scope)}
                  onChange={() => {
                    toggleScope(scope)
                  }}
                  label={
                    <span className="u-row">
                      <span className="u-mono">{scope}</span>
                      <span className="u-muted u-xs">{t(`apiKeys.scope.${SCOPE_COPY[scope]}`)}</span>
                    </span>
                  }
                  hint={t(`apiKeys.scopeHint.${SCOPE_COPY[scope]}`)}
                />
              ))}
            </div>
            {errors.scopes ? (
              <p
                className="u-xs"
                role="alert"
                style={{ color: 'var(--danger)', margin: 'var(--space-2) 0 0' }}
              >
                {errors.scopes}
              </p>
            ) : null}
          </fieldset>

          <Input
            label={t('console:field.rateLimit')}
            description={t('apiKeys.rateLimitHint')}
            type="number"
            inputMode="numeric"
            min={MIN_RATE_LIMIT}
            max={MAX_RATE_LIMIT}
            showOptional
            mono
            value={draft.rateLimit}
            error={errors.rateLimit}
            placeholder={t('apiKeys.rateLimitPlaceholder')}
            onChange={(event) => {
              setDraft((current) => ({ ...current, rateLimit: event.target.value }))
            }}
          />

          <Select
            label={t('apiKeys.expiry')}
            description={t('apiKeys.expiryHint')}
            value={draft.expiresInDays}
            onChange={(event) => {
              setDraft((current) => ({ ...current, expiresInDays: event.target.value }))
            }}
            options={EXPIRY_DAYS.map((days) => ({
              value: days,
              label:
                days === '0'
                  ? t('apiKeys.expiryNever')
                  : t('apiKeys.expiryDays', { count: Number(days) }),
            }))}
          />
        </form>
      </Modal>

      <Modal
        open={created !== null}
        onClose={() => {
          if (acknowledged) setCreated(null)
        }}
        closeOnBackdrop={false}
        title={t('apiKeys.createdTitle')}
        description={t('apiKeys.createdDescription')}
        size="lg"
        footer={
          <Button
            variant="primary"
            disabled={!acknowledged}
            onClick={() => {
              setCreated(null)
            }}
          >
            {t('apiKeys.createdDone')}
          </Button>
        }
      >
        {created ? (
          <div className="u-stack">
            <div
              role="alert"
              className="u-row"
              style={{
                alignItems: 'flex-start',
                gap: 'var(--space-2)',
                border: '1px solid var(--warning)',
                background: 'var(--bg-inset)',
                borderRadius: 'var(--radius)',
                padding: 'var(--space-3)',
                color: 'var(--text)',
              }}
            >
              <span style={{ color: 'var(--warning)', display: 'flex' }}>
                <AlertIcon size={16} />
              </span>
              <span>
                <strong>{t('common:secret.shownOnce')}</strong>
                <br />
                <span className="u-secondary">{t('apiKeys.createdWarning')}</span>
              </span>
            </div>

            <div className="u-stack-sm">
              <span className="u-xs u-muted">{t('apiKeys.createdKeyLabel')}</span>
              <MaskedSecret value={secretOf(created)} oneTime copyable />
            </div>

            <dl
              className="u-stack-sm"
              style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 'var(--space-2)' }}
            >
              <dt className="u-xs u-muted">{t('apiKeys.field.name')}</dt>
              <dd style={{ margin: 0 }}>{created.name}</dd>
              <dt className="u-xs u-muted">{t('console:field.scopes')}</dt>
              <dd className="u-mono u-xs" style={{ margin: 0 }}>
                {created.scopes.join(' ') || t('common:value.none')}
              </dd>
              <dt className="u-xs u-muted">{t('console:field.rateLimit')}</dt>
              <dd style={{ margin: 0 }}>
                {created.rate_limit == null
                  ? t('apiKeys.rateLimitDefault')
                  : t('apiKeys.rateLimitValue', { value: format.number(created.rate_limit) })}
              </dd>
              <dt className="u-xs u-muted">{t('console:field.expiresAt')}</dt>
              <dd style={{ margin: 0 }}>
                {created.expires_at ? format.dateTime(created.expires_at) : t('apiKeys.expiryNever')}
              </dd>
            </dl>

            <Checkbox
              checked={acknowledged}
              onChange={(event) => {
                setAcknowledged(event.target.checked)
              }}
              label={t('apiKeys.createdAck')}
            />
          </div>
        ) : null}
      </Modal>

      <ConfirmDialog
        open={revoking !== null}
        danger
        loading={revoke.isPending}
        title={t('apiKeys.revokeTitle')}
        description={t('apiKeys.revokeDescription', { name: revoking?.name ?? '' })}
        confirmLabel={t('apiKeys.revoke')}
        onCancel={() => {
          setRevoking(null)
        }}
        onConfirm={() => {
          if (revoking) revoke.mutate(revoking.id)
        }}
      />
    </div>
  )
}
