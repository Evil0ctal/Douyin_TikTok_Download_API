import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'wouter'

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
import {
  SESSION_KEY,
  useApiMutation,
  useApiQuery,
  useFormatters,
  useInvalidate,
  useSession,
} from '@/hooks'
import { apiDelete, apiPost, apiPut } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MIN_PASSWORD_LENGTH } from '@/lib/passwords'
import { POLL } from '@/lib/query'
import { atLeast } from '@/lib/roles'
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
 *
 * A role change is confirmed before it is sent. The picker used to save on
 * change, and one slip on the demo account's row made the account whose
 * password the login page publishes an administrator. That row is now locked
 * outright - the server refuses any change to it as well - and `demo` is not
 * offered as a role, because demo mode mints it and nobody assigns it.
 */

const USERS_KEY = ['admin', 'users'] as const
const SESSIONS_KEY = ['auth', 'sessions'] as const

/** Roles an administrator hands out. `demo` belongs to demo mode, not to this page. */
const ASSIGNABLE_ROLES: readonly UserRole[] = USER_ROLES.filter((role) => role !== 'demo')

function isAssignable(value: string): value is UserRole {
  return (ASSIGNABLE_ROLES as readonly string[]).includes(value)
}

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

/**
 * Capabilities per role, straight from docs/design/15-operations.md.
 *
 * `demo` appears on two rows and no others, which is the point of it: it calls
 * the platforms and reads the boards that carry no credential, and it is
 * refused everywhere else - including on every write, whatever the row says,
 * because the account is read-only by a separate rule.
 */
const CAPABILITIES: ReadonlyArray<{ id: string; roles: readonly UserRole[] }> = [
  { id: 'fetch', roles: ['admin', 'operator', 'viewer', 'demo'] },
  { id: 'read', roles: ['admin', 'operator', 'viewer'] },
  { id: 'readPublic', roles: ['admin', 'operator', 'viewer', 'demo'] },
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
  demo: boolean
}

