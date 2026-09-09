import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Button,
  Card,
  CheckIcon,
  ConfirmDialog,
  CopyableId,
  CrossIcon,
  DataTable,
  Input,
  LogoutIcon,
  Modal,
  PageHeader,
  RefreshIcon,
  Select,
  StatusBadge,
  UsersIcon,
  type Column,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters, useInvalidate, useSession } from '@/hooks'
import { apiDelete, apiPost, apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { POLL } from '@/lib/query'
import { USER_ROLES, type UserRole } from '@/lib/types'

/**
 * Accounts, roles and sessions.
 *
 * The capability matrix is on the page rather than in the documentation because
 * a role name alone does not tell an administrator what they are handing over,
 * and doc 15 defines exactly three roles precisely so the whole rule set fits in
 * one small table.
 *
 * Two guards belong to the server and are surfaced here as its refusals: the
 * last administrator cannot be demoted or deleted, and you cannot delete the
 * account you are signed in with. A self-hosted instance without an
 * administrator can only be repaired from a shell.
 */

const USERS_KEY = ['admin', 'users'] as const
const SESSIONS_KEY = ['auth', 'sessions'] as const

const MIN_PASSWORD_LENGTH = 8

interface UserRow {
  id: string
  username: string
  role: UserRole
  created_at: string
  last_login_at?: string | null
}

interface SessionRow {
  id: string
  created_at?: string | null
  last_seen_at?: string | null
  ip?: string | null
  user_agent?: string | null
  current: boolean
}

/** Capabilities per role, straight from docs/design/15-operations.md. */
const CAPABILITIES: ReadonlyArray<{ id: string; roles: readonly UserRole[] }> = [
  { id: 'read', roles: ['admin', 'operator', 'viewer'] },
  { id: 'pool', roles: ['admin', 'operator'] },
  { id: 'keys', roles: ['admin', 'operator'] },
  { id: 'diagnose', roles: ['admin', 'operator'] },
  { id: 'settings', roles: ['admin', 'operator'] },
  { id: 'sensitive', roles: ['admin'] },
  { id: 'users', roles: ['admin'] },
  { id: 'backup', roles: ['admin'] },
]

interface CapabilityRow {
  id: string
  admin: boolean
  operator: boolean
  viewer: boolean
}

const CAPABILITY_ROWS: CapabilityRow[] = CAPABILITIES.map((capability) => ({
  id: capability.id,
  admin: capability.roles.includes('admin'),
  operator: capability.roles.includes('operator'),
  viewer: capability.roles.includes('viewer'),
}))

/** Colour, icon and word: a matrix of bare ticks is unreadable when pasted. */
function YesNo({ value }: { value: boolean }) {
  const { t } = useTranslation('common')
  return (
    <span
      className="u-nowrap"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        color: value ? 'var(--success)' : 'var(--text-muted)',
        fontSize: 'var(--text-xs)',
        lineHeight: 'var(--leading-xs)',
      }}
    >
      {value ? <CheckIcon size={11} /> : <CrossIcon size={11} />}
      {value ? t('value.yes') : t('value.no')}
    </span>
  )
}

function Banner({ tone, icon, children }: { tone: 'accent' | 'caution'; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 'var(--space-2)',
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius)',
        border: `1px solid var(--${tone === 'accent' ? 'border' : 'caution'})`,
        background: tone === 'accent' ? 'var(--accent-subtle)' : 'transparent',
        color: 'var(--text-secondary)',
        fontSize: 'var(--text-sm)',
        lineHeight: 'var(--leading-sm)',
      }}
    >
      <span style={{ color: `var(--${tone})`, flex: '0 0 auto', marginTop: '2px' }}>{icon}</span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  )
}

interface CreateDraft {
  username: string
  password: string
  role: UserRole
}

