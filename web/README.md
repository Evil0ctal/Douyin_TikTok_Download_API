# dtk console

React SPA for the dtk v5 API. Built to static files and served by the api
container - no Node runtime in production, no SSR.

Specs this implements: [`docs/design/07-frontend.md`](../docs/design/07-frontend.md)
(what the pages are), [`docs/design/12-design-system.md`](../docs/design/12-design-system.md)
(what they look like - authoritative for anything visual),
[`docs/design/14-i18n.md`](../docs/design/14-i18n.md) (language rules) and
[`docs/design/06-api-auth-mcp.md`](../docs/design/06-api-auth-mcp.md) (the API).

## Commands

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm run build      # typecheck, then emit web/dist
npm run preview
npm run typecheck
npm run lint       # eslint + locale parity/ICU/error-code coverage
npm run verify     # all three, in the order CI runs them
```

`DTK_API_TARGET` overrides the dev proxy target. `VITE_API_BASE_URL` is only
needed when the console is served from a different origin than the API.

## Layout

```
src/
  main.tsx            theme + i18n bootstrap, root render
  App.tsx             shell, router, setup gate
  lib/                api client, formatting, i18n, theme, tokens of behaviour
  components/         the design-system inventory (import from '@/components')
  hooks/              shared hooks (import from '@/hooks/...')
  locales/{en,zh}/    common | console | errors | setup
  pages/              one file per route
  styles/             tokens.css, base.css, utilities.css
```

`@/` resolves to `src/`. TypeScript runs strict plus `noUncheckedIndexedAccess`
and `verbatimModuleSyntax`, so index access is `T | undefined` and type-only
imports need `import type`.

## Non-negotiables

These are enforced by lint or by review; do not work around them.

1. **No colour literals outside `styles/tokens.css`.** `local/no-raw-hex-color`
   fails the build on a bare hex in any source file. Use `var(--accent)`,
   `var(--danger)`, `var(--bg-raised)`. Both themes are complete: anything you
   add must be readable on `#0B0D10` and on `#FBFCFD`.
2. **No CJK in `.ts`/`.tsx`.** `local/no-cjk-source` covers comments, strings and
   identifiers. Chinese lives in `src/locales/zh/*.json` only.
3. **Every user-visible string goes through `t()`**, and `en`/`zh` key sets must
   match exactly - `npm run lint` fails otherwise. Never translate error codes,
   state enum values, field names, endpoint paths or config keys; translate their
   *display labels* instead.
4. **Status is colour + icon + text.** Use `<StatusBadge kind="identity" value={row.state} />`
   rather than colouring a cell. `business_error` renders muted, never red: a
   deleted video is not a system fault.
5. **Ids and JSON are mono.** `<CopyableId>` for any id, `mark columns mono`, and
   `<CodeBlock json={...}>` for payloads.
6. **Polled values never animate.** Pass `flashValue` to `DataTable` so a row
   highlights once when its *status changes*; do not add transitions on refresh.
7. **Focus rings stay.** No `outline: none` without a visible replacement.
   Everything must be reachable and operable with the keyboard, and overlays must
   close on Esc.

## Calling the API

```ts
import { apiGet, apiPost, apiRequest, isApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { useApiQuery, useApiMutation } from '@/hooks'
import { POLL } from '@/lib/query'

// Query with polling; errors arrive as ApiError.
const identities = useApiQuery<Identity[]>({
  key: ['identities', filter],
  path: paths.identities.list,
  params: { state: filter },
  poll: POLL.fast,          // 5s. Use POLL.normal / POLL.slow, do not invent one.
})

// Meta (cursor, request_id, duration) when you need it:
const { data, meta } = await apiRequest<RequestLogRow[]>(paths.logs.requests)
```

The client handles the async-first protocol for you: a `202 {task_id}` is polled
against `/api/v1/tasks/{id}` and the promise resolves with the finished result.
Pass `awaitTask: false` to get the raw task id (batch submits do their own
orchestration), or use `waitForTask` / `openTaskEvents` directly.

It also sends `Accept-Language` from the current UI language, records the
`X-RateLimit-*` and `Retry-After` headers (`getRateLimit()`), and routes a
401 to the login page.

Failures: `error.code` is the stable enum (`ApiErrorPayload` is a discriminated
union, so `switch (error.payload.code)` narrows), `error.kind` separates an API
failure from network, timeout, aborted and malformed. Render one with
`<ErrorState error={query.error} onRetry={query.refetch} />` or
`toast.apiError(error)`; both show the code, the localized message, the hint and
the `request_id`.

## Formatting

```ts
const f = useFormatters()   // bound to the active language
f.compact(12800)            // en: 12.8K   zh: 1.28万  (Intl, not a translation)
f.number(1234)  f.percent(0.982)  f.bytes(1234567)
f.timestamp(row.ts)  f.relative(row.ts)  f.duration(ms)  f.latency(ms)
f.timeZone()                // { iana, short } for chart axes
```

Missing values render as an em dash. `null` and `0` are different facts - never
coerce one into the other.

## Components

`Button` `Input` `Select` `Textarea` `Checkbox` `Switch` `Field` · `StatusBadge`
`ErrorCodeBadge` `CopyableId` `MaskedSecret` · `Card` `PageHeader` `MetricTile`
`EmptyState` `ErrorState` `Skeleton` · `DataTable` `CodeBlock` `TimeSeriesChart`
· `Modal` `Drawer` `ConfirmDialog` `Toast` `Menu` · `Sidebar` `TopBar`
`ThemeToggle` `LanguageSwitcher` `ErrorBoundary`.

`DataTable` gives you sorting, a column picker, a density toggle, a sticky
header, skeleton, empty and error states, optional selection, and degrades to
cards under 768px. Give it a `storageKey` so the viewer's column and density
choices persist.

`TimeSeriesChart` is hand-rolled SVG. Colour series from the status palette:
success rate `var(--success)`, risk rate `var(--danger)`, volume `var(--accent)`,
categorical comparisons `var(--series-1..6)`.

## Pages

`src/pages/*.tsx` are scaffold placeholders that render a `PageHeader`. Replace
the body, keep the `console:page.<name>.*` keys, and add page-specific keys to
`locales/{en,zh}/console.json` in the same commit. Routes live in `App.tsx` and
navigation entries in `lib/nav.ts`.

## Setup gate

`App.tsx` reads `GET /api/setup/status`. While `initialized` is false, every
route redirects to `/setup`. If that call fails at the transport level the
console fails *open* rather than locking the operator out of the pages that would
explain the outage - only a hard network error renders a full-page error.

## Responsive

`< 768px` sidebar becomes a drawer and tables become cards; `768-1280px` the
sidebar is icons only; `>= 1280px` the full 240px sidebar with a 1440px content
column. The console must stay usable on a phone: that is where the 3am alert
lands.
