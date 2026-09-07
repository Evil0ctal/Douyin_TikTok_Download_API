/**
 * Every path the console calls, in one place.
 *
 * Paths are part of the API contract and are never translated. Keeping them here
 * means a backend rename is one edit, not a grep across fifteen pages.
 * See docs/design/06-api-auth-mcp.md and docs/design/15-operations.md.
 */

export const API_V1 = '/api/v1'

export const paths = {
  setup: {
    status: '/api/setup/status',
    init: '/api/setup/init',
  },
  auth: {
    session: `${API_V1}/auth/session`,
    login: `${API_V1}/auth/login`,
    logout: `${API_V1}/auth/logout`,
    password: `${API_V1}/auth/password`,
    sessions: `${API_V1}/auth/sessions`,
  },
  tasks: {
    root: `${API_V1}/tasks`,
    byId: (taskId: string) => `${API_V1}/tasks/${encodeURIComponent(taskId)}`,
    events: (taskId: string) => `${API_V1}/tasks/${encodeURIComponent(taskId)}/events`,
    batch: `${API_V1}/tasks/batch`,
  },
  parse: `${API_V1}/parse`,
  identities: {
    list: `${API_V1}/admin/identities`,
    mint: `${API_V1}/admin/identities/mint`,
    import: `${API_V1}/admin/identities/import`,
    byId: (id: string) => `${API_V1}/admin/identities/${encodeURIComponent(id)}`,
    test: (id: string) => `${API_V1}/admin/identities/${encodeURIComponent(id)}/test`,
    events: (id: string) => `${API_V1}/admin/identities/${encodeURIComponent(id)}/events`,
  },
  proxies: {
    list: `${API_V1}/admin/proxies`,
    import: `${API_V1}/admin/proxies/import`,
    byId: (id: string) => `${API_V1}/admin/proxies/${encodeURIComponent(id)}`,
    test: (id: string) => `${API_V1}/admin/proxies/${encodeURIComponent(id)}/test`,
  },
  apiKeys: {
    list: `${API_V1}/admin/api-keys`,
    byId: (id: string) => `${API_V1}/admin/api-keys/${encodeURIComponent(id)}`,
  },
  endpointsHealth: `${API_V1}/admin/endpoints/health`,
  metrics: {
    timeseries: `${API_V1}/admin/metrics/timeseries`,
  },
  logs: {
    requests: `${API_V1}/admin/logs/requests`,
    audit: `${API_V1}/admin/audit`,
  },
  settings: {
    list: `${API_V1}/admin/settings`,
    byKey: (key: string) => `${API_V1}/admin/settings/${encodeURIComponent(key)}`,
  },
  users: {
    list: `${API_V1}/admin/users`,
    byId: (id: string) => `${API_V1}/admin/users/${encodeURIComponent(id)}`,
  },
  notifications: {
    channels: `${API_V1}/admin/notifications/channels`,
    byId: (id: string) => `${API_V1}/admin/notifications/channels/${encodeURIComponent(id)}`,
    test: `${API_V1}/admin/notifications/test`,
  },
  diagnose: `${API_V1}/admin/diagnose`,
  backup: {
    list: `${API_V1}/admin/backup`,
    create: `${API_V1}/admin/backup`,
    restore: `${API_V1}/admin/backup/restore`,
    byId: (id: string) => `${API_V1}/admin/backup/${encodeURIComponent(id)}`,
  },
  system: {
    status: `${API_V1}/system/status`,
    health: '/healthz',
    ready: '/readyz',
  },
  docs: {
    /** Swagger UI is served by the api container and rendered per UI language. */
    swagger: '/docs',
    openapi: '/openapi.json',
    redoc: '/redoc',
  },
} as const