export default function Users() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const session = useSession()

  const role = session.data?.role ?? null
  const isAdmin = role === null || role === 'admin'

  const users = useApiQuery<UserRow[]>({
    key: USERS_KEY,
    path: paths.users.list,
    poll: POLL.slow,
    enabled: isAdmin,
  })

  const sessions = useApiQuery<SessionRow[]>({
    key: SESSIONS_KEY,
    path: paths.auth.sessions,
    poll: POLL.slow,
  })

  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<CreateDraft>({ username: '', password: '', role: 'viewer' })
  const [touched, setTouched] = useState(false)
  const [deleting, setDeleting] = useState<UserRow | null>(null)
  const [signingOut, setSigningOut] = useState(false)

  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [passwordTouched, setPasswordTouched] = useState(false)

  const create = useApiMutation<UserRow, CreateDraft>(
    (variables) => apiPost<UserRow>(paths.users.list, variables),
    {
      onSuccess: (created) => {
        setCreating(false)
        setTouched(false)
        setDraft({ username: '', password: '', role: 'viewer' })
        void invalidate(USERS_KEY)
        toast.success(t('users.toast.created', { username: created.username }))
      },
      onError: (error) => {
        toast.apiError(error, t('users.toast.createFailed'))
      },
    },
  )

  const changeRole = useApiMutation<unknown, { user: UserRow; role: UserRole }>(
    (variables) => apiPut(paths.users.byId(variables.user.id), { role: variables.role }),
    {
      onSuccess: (_result, variables) => {
        void invalidate(USERS_KEY)
        toast.success(
          t('users.toast.roleChanged', { username: variables.user.username, role: variables.role }),
        )
      },
      onError: (error) => {
        toast.apiError(error, t('users.toast.roleFailed'))
      },
    },
  )

  const remove = useApiMutation<unknown, UserRow>((user) => apiDelete(paths.users.byId(user.id)), {
    onSuccess: (_result, user) => {
      setDeleting(null)
      void invalidate(USERS_KEY)
      toast.success(t('users.toast.deleted', { username: user.username }))
    },
    onError: (error) => {
      setDeleting(null)
      toast.apiError(error, t('users.toast.deleteFailed'))
    },
  })

  const changePassword = useApiMutation<unknown, { current_password: string; new_password: string }>(
    (variables) => apiPost(paths.auth.password, variables),
    {
      onSuccess: () => {
        setCurrent('')
        setNext('')
        setRepeat('')
        setPasswordTouched(false)
        void invalidate(SESSIONS_KEY)
        toast.success(t('users.password.changed'), { description: t('users.password.changedHint') })
      },
      onError: (error) => {
        toast.apiError(error, t('users.password.failed'))
      },
    },
  )

  const signOutOthers = useApiMutation<unknown, void>(() => apiDelete(paths.auth.sessions), {
    onSuccess: () => {
      setSigningOut(false)
      void invalidate(SESSIONS_KEY)
      toast.success(t('users.sessions.signedOutOthers'))
    },
    onError: (error) => {
      setSigningOut(false)
      toast.apiError(error, t('users.sessions.signOutFailed'))
    },
  })

  const passwordTooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH
  const passwordMismatch = repeat.length > 0 && repeat !== next
  const passwordInvalid =
    current.length === 0 || next.length < MIN_PASSWORD_LENGTH || repeat !== next

  const usernameInvalid = draft.username.trim().length < 3
  const draftPasswordInvalid = draft.password.length < MIN_PASSWORD_LENGTH

  const userColumns: Array<Column<UserRow>> = [
    {
      id: 'username',
      header: t('field.username'),
      cell: (row) => (
        <span className="u-row" style={{ gap: 'var(--space-2)' }}>
          <span className="u-truncate">{row.username}</span>
          {row.id === session.data?.id ? (
            <span className="u-xs u-muted">{t('users.you')}</span>
          ) : null}
        </span>
      ),
      sortValue: (row) => row.username,
    },
    {
      id: 'role',
      header: t('field.role'),
      cell: (row) => (
        <span className="u-row" style={{ gap: 'var(--space-2)' }}>
          <StatusBadge kind="role" value={row.role} />
          <span className="u-mono u-xs u-muted">{row.role}</span>
        </span>
      ),
      sortValue: (row) => row.role,
      width: '200px',
    },
    {
      id: 'id',
      header: t('users.column.id'),
      mono: true,
      cell: (row) => <CopyableId value={row.id} length={10} middle />,
      width: '150px',
      defaultHidden: true,
    },
    {
      id: 'created',
      header: t('field.createdAt'),
      mono: true,
      cell: (row) => formatters.dateTime(row.created_at),
      sortValue: (row) => row.created_at,
      width: '180px',
      hideOnMobile: true,
    },
    {
      id: 'lastLogin',
      header: t('users.column.lastLogin'),
      mono: true,
      cell: (row) =>
        row.last_login_at ? (
          formatters.relative(row.last_login_at)
        ) : (
          <span className="u-muted">{t('common:time.never')}</span>
        ),
      sortValue: (row) => row.last_login_at ?? '',
      width: '160px',
    },
    {
      id: 'actions',
      header: <span className="u-sr-only">{t('common:action.more')}</span>,
      hideable: false,
      align: 'right',
      cell: (row) => (
        <span className="u-row" style={{ justifyContent: 'flex-end', gap: 'var(--space-2)' }}>
          <Select
            aria-label={t('users.action.changeRole')}
            value={row.role}
            style={{ maxWidth: '150px' }}
            disabled={!isAdmin || changeRole.isPending}
            onChange={(event) => {
              const value = event.target.value
              if ((USER_ROLES as readonly string[]).includes(value) && value !== row.role) {
                changeRole.mutate({ user: row, role: value as UserRole })
              }
            }}
            options={USER_ROLES.map((value) => ({ value, label: t(`common:state.role.${value}`) }))}
          />
          <Button
            size="sm"
            variant="ghost"
            disabled={!isAdmin || row.id === session.data?.id}
            title={row.id === session.data?.id ? t('users.cannotDeleteSelf') : undefined}
            onClick={() => {
              setDeleting(row)
            }}
          >
            {t('common:action.delete')}
          </Button>
        </span>
      ),
      width: '260px',
    },
  ]

  const sessionColumns: Array<Column<SessionRow>> = [
    {
      id: 'id',
      header: t('users.column.session'),
      mono: true,
      cell: (row) => (
        <span className="u-row" style={{ gap: 'var(--space-2)' }}>
          <CopyableId value={row.id} length={12} />
          {row.current ? (
            <span className="u-xs" style={{ color: 'var(--accent)' }}>
              {t('users.sessions.current')}
            </span>
          ) : null}
        </span>
      ),
      width: '220px',
    },
    {
      id: 'lastSeen',
      header: t('users.column.lastSeen'),
      mono: true,
      cell: (row) =>
        row.last_seen_at ? formatters.relative(row.last_seen_at) : <span className="u-muted">—</span>,
      sortValue: (row) => row.last_seen_at ?? '',
      width: '160px',
    },
    {
      id: 'created',
      header: t('field.createdAt'),
      mono: true,
      cell: (row) =>
        row.created_at ? formatters.dateTime(row.created_at) : <span className="u-muted">—</span>,
      sortValue: (row) => row.created_at ?? '',
      width: '180px',
      hideOnMobile: true,
    },
    {
      id: 'ip',
      header: t('users.column.ip'),
      mono: true,
      cell: (row) => row.ip ?? <span className="u-muted">—</span>,
      width: '150px',
    },
    {
      id: 'agent',
      header: t('users.column.userAgent'),
      cell: (row) => (
        <span className="u-truncate u-muted u-xs" title={row.user_agent ?? undefined}>
          {row.user_agent ?? '—'}
        </span>
      ),
      hideOnMobile: true,
    },
  ]

  const capabilityColumns: Array<Column<CapabilityRow>> = [
    {
      id: 'capability',
      header: t('users.matrix.capability'),
      cell: (row) => t(`users.capability.${row.id}`),
      hideable: false,
    },
    {
      id: 'admin',
      header: (
        <span className="u-row" style={{ gap: 'var(--space-1)' }}>
          {t('common:state.role.admin')} <span className="u-mono u-xs u-muted">admin</span>
        </span>
      ),
      cell: (row) => <YesNo value={row.admin} />,
      width: '150px',
    },
    {
      id: 'operator',
      header: (
        <span className="u-row" style={{ gap: 'var(--space-1)' }}>
          {t('common:state.role.operator')} <span className="u-mono u-xs u-muted">operator</span>
        </span>
      ),
      cell: (row) => <YesNo value={row.operator} />,
      width: '160px',
    },
    {
      id: 'viewer',
      header: (
        <span className="u-row" style={{ gap: 'var(--space-1)' }}>
          {t('common:state.role.viewer')} <span className="u-mono u-xs u-muted">viewer</span>
        </span>
      ),
      cell: (row) => <YesNo value={row.viewer} />,
      width: '150px',
    },
  ]

  return (
    <div className="u-page">
      <PageHeader
        title={t('page.users.title')}
        description={t('page.users.description')}
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              icon={<RefreshIcon />}
              loading={users.isFetching && !users.isLoading}
              onClick={() => {
                void invalidate(USERS_KEY)
                void invalidate(SESSIONS_KEY)
              }}
            >
              {t('common:action.refresh')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon={<UsersIcon />}
              disabled={!isAdmin}
              onClick={() => {
                setCreating(true)
              }}
            >
              {t('users.action.create')}
            </Button>
          </>
        }
      />

      {isAdmin ? null : (
        <Banner tone="caution" icon={<AlertIcon size={14} />}>
          {t('users.adminOnly')}
        </Banner>
      )}

      {isAdmin ? (
        <Card title={t('users.list.title')} description={t('users.list.description')} flush>
          <DataTable
            columns={userColumns}
            rows={users.data}
            getRowId={(row) => row.id}
            loading={users.isLoading}
            error={users.isError ? users.error : undefined}
            onRetry={() => {
              void users.refetch()
            }}
            storageKey="users"
            flashValue={(row) => row.role}
            emptyTitle={t('users.empty.title')}
            emptyDescription={t('users.empty.description')}
            caption={t('users.list.title')}
          />
        </Card>
      ) : null}

      <Card title={t('users.matrix.title')} description={t('users.matrix.description')} flush>
        <DataTable
          columns={capabilityColumns}
          rows={CAPABILITY_ROWS}
          getRowId={(row) => row.id}
          storageKey="users-capabilities"
          caption={t('users.matrix.title')}
        />
      </Card>

      <Card title={t('users.password.title')} description={t('users.password.description')}>
        <form
          className="u-stack"
          style={{ maxWidth: '420px' }}
          onSubmit={(event) => {
            event.preventDefault()
            setPasswordTouched(true)
            if (passwordInvalid) return
            changePassword.mutate({ current_password: current, new_password: next })
          }}
        >
          <Input
            label={t('users.password.current')}
            type="password"
            autoComplete="current-password"
            required
            value={current}
            error={passwordTouched && current.length === 0 ? t('users.password.required') : undefined}
            onChange={(event) => {
              setCurrent(event.target.value)
            }}
          />
          <Input
            label={t('users.password.next')}
            type="password"
            autoComplete="new-password"
            required
            value={next}
            description={t('users.password.rule', { min: MIN_PASSWORD_LENGTH })}
            error={passwordTooShort ? t('users.password.tooShort', { min: MIN_PASSWORD_LENGTH }) : undefined}
            onChange={(event) => {
              setNext(event.target.value)
            }}
          />
          <Input
            label={t('users.password.repeat')}
            type="password"
            autoComplete="new-password"
            required
            value={repeat}
            error={passwordMismatch ? t('users.password.mismatch') : undefined}
            onChange={(event) => {
              setRepeat(event.target.value)
            }}
          />
          <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
            <Button type="submit" variant="primary" loading={changePassword.isPending}>
              {t('users.password.submit')}
            </Button>
            <span className="u-xs u-muted">{t('users.password.revokes')}</span>
          </div>
        </form>
      </Card>

      <Card
        title={t('users.sessions.title')}
        description={t('users.sessions.description')}
        flush
        actions={
          <Button
            size="sm"
            variant="secondary"
            icon={<LogoutIcon />}
            loading={signOutOthers.isPending}
            onClick={() => {
              setSigningOut(true)
            }}
          >
            {t('users.sessions.signOutOthers')}
          </Button>
        }
      >
        <DataTable
          columns={sessionColumns}
          rows={sessions.data}
          getRowId={(row) => row.id}
          loading={sessions.isLoading}
          error={sessions.isError ? sessions.error : undefined}
          onRetry={() => {
            void sessions.refetch()
          }}
          storageKey="sessions"
          emptyTitle={t('users.sessions.emptyTitle')}
          emptyDescription={t('users.sessions.emptyDescription')}
          caption={t('users.sessions.title')}
        />
      </Card>

      <Modal
        open={creating}
        onClose={() => {
          setCreating(false)
        }}
        title={t('users.create.title')}
        description={t('users.create.description')}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setCreating(false)
              }}
              disabled={create.isPending}
            >
              {t('common:action.cancel')}
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              onClick={() => {
                setTouched(true)
                if (usernameInvalid || draftPasswordInvalid) return
                create.mutate({ ...draft, username: draft.username.trim() })
              }}
            >
              {t('common:action.create')}
            </Button>
          </>
        }
      >
        <div className="u-stack">
          <Input
            label={t('field.username')}
            value={draft.username}
            mono
            required
            autoComplete="off"
            description={t('users.create.usernameHint')}
            error={touched && usernameInvalid ? t('users.create.usernameInvalid') : undefined}
            onChange={(event) => {
              setDraft({ ...draft, username: event.target.value })
            }}
          />
          <Input
            label={t('users.create.password')}
            type="password"
            value={draft.password}
            required
            autoComplete="new-password"
            description={t('users.password.rule', { min: MIN_PASSWORD_LENGTH })}
            error={
              touched && draftPasswordInvalid
                ? t('users.password.tooShort', { min: MIN_PASSWORD_LENGTH })
                : undefined
            }
            onChange={(event) => {
              setDraft({ ...draft, password: event.target.value })
            }}
          />
          <Select
            label={t('field.role')}
            value={draft.role}
            description={t('users.create.roleHint')}
            onChange={(event) => {
              const value = event.target.value
              if ((USER_ROLES as readonly string[]).includes(value)) {
                setDraft({ ...draft, role: value as UserRole })
              }
            }}
            options={USER_ROLES.map((value) => ({ value, label: t(`common:state.role.${value}`) }))}
          />
        </div>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        danger
        loading={remove.isPending}
        confirmPhrase={deleting?.username}
        title={t('users.confirm.deleteTitle')}
        description={t('users.confirm.deleteDescription', { username: deleting?.username ?? '' })}
        confirmLabel={t('common:action.delete')}
        onCancel={() => {
          setDeleting(null)
        }}
        onConfirm={() => {
          if (deleting) remove.mutate(deleting)
        }}
      >
        <Banner tone="caution" icon={<AlertIcon size={14} />}>
          {t('users.confirm.deleteWarning')}
        </Banner>
      </ConfirmDialog>

      <ConfirmDialog
        open={signingOut}
        loading={signOutOthers.isPending}
        title={t('users.confirm.signOutTitle')}
        description={t('users.confirm.signOutDescription')}
        confirmLabel={t('users.sessions.signOutOthers')}
        onCancel={() => {
          setSigningOut(false)
        }}
        onConfirm={() => {
          signOutOthers.mutate()
        }}
      />

    </div>
  )
}