const CAPABILITY_ROWS: CapabilityRow[] = CAPABILITIES.map((capability) => ({
  id: capability.id,
  admin: capability.roles.includes('admin'),
  operator: capability.roles.includes('operator'),
  viewer: capability.roles.includes('viewer'),
  demo: capability.roles.includes('demo'),
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

interface RoleChange {
  user: UserRow
  role: UserRole
}

interface ResetDraft {
  user: UserRow
  password: string
  repeat: string
  touched: boolean
}

interface UpdateResult {
  revoked_sessions?: number
}

export default function Users() {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const formatters = useFormatters()
  const invalidate = useInvalidate()
  const session = useSession()

  const role = session.data?.role ?? null
  const isAdmin = atLeast(role, 'admin')

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
  const [roleChange, setRoleChange] = useState<RoleChange | null>(null)
  const [resetting, setResetting] = useState<ResetDraft | null>(null)

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

  const changeRole = useApiMutation<unknown, RoleChange>(
    (variables) => apiPut(paths.users.byId(variables.user.id), { role: variables.role }),
    {
      onSuccess: (_result, variables) => {
        setRoleChange(null)
        void invalidate(USERS_KEY)
        // Demoting yourself changes what this console may show you.
        if (variables.user.id === session.data?.id) void invalidate(SESSION_KEY)
        toast.success(
          t('users.toast.roleChanged', {
            username: variables.user.username,
            role: t(`common:state.role.${variables.role}`),
          }),
        )
      },
      onError: (error) => {
        setRoleChange(null)
        toast.apiError(error, t('users.toast.roleFailed'))
      },
    },
  )

  // The administrator's reset: no current password, and every session the
  // account has open is signed out. The new password is neither echoed in the
  // toast nor kept in state once it has been sent.
  const resetPassword = useApiMutation<UpdateResult | null, { user: UserRow; password: string }>(
    (variables) =>
      apiPut<UpdateResult | null>(paths.users.byId(variables.user.id), {
        password: variables.password,
      }),
    {
      onSuccess: (result, variables) => {
        setResetting(null)
        toast.success(t('users.reset.done', { username: variables.user.username }), {
          description: t('users.reset.doneHint', { count: result?.revoked_sessions ?? 0 }),
        })
      },
      onError: (error) => {
        setResetting((current) => (current ? { ...current, password: '', repeat: '' } : current))
        toast.apiError(error, t('users.reset.failed'))
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

  const resetTooShort =
    resetting !== null && resetting.touched && resetting.password.length < MIN_PASSWORD_LENGTH
  const resetMismatch =
    resetting !== null && resetting.repeat.length > 0 && resetting.repeat !== resetting.password
  const resetInvalid =
    resetting === null ||
    resetting.password.length < MIN_PASSWORD_LENGTH ||
    resetting.repeat !== resetting.password

  const roleLabel = (value: UserRole): string => t(`common:state.role.${value}`)
  const promoting = roleChange?.role === 'admin'
  const demotingSelf = roleChange !== null && roleChange.user.id === session.data?.id

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
      // A uuid is 36 characters and every one of them carries; the last few
      // are where two ids issued in the same second differ. A floor rather
      // than a width, because auto layout overrides a width the moment the
      // columns want more room than the container has - measured at this
      // font, 292px is the whole id plus the copy affordance.
      minWidth: '292px',
      mono: true,
      cell: (row) => <CopyableId value={row.id} />,
      // No width: 150px cut a 36-character uuid in half on a table that had
      // the room for it. Left to size itself, the column shows the whole id
      // where it fits and ellipsises only where it genuinely does not.
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
      cell: (row) =>
        row.role === 'demo' ? (
          // Its role and password belong to demo mode and the server refuses
          // both here; what this row can offer is the way to where they live.
          <span className="u-xs u-muted">
            {t('users.demoLocked')} <Link href="/settings">{t('users.demoLockedLink')}</Link>
          </span>
        ) : (
          <span className="u-row u-wrap" style={{ justifyContent: 'flex-end', gap: 'var(--space-2)' }}>
            <Select
              aria-label={t('users.action.changeRole')}
              value={row.role}
              style={{ maxWidth: '150px' }}
              disabled={changeRole.isPending}
              onChange={(event) => {
                const value = event.target.value
                if (isAssignable(value) && value !== row.role) {
                  setRoleChange({ user: row, role: value })
                }
              }}
              options={ASSIGNABLE_ROLES.map((value) => ({ value, label: roleLabel(value) }))}
            />
            {/* Your own goes through the form below, which asks for the current
                password; this one would also sign you out of this tab. */}
            {row.id === session.data?.id ? null : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setResetting({ user: row, password: '', repeat: '', touched: false })
                }}
              >
                {t('users.action.resetPassword')}
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={row.id === session.data?.id}
              title={row.id === session.data?.id ? t('users.cannotDeleteSelf') : undefined}
              onClick={() => {
                setDeleting(row)
              }}
            >
              {t('common:action.delete')}
            </Button>
          </span>
        ),
      width: '360px',
    },
  ]

  const sessionColumns: Array<Column<SessionRow>> = [
    {
      id: 'id',
      header: t('users.column.session'),
      mono: true,
      // The whole identifier plus the "this one" marker beside it. Safe to
      // show: it is a truncated sha256 of the session token and not the token
      // - the cookie itself never reaches this page - so there is nothing here
      // to protect by hiding half of it.
      minWidth: '220px',
      cell: (row) => (
        <span className="u-row" style={{ gap: 'var(--space-2)' }}>
          <CopyableId value={row.id} />
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
    {
      id: 'demo',
      header: (
        <span className="u-row" style={{ gap: 'var(--space-1)' }}>
          {t('common:state.role.demo')} <span className="u-mono u-xs u-muted">demo</span>
        </span>
      ),
      cell: (row) => <YesNo value={row.demo} />,
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
            {isAdmin ? (
              <Button
                variant="primary"
                size="sm"
                icon={<UsersIcon />}
                onClick={() => {
                  setCreating(true)
                }}
              >
                {t('users.action.create')}
              </Button>
            ) : null}
          </>
        }
      />

      {/* Not while the session is still loading: "only an administrator" on
          an administrator's screen, even for a frame, reads as a demotion. */}
      {role === null || isAdmin ? null : (
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
              if (isAssignable(value)) {
                setDraft({ ...draft, role: value })
              }
            }}
            options={ASSIGNABLE_ROLES.map((value) => ({ value, label: roleLabel(value) }))}
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
        open={roleChange !== null}
        danger={promoting || demotingSelf}
        loading={changeRole.isPending}
        title={t('users.confirm.roleTitle')}
        description={
          roleChange
            ? t('users.confirm.roleDescription', {
                username: roleChange.user.username,
                from: roleLabel(roleChange.user.role),
                to: roleLabel(roleChange.role),
              })
            : undefined
        }
        confirmLabel={t('users.action.changeRole')}
        onCancel={() => {
          setRoleChange(null)
        }}
        onConfirm={() => {
          if (roleChange) changeRole.mutate(roleChange)
        }}
      >
        {demotingSelf ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('users.confirm.roleSelfWarning')}
          </Banner>
        ) : promoting ? (
          <Banner tone="caution" icon={<AlertIcon size={14} />}>
            {t('users.confirm.roleAdminWarning')}
          </Banner>
        ) : null}
      </ConfirmDialog>

      <Modal
        open={resetting !== null}
        onClose={() => {
          if (!resetPassword.isPending) setResetting(null)
        }}
        title={t('users.reset.title', { username: resetting?.user.username ?? '' })}
        description={t('users.reset.description')}
        footer={
          <>
            <Button
              variant="ghost"
              disabled={resetPassword.isPending}
              onClick={() => {
                setResetting(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            <Button
              type="submit"
              form="user-reset-password"
              variant="danger"
              loading={resetPassword.isPending}
            >
              {t('users.reset.submit')}
            </Button>
          </>
        }
      >
        {resetting ? (
          <form
            id="user-reset-password"
            className="u-stack"
            onSubmit={(event) => {
              event.preventDefault()
              setResetting({ ...resetting, touched: true })
              if (resetInvalid) return
              resetPassword.mutate({ user: resetting.user, password: resetting.password })
            }}
          >
            {/* A username the browser can see, so a password manager files the
                new password under this account rather than under whoever is
                signed in. */}
            <input type="text" autoComplete="username" value={resetting.user.username} readOnly hidden />
            <Input
              label={t('users.password.next')}
              type="password"
              autoComplete="new-password"
              required
              value={resetting.password}
              description={t('users.password.rule', { min: MIN_PASSWORD_LENGTH })}
              error={resetTooShort ? t('users.password.tooShort', { min: MIN_PASSWORD_LENGTH }) : undefined}
              onChange={(event) => {
                setResetting({ ...resetting, password: event.target.value })
              }}
            />
            <Input
              label={t('users.password.repeat')}
              type="password"
              autoComplete="new-password"
              required
              value={resetting.repeat}
              error={resetMismatch ? t('users.password.mismatch') : undefined}
              onChange={(event) => {
                setResetting({ ...resetting, repeat: event.target.value })
              }}
            />
            <Banner tone="caution" icon={<LogoutIcon size={14} />}>
              {t('users.reset.revokes')}
            </Banner>
          </form>
        ) : null}
      </Modal>

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
