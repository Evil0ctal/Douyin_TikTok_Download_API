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
    /** The caller's own principal. `sessions` below is the device list, not this. */
    me: `${API_V1}/auth/me`,
    login: `${API_V1}/auth/login`,
    logout: `${API_V1}/auth/logout`,
    password: `${API_V1}/auth/password`,
    sessions: `${API_V1}/auth/sessions`,
  },
  tasks: {
    /**
     * The family prefix; the API serves no GET here, only the paths below it.
     * lib/query.ts matches cache entries against it, which is how a polled task
     * (Diagnose) gets expired when the language changes.
     */
    root: `${API_V1}/tasks`,
    byId: (taskId: string) => `${API_V1}/tasks/${encodeURIComponent(taskId)}`,
    events: (taskId: string) => `${API_V1}/tasks/${encodeURIComponent(taskId)}/events`,
    batch: `${API_V1}/tasks/batch`,
  },
  parse: `${API_V1}/parse`,
  identities: {
    list: `${API_V1}/admin/identities`,
    mint: `${API_V1}/admin/identities/mint`,
    /** Pool level against pool.min_size, per platform: what the refill job reads. */
    pool: `${API_V1}/admin/identities/pool`,
    import: `${API_V1}/admin/identities/import`,
    byId: (id: string) => `${API_V1}/admin/identities/${encodeURIComponent(id)}`,
    test: (id: string) => `${API_V1}/admin/identities/${encodeURIComponent(id)}/test`,
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
  archive: {
    list: `${API_V1}/archive`,
    stats: `${API_V1}/archive/stats`,
    export: `${API_V1}/archive/export`,
    recheck: `${API_V1}/archive/recheck`,
    backfill: `${API_V1}/archive/backfill`,
    /** Removes the archive rows and, unless told otherwise, the stored files. */
    delete: `${API_V1}/archive/delete`,
    collections: `${API_V1}/archive/collections`,
    collection: (id: string) => `${API_V1}/archive/collections/${encodeURIComponent(id)}`,
    collectionItems: (id: string) =>
      `${API_V1}/archive/collections/${encodeURIComponent(id)}/items`,
    /** POST, not DELETE: it carries a body, and bodies on DELETE get dropped. */
    collectionItemsRemove: (id: string) =>
      `${API_V1}/archive/collections/${encodeURIComponent(id)}/items/remove`,
  },
  watchlist: {
    list: `${API_V1}/admin/watchlist`,
    create: `${API_V1}/admin/watchlist`,
    pause: `${API_V1}/admin/watchlist/pause`,
    byId: (id: string) => `${API_V1}/admin/watchlist/${encodeURIComponent(id)}`,
  },
  downloads: {
    list: `${API_V1}/downloads`,
    create: `${API_V1}/downloads`,
    storage: `${API_V1}/downloads/storage`,
    pin: (id: string) => `${API_V1}/downloads/${encodeURIComponent(id)}/pin`,
    byId: (id: string) => `${API_V1}/downloads/${encodeURIComponent(id)}`,
    // A plain href the browser follows, so the session cookie authenticates it
    // the same way it does every other request from this page.
    file: (id: string, name: string) =>
      `${API_V1}/downloads/${encodeURIComponent(id)}/files/${encodeURIComponent(name)}`,
  },
  tools: {
    sign: `${API_V1}/tools/sign`,
    parseUrl: `${API_V1}/tools/parse-url`,
    /** The same recognition, over a pasted list. No network calls. */
    parseBatch: `${API_V1}/tools/parse-batch`,
    identity: `${API_V1}/tools/identity`,
  },
  endpointsHealth: `${API_V1}/admin/endpoints/health`,
  /** Which endpoints are served without a key, and which can never be. */
  endpointsAccess: `${API_V1}/admin/endpoints/access`,
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
    /** Channels themselves are the `notify.channels` setting, edited through `settings`. */
    test: `${API_V1}/admin/notifications/test`,
  },
  diagnose: `${API_V1}/admin/diagnose`,
  backup: {
    list: `${API_V1}/admin/backup`,
    create: `${API_V1}/admin/backup`,
    restore: `${API_V1}/admin/backup/restore`,
  },
  system: {
    status: `${API_V1}/system/status`,
    health: '/healthz',
    ready: '/readyz',
  },
  docs: {
    /**
     * The bare Swagger UI the api container serves, rendered per UI language.
     * Not /docs: that is this console's own reference page, so the button
     * beside it used to open the page the reader was already on.
     */
    swagger: '/swagger',
    openapi: '/openapi.json',
    redoc: '/redoc',
  },
} as const
