# Console overview

After reading this you will be able to get into the console for the first time, find your way around its sidebar, and read the four pages that tell you whether the instance is healthy — Overview, System, Logs and Diagnose — including what every panel on them means and what you should do about what it shows.

## What the console is

The console is a single-page React application that the **api** container serves itself. It is not a separate service, has no port of its own, and shares an origin with the API, which is why the session cookie works for both. With the default compose file the whole thing lives at `http://127.0.0.1:8000/`.

```bash
docker compose -p dtk -f docker/compose.yml up -d
# then open http://127.0.0.1:8000/
```

A few consequences of being served this way are worth knowing before you go looking for a bug that is not there:

- **The router lives in the browser.** Any path the API has not claimed falls back to `index.html`, so deep links and reloads work on every console page.
- **The API keeps its prefixes.** `/api`, `/healthz`, `/readyz`, `/swagger`, `/redoc`, `/openapi.json` and `/mcp` are reserved and never answer with the console shell. A typo under `/api` returns a JSON 404, not a page.
- **`/docs` belongs to the console.** It is the console's own API reference page, which renders Swagger UI in your current interface language. The credential-free raw document is at `/swagger`.
- **In a development checkout there is no console at all.** The build output is copied into the image; if `web/dist` is absent every console route simply does not exist, and `npm run dev` proxies to the API process instead.

Everything the console does, it does through the same public REST API described in [REST API guide](./11-api.md). There is no private back channel: anything the console can show you, a script of yours can fetch.

## First run: the setup wizard

A fresh deployment has no account. That leaves a window in which whoever arrives first becomes the administrator, and the obvious fix — "only accept connections from private addresses" — does not work: Docker's userland proxy rewrites the source address to the bridge gateway, so every request on earth looks like `172.17.0.1`. The gate is therefore a one-time token that only exists in the container log.

On startup, while the `users` table is still empty, the api container prints a banner:

```text
==========================================================================
  dtk is not initialized yet.
  Open this URL to create the administrator account:

    http://127.0.0.1:8000/setup?token=...

  The token expires in 24 hours.
  To issue a new one: docker compose restart api
==========================================================================
```

The banner's last line is shorthand and will not run as printed from the
repository root: there is no `compose.yml` there, only `docker/compose.yml`.
Reissue with the full form, the one in the Reissue row below —
`docker compose -p dtk -f docker/compose.yml restart api`.

Read it with:

```bash
docker compose -p dtk -f docker/compose.yml logs api
```

Facts about the token, all enforced server-side:

| Property | Value | Why |
| --- | --- | --- |
| Lifetime | 24 hours from the moment the token was issued — a restart re-announces the same token without extending it | Long enough to find the log line, short enough that an abandoned deployment does not stay claimable |
| Storage | Redis only | It never reaches the database, never touches disk, and never appears in an HTTP response |
| Wrong attempts allowed | 4 | The fifth failure deletes the token; guessing 32 random bytes is not the threat, a script hammering the endpoint is |
| Reissue | `docker compose -p dtk -f docker/compose.yml restart api` | An unexpired token is reused and re-announced on restart, so a restart does not invalidate a link you are already holding |
| After an account exists | Permanently closed | `POST /api/setup/init` answers 409 without ever looking at the token |

While the instance is uninitialized, every console route redirects to `/setup`. If the status call itself fails with a network error the console **fails open** and shows you a retry panel with the language and theme switchers on it, rather than locking you out of the pages that would explain the problem.

The wizard has four steps, and only the first is mandatory.

| Step | What it does | Skippable |
| --- | --- | --- |
| 1. Create an administrator | Consumes the token, creates the first `admin` account, then immediately signs you in with the credentials you just typed (the init call itself issues no session) | No |
| 2. Add a proxy | Pastes a proxy list, imports it, then probes every imported proxy in turn and shows exit country and latency per row | Yes |
| 3. Mint the first identity | Mints 1–5 identities on a chosen platform through the headless browser | Yes |
| 4. Smoke test | Runs a real link you supply through `POST /api/v1/parse` end to end and shows platform, content id, duration and request id | Yes |

Details that catch people out:

- **The username rule is stricter than it looks.** 3 to 64 characters, letters, digits, dot, dash or underscore, and it must start with a letter or a digit.
- **The wizard asks for a 12-character password; the server floor is 8.** The extra four characters are the wizard's own policy, because this is the account with no email recovery.
- **A wrong token tells you how many attempts remain.** The server returns `SETUP_TOKEN_INVALID` with `attempts_remaining`, and the wizard prints it.
- **Step 2 imports and probes as one action.** A proxy list that was accepted but never dialled is exactly the failure this step exists to catch. Accepted line formats are `host:port`, `host:port:user:pass`, `user:pass@host:port` and `scheme://user:pass@host:port`.
- **Step 3 disappears if you have no browser container.** Minting drives a real browser, and the browser is behind an optional compose profile. When `browser_rpc` reports `configured: false`, the step says so and offers the command instead of failing:

  ```bash
  echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
  CLOAKBROWSER_COMMIT=<40-char sha> \
    docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
  ```

  The console prints only the `docker compose` line, and the two things it
  leaves out are both load-bearing. `CLOAKBROWSER_COMMIT` is the build argument
  that puts a browser in the image at all — it defaults to empty, and an image
  built without it starts, reports healthy and mints nothing; `.env.example`
  carries the revision this repository is verified against. `DTK_BROWSER_RPC_URL`
  is how `api` and `worker` learn the container exists. See
  [Installation](./02-installation.md).

  Running without it is supported — you import cookie jars by hand from the Identities page. See [Identities and proxies](./06-identities-and-proxies.md).
- **Step 4 uses your link, not a built-in one.** This is the opposite of the Diagnose page's step 6, which uses a fixed public link the server ships. If it fails, the wizard points you at Diagnose, which runs the same pipeline one stage at a time and names the stage that broke.

## Signing in

`/login` is the console's only entry point. There is no OAuth, no magic link and no registration form.

- **Every credential rejection reads the same.** "No such user" and "wrong password" are different facts, and telling them apart hands an attacker a free account-enumeration oracle. The only failures phrased differently are the ones you can act on: a lockout (with the seconds remaining) and an unreachable API.
- **Failures are counted per account and per address.** Five failures on one username lock that account for 15 minutes. The per-address counter is much higher (20) and only binds when the address actually identifies one caller — behind a TLS terminator or Docker's published-port proxy every login shares one peer address, and refusing there would let twenty junk attempts from a stranger shut the only administrator out of their own console.
- **A successful sign-in sets a session cookie valid for 7 days**, `HttpOnly`, `SameSite=Lax`, and `Secure` only when the request actually arrived over HTTPS.
- **There is no password reset by email.** Recovery is a command on the host that runs the api container:

  ```bash
  docker compose -p dtk -f docker/compose.yml exec api dtk user passwd <username>
  ```

The login screen carries the language and theme switchers, plus outbound links to the source repository and the licence — someone who lands on a login they did not expect should be able to find out what it is without holding an account for it. It deliberately does **not** link to the console's own About page, which needs a session and would bounce them straight back here.

## The shell

Every page inside the console sits in the same frame: a sidebar on the left, a sticky top bar, and a content column capped at 1440px.

### Sidebar

240px wide, collapsible to a 56px icon rail, and the collapsed state is remembered in this browser. Below 1280px it collapses to icons on its own; below 768px it disappears and becomes a drawer opened from the top bar. There is a "Skip to content" link for keyboard users, and the wordmark at the top is a link home.

Navigation is grouped by what you reach for during an incident rather than by the order anything was built.

| Group | Pages |
| --- | --- |
| Monitor | Overview (`/`) |
| Pool | Identities (`/identities`), Proxies (`/proxies`), Scheduler (`/scheduler`) |
| Tools | Playground (`/playground`), Building blocks (`/tools`), Library (`/library`), Watchlist (`/watchlist`), Downloads (`/downloads`), API docs (`/docs`), MCP (`/mcp-guide`) |
| Access | API keys (`/api-keys`), Endpoint access (`/endpoint-access`), Users (`/users`) |
| Operations | Logs (`/logs`), System (`/system`), Diagnose (`/diagnose`), Backup (`/backup`), Notifications (`/notifications`), Settings (`/settings`) |

Two things are pinned under the scrolling navigation rather than living in a group:

- **About** (`/about`) — the colophon: licence, copyright, links to the repository and issue tracker, and the donation addresses. It is a page in this console, so it reads as one more nav row.
- **The sponsor strip** — a labelled outbound link to the project's sponsor, hidden when the sidebar is collapsed to icons. Two deliberate choices here: the logo image is **served from your own instance**, never fetched from the sponsor's host, so opening the console does not report to a third party that your deployment exists; and the strip says "sponsor" out loud rather than posing as a feature. The link carries fixed campaign parameters that describe the project and the placement, never you, your instance or your visitors — every self-hosted copy sends exactly the same string.

The MCP entry points at `/mcp-guide` and not `/mcp`, because `/mcp` belongs to the MCP endpoint itself, mounted on the API. The endpoint's own path is `/mcp/` with the trailing slash; `/mcp` answers `307 Temporary Redirect` to it, so configure clients with the slash — see [MCP and AI agents](./12-mcp.md). `/parse` redirects to `/downloads`; the parse tool became a mode of that page and the old path was kept because it had been in the navigation for the life of v5.

### Which document covers which page

The sidebar is the map of the console. This is the map from the console back to
the documentation — every nav row, and the page that explains it.

| Page | Path | Documented in |
| --- | --- | --- |
| Overview | `/` | This page, [below](#overview) |
| Identities | `/identities` | [Identities and proxies](./06-identities-and-proxies.md) |
| Proxies | `/proxies` | [Identities and proxies](./06-identities-and-proxies.md) |
| Scheduler | `/scheduler` | [Identities and proxies](./06-identities-and-proxies.md) |
| Playground | `/playground` | [Playground and tools](./07-playground-and-tools.md) |
| Building blocks | `/tools` | [Playground and tools](./07-playground-and-tools.md) |
| Library | `/library` | [Downloads, library and watchlist](./08-downloads-and-library.md) |
| Watchlist | `/watchlist` | [Downloads, library and watchlist](./08-downloads-and-library.md) |
| Downloads | `/downloads` | [Downloads, library and watchlist](./08-downloads-and-library.md) |
| API docs | `/docs` | [Playground and tools](./07-playground-and-tools.md) |
| MCP | `/mcp-guide` | [MCP and AI agents](./12-mcp.md) |
| API keys | `/api-keys` | [Users and API keys](./09-users-and-api-keys.md) |
| Endpoint access | `/endpoint-access` | [Identities and proxies](./06-identities-and-proxies.md) |
| Users | `/users` | [Users and API keys](./09-users-and-api-keys.md) |
| Logs | `/logs` | This page, [below](#logs) |
| System | `/system` | This page, [below](#system) |
| Diagnose | `/diagnose` | This page, [below](#diagnose) |
| Backup | `/backup` | [Operations](./10-operations.md) |
| Notifications | `/notifications` | [Operations](./10-operations.md) |
| Settings | `/settings` | [Operations](./10-operations.md) |
| About | `/about` | This page, above |

### Top bar

| Element | What it is |
| --- | --- |
| Menu button | Opens the navigation drawer. Only visible below 768px |
| Breadcrumb | `Console / <current page>` |
| Host tag | The host you are actually connected to, from `window.location.host`. Hidden below 768px |
| Language switcher | English / 中文 |
| Theme switcher | Dark / Light / System |
| Account menu | Shows the signed-in username, and signs you out |

**Language.** The switch takes effect immediately without a reload, and it also changes the `Accept-Language` header the console sends, so server-rendered prose — settings descriptions, circuit-breaker reasons, task errors, the OpenAPI document — comes back in the same language. The resolution order is: `?lang=` on this navigation (or earlier in this tab) → your explicit stored choice → the language the server negotiated for the document → `navigator.language` → English. A `?lang=` link is remembered for the tab only and never persisted: a link from a colleague says which language *this visit* should be in, not what you want from now on.

**Theme.** Three modes, not two: "System" follows the OS. The choice persists in this browser.

Language, theme and sidebar state are all per-browser preferences held in local storage. Nothing about them is stored on the server, and clearing site data resets them.

## Conventions every page shares

Learning these once saves reading them four times.

- **Tables sort, hide columns and change density.** Every data table has a column menu and a density menu, and both choices are remembered per table in this browser. Several columns start hidden — they are listed per page below.
- **On a phone, tables become cards.** Columns marked as phone-hostile are dropped from the card view rather than squeezed.
- **Ids are click-to-copy.** Request ids, identity ids, proxy ids and commit hashes are rendered whole in a monospace face with a copy affordance, because half a UUID is worth nothing.
- **Status is colour *and* icon *and* text.** People paste these tables into chat, where only text survives, and screenshot them into issues, where colour shifts. The label is the payload.
- **Nothing animates on a refresh.** A row highlights only when its status genuinely changed, never on a poll — a table that fades every five seconds cannot be read.
- **Times are shown in your local zone; the server stores UTC.** Tables that deal in timestamps say so under the filters.
- **Pages poll rather than stream.** Pool state and anything you watch during an incident refreshes every 5 seconds, charts every 8, slow inventories every 10.
- **Your role gates what you can do, not what you can see.** A `viewer` can read every page in this document; running the self-check or changing a setting requires `operator` or `admin`. See [Users and API keys](./09-users-and-api-keys.md).

## Overview

`/` — **"Is this thing healthy right now, and what has it actually accumulated?"** This is the landing page and the one to leave open on a second monitor.

The page header carries the running version and, if the image was built with `DTK_COMMIT`, the build commit as a copyable value. Two selectors sit beside it:

| Selector | Options |
| --- | --- |
| Endpoint | All endpoints, or one specific endpoint. Populated from the endpoint health board |
| Time range | Last hour (60s buckets), Last 6 hours (300s), Last 24 hours (300s), Last 7 days (1800s) |

The bucket widths are not arbitrary: 24 hours at five minutes is 288 points, which draws instantly. A week at that resolution would not.

### Empty state

If the pool census is zero *and* no traffic was logged in the selected time range, the page replaces itself with "No traffic yet" and a button to the Identities page. This only fires when both are true, so a quiet hour on a working instance does not trigger it.

### Alerts

Alert banners appear above everything else, and only for conditions you must act on. An alert that fires on everything is an alert nobody reads.

| Alert | Tone | Meaning | What to do |
| --- | --- | --- | --- |
| No active identity | Danger | The pool has zero `active` identities. Requests are rejected with `IDENTITY_POOL_EXHAUSTED` until it recovers | Mint or import from the Identities page — the banner carries a button |
| *N* degraded identities | Caution | Those identities still serve traffic but fail more often than the rest | Investigate on the Identities page; a persistent degradation usually means a dead proxy or a stale cookie jar |
| One banner per open circuit | Danger | That endpoint is tripped. The banner names the endpoint, the reason and how long until it reopens | See the endpoint health table below |

Pool alarms are suppressed while the census is still loading or has failed. Without that, the page flashes a red "no usable identity" alarm on every load and parks one permanently whenever the status call errors.

### Metric tiles, first row — how the requests went

| Tile | Source | Colour rule |
| --- | --- | --- |
| Active identities | Pool census summed across platforms | Green above zero, red at zero |
| Requests | Total requests in the selected window, with a sparkline | — |
| Success rate | `ok` ÷ total in the window | Caution below 90%, green at or above it |
| Risk-control rate | `risk_control` ÷ total | Red whenever it is above zero |
| Average duration | Request-weighted mean duration | — |
| Open circuits | Count of tripped endpoints | Red above zero. Footer counts declared endpoints |

The 90% floor is one constant shared by the tile and the per-endpoint table, so the two cannot disagree about what "fine" means. A hardcoded green would paint a 12% success rate the same shade as a 99% one.

### Metric tiles, second row — what it kept

Every tile above is about requests. None of them says what the instance is accumulating, which is a different and more common question.

| Tile | Source | Colour rule |
| --- | --- | --- |
| Archived posts | Rows in the content archive. Footer: how many distinct authors | — |
| Media downloaded | Archived posts whose media is stored. Footer: of how many archived | — |
| Disk used | Measured from the media volume when the downloader answers, otherwise the stored-media total recorded in the database — the sum of the download rows, not the database size. Footer: the ceiling from `media.max_bytes` | Warning at 75% of the ceiling, danger at 90% |
| Downloading now | Jobs in flight. Footer: how many failed or partial | — |
| On the watchlist | Watchlist entries. Footer: how many are failing | Warning when any are failing |

Disk is quiet until it is nearly full, and loud once it is, because past the ceiling the oldest unpinned downloads start being evicted. See [Downloads, library and watchlist](./08-downloads-and-library.md).

### Pool state distribution

A proportional bar plus a count per state, across platforms: `minting`, `active`, `cooling`, `degraded`, `retired`. `cooling` is not a problem — it is the scheduler resting an identity on purpose. A pool that is mostly `degraded` or `retired` is. [Concepts](./04-concepts.md) explains the state machine.

### Charts

Three, all drawn from `request_log`:

1. **Success and risk-control rate** — share of requests per bucket, on a fixed 0–100% axis so the shape is comparable between windows.
2. **Request volume** — requests per bucket.
3. **Success rate by endpoint** — only shown when no single endpoint is selected and more than one endpoint has traffic. Capped at the six busiest endpoints in the window, because the categorical palette has six entries that clear a 3:1 contrast ratio in both themes and a line chart with more series stops being readable anyway.

### Endpoint health table

Every endpoint the platform adapters declare, whether or not it has traffic. Listing the quiet ones is deliberate: an endpoint missing from a board is exactly as invisible as one that was never called.

| Column | Meaning |
| --- | --- |
| Circuit | Closed or Open. The default sort puts open circuits first |
| Endpoint | The logical endpoint name, e.g. `douyin.content_detail` |
| Success rate | Over the breaker's rolling 300-second window. Caution below 90% |
| Risk-control rate | Over the same window. Red above zero |
| Samples | How many requests are in that window. A rate over 3 samples means very little |
| Identities hit | Distinct identities that saw risk control. Hidden by default |
| Reopens in | How long until a tripped endpoint is retried |

The numbers here come from Redis — the same rolling window the circuit breaker itself decides on — not from the charts above, which come from the request log. They answer different questions and will not always agree: the board is "usable right now", the charts are "what the last day looked like".

If this table is empty, the platform adapters reported no endpoints at all. That is a deployment problem, not an empty database.

### What to do about what you see here

- **Success rate down, risk rate up, one endpoint** → open [Logs](#logs) filtered to that endpoint with the `risk_control` outcome, and look at which identities are involved.
- **Success rate down across every endpoint** → this is usually egress, not the platform. Run [Diagnose](#diagnose).
- **Circuit open** → it will retry itself on a timer. Repeated tripping on one endpoint means the signature or the endpoint definition has drifted; the report from Diagnose is what to attach to an issue.
- **Pool empty or thin** → Identities page.
- **Disk tile in warning** → Downloads page, or raise `media.max_bytes` in [Configuration](./03-configuration.md).

## System

`/system` — **"What exactly is running here, and can it reach the things it needs?"** This is the page you screenshot into a bug report.

The header shows version and commit, and a Refresh button that forces a re-read rather than waiting for the poll.

### Tiles

| Tile | Meaning |
| --- | --- |
| Version | The running `dtk` version. Footer shows the runtime settings version, which increments on every settings change |
| Uptime | Since the api process started, from a monotonic clock so a clock adjustment cannot make it jump |
| Active identities | Total active across platforms |
| Database size | `pg_database_size` for the current database |

### Components

Reachability and round-trip time for `postgres`, `redis` and `browser_rpc`, **as measured by the server, not by your browser**. That distinction matters: a slow row here is a slow dependency, not a slow laptop.

| State | Means |
| --- | --- |
| Healthy | The probe answered. Latency is the round trip |
| Unhealthy | The probe ran and failed or timed out. The detail column carries `timeout` or the exception's type name, untranslated — and an unreachable `browser_rpc` reports no detail at all here. For the driver's own words, run Diagnose and read step 1 |
| Unknown | The component reported neither — in practice, `browser_rpc` with no URL configured. The detail column then says "Not configured" |

`browser_rpc` also reports its warm context count in the detail column when it is healthy. An unreachable `browser_rpc` is never a reason to take the instance out of rotation: minting is not on the request path, so `/readyz` deliberately ignores it and only requires Postgres and Redis.

### Chromium and wreq profile

Two numbers side by side: the Chromium major that `browser_rpc` reports, and the major of the TLS emulation profile the transport would actually use for a request from that browser. This card exists because their disagreement is a failure mode nothing else on the console would show — signatures keep being produced, they just stop matching what the platform expects, and you find out weeks later from a rising risk-control rate.

| State | Banner | What it means |
| --- | --- | --- |
| Both known and equal | Success | No drift |
| Both known and different | Caution | Version drift. Signatures are still produced but the fingerprint no longer matches the browser that made them. Pin the browser image or update the profile |
| Chromium unknown | Info | `browser-rpc` is not configured, or has not answered a health check yet |
| wreq profile unknown | Info | The installed `wreq` ships no Chrome emulation profile at all. Signing still works; the TLS fingerprint does not match the browser that produced it |

The two "unknown" messages are separate on purpose. One message covering both used to blame `browser-rpc` for a gap that was on the `wreq` side, while the row directly above displayed the Chromium major `browser-rpc` had just reported.

### Storage

Estimated row counts per table, with the database size in the card's description. The tables reported are `request_log`, `identity_events`, `content_snapshots`, `tasks` and `identities`.

Read "estimated" literally. `count(*)` on the request log is a full scan of the largest table in the system, far too expensive for a status endpoint that gets polled, and the three figures behind that word come from three different places: the hypertables `request_log`, `identity_events` and `content_snapshots` from TimescaleDB's chunk-aware estimate, `tasks` from the planner's `reltuples`, and `identities` from an exact `count(*)` — that table is small enough to afford one. A table whose estimate is genuinely unknown — a relation nobody has analysed, or a server without TimescaleDB — is **dropped from the table entirely** rather than shown with a blank cell, so a row that is missing is not a row reading zero. Use these numbers to size a retention policy, not to audit anything.

### Update check

**Off by default, and nothing leaves the machine unless you turn it on.** The toggle writes the runtime setting `system.check_updates`, so the choice survives a restart. A `viewer` cannot change it.

When enabled, "Check now" makes one request **from your browser to `api.github.com`** — never from the server. The result compares the latest release tag against your running version, treating `v5.0.0` and `5.0.0` as the same release. A deployment with no outbound access should leave this off; quietly calling home from an operations console is not a decision to make on an operator's behalf.

## Logs

`/logs` — **"What happened to this specific call, and who changed what?"** Two logs, deliberately kept apart, as two tabs.

A **Live** switch in the header refreshes the visible tab on a timer (5 seconds for requests, 10 for audit). Turn it off while you are reading a row — otherwise the table moves under you.

### Request log tab

High volume. One structured row per upstream request.

**Filters** — every field is optional, and an empty one matches everything:

| Filter | Notes |
| --- | --- |
| Request id | Exact match on a UUID |
| Endpoint | Exact logical endpoint name, e.g. `douyin.content_detail` |
| Identity id | Every request made with one identity |
| Time range | 15 minutes, 1 hour (default), 6 hours, 24 hours, 7 days, 30 days |
| Rows | 100 (default), 200, 500 |
| Outcome | Toggle buttons; several may be active at once. "All" clears them |

Two limits here are structural rather than cosmetic. Both the window and the row count are **mandatory** and neither has an "all" setting: an unbounded read of `request_log` is the one query on this API that can stall an instance. The server caps the window at 30 days and the row count at 500. The consequence worth knowing is that **a request id older than your window is simply not found** — widen the range rather than assuming the row is gone.

Retention is a separate ceiling: `retention.request_log_days` defaults to 14, so asking for 30 days is allowed and just returns less.

**The outcome vocabulary** is the single most useful thing on this page:

| Outcome | Colour | Means |
| --- | --- | --- |
| Success | Green | The platform answered and the answer parsed |
| No content | **Muted grey** | The platform explained itself: deleted post, private account, region lock. Not a system fault |
| Risk control | Red | The platform refused to explain — a challenge, an empty payload, a block. This is the one that costs you identities |
| Network error | Caution | The request never got a usable answer: DNS, TLS, proxy, timeout |

"No content" is muted on purpose. A deleted video is not a bug, and painting it red sends people hunting for one that does not exist.

**Columns** (hidden by default: Proxy, Reject reason, Cached, Signer, Platform):

| Column | Meaning |
| --- | --- |
| Timestamp | Local time |
| Outcome | The badge above |
| Endpoint | Logical endpoint name |
| Request id | Copyable. This is what you quote in an issue |
| HTTP status | As returned by the platform, when there was one |
| Duration | Wall time for the upstream call |
| Identity | Which identity served it |
| Proxy | Which proxy it left through |
| Error code | Stable machine code, never translated |
| Reject reason | Why the scheduler refused to send it at all — see below |
| Cached | Whether the answer came from cache |
| Signer | Which signing path produced the signature |
| Platform | douyin / tiktok |

A **reject reason** means the request never left the building. The scheduler declined it:

| Reason | Means |
| --- | --- |
| `circuit_open` | The endpoint's circuit was tripped |
| `no_identity` | No active or degraded identity for that platform |
| `no_token` | Every identity was out of quota in the token bucket |
| `all_inflight` | Every identity was busy |
| `queue_full` | The wait queue was full |
| `wait_timeout` | Gave up waiting for an identity |
| `pinned_unavailable` | A specifically named identity could not serve it |

A code this build has no translation for is shown exactly as it arrived — an untranslated reason is still the thing to quote in an issue; a missing-key stub is not.

Clicking a row opens a drawer with the full record, plus the raw JSON collapsed at the bottom. Nothing in a row is a credential: the fetch layer logs the *logical endpoint name* it scheduled on, never the signed URL it built, and identities and proxies appear only as ids.

### Audit log tab

Low volume, and kept out of the request log so it is never buried by ordinary traffic. It answers "who changed what".

Filters are just **Action** (an exact action name such as `api_key.created` or `settings.updated`) and **Rows**.

| Column | Meaning |
| --- | --- |
| Timestamp | Local time |
| Action | Dotted action name |
| Who | Username if known; otherwise the user id; otherwise the API key that did it; otherwise "System" |
| Target | Target type and id, e.g. `setting` + the key name |
| Source address | Client IP. Hidden by default |

The drawer shows the entry's detail payload. **Settings changes are re-masked on the way out**: a settings row records the old and the new value, and some settings hold credentials. Rows written before masking existed at the source are still in this table — nothing trims it — so they are masked again on read.

## Diagnose

`/diagnose` — **"I deployed it and I get no data. Which of the four places it could be, is it?"** The cause is normally in the proxy, the cookie jar, the signing path or the network, and guessing costs a round trip per guess. This page walks all of them and hands back one block of text.

Running it requires the `operator` or `admin` role. A `viewer` can open the page but the run will be refused.

### How a run works

Pressing **Run self check** issues `POST /api/v1/admin/diagnose`, which returns `202` with a task id rather than doing the work inline — a diagnosis dials proxies and makes real outbound requests, none of which belongs on an HTTP handler in the container that is supposed to stay responsive. The console then polls the task every 5 seconds until it settles, marking each step as its result lands. The task id is shown in the header, copyable.

One option: **Include the end-to-end fetch**, on by default. Turning it off skips step 6 and keeps the entire run local — which also means it spends no identity.

The 15-second timeout is a per-request network timeout on the steps that make outbound calls, not a deadline on a step: component probes in step 1 are bounded at 3 seconds each, and the end-to-end fetch in step 6 at 25. The six steps run one after another, so a slow step does hold up the rest — step 3 is the one to watch, because it dials at most 20 proxies, oldest first, at up to 15 seconds each.

### The report is redacted at the source

The whole point of this report is to be pasted into a public issue, so masking happens in the renderer rather than in whoever is pasting it. Proxy passwords, cookies, `Authorization` and `X-API-Key` headers, cookie and signature parameters (`sessionid`, `sid_guard`, `odin_tt`, `ttwid`, `msToken`, `a_bogus`, `X-Bogus`, `_signature`, `verifyFp` and friends) and the secret half of any `dtk_..._...` API key are all replaced with `[REDACTED]`. A proxy's *username* survives so the proxy stays identifiable; the password never does. An API key's public prefix survives so the report can still say which key was in play.

The **Copy report** button copies exactly the plain text the server rendered, not a reassembly by the browser.

### The six steps

Each step reports a stable code, and the sentence you read is rendered from that code in your language. Evidence — a driver's error message, an exception's text, per-step details — deliberately stays in English, because that is what a maintainer can search for.

| # | Step | What it actually does |
| --- | --- | --- |
| 1 | `components` | Round-trips Postgres, Redis and `browser-rpc` |
| 2 | `egress` | Fetches `https://www.douyin.com/` and `https://www.tiktok.com/` **directly, without a proxy** — this is how "no internet" is told apart from "bad proxy" |
| 3 | `proxies` | Dials up to the 20 oldest configured proxies (`MAX_PROXIES_PROBED`) and asks an echo service for the exit address, then reports exit IP, stored country and whether the row is marked healthy |
| 4 | `pool` | Counts identities per state, and reports when the last successful request was and how long the oldest active identity has been idle |
| 5 | `signing` | Signs one fixed request per platform with the native algorithm and compares it against `browser-rpc` — the same shadow comparison the signer registry runs in production |
| 6 | `smoke` | Pulls one fixed public link through resolve → sign → fetch → classify |

Note that step 6 uses a link the server ships, not one you supply — unlike the setup wizard's fourth step, which asks you for one. A post that has since been deleted or made private still **passes**: the platform explained itself, which is all this step is asking.

### Reading a result

Four statuses: **Pass**, **Warning**, **Fail** and **Skipped**. The run's verdict is the worst status any step reached — `PASS`, `WARN` or `FAIL`.

**A warning does not fail the run.** A warning is a step that ran, reached its answer, and wants you to know something: a pool running thin, a setting left at a development default. Folding warnings in with failures made the whole report read "verdict FAIL" over a note, and an operator told their instance failed while it is serving traffic stops reading the verdict at all.

**Skipped is not a failure either.** A step whose dependency is absent reports "not configured" rather than a red failure you cannot act on.

#### Step 1 — components

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `components_ok` | Pass | All three answered |
| `components_browser_rpc_down` | Warning | Postgres and Redis are fine; `browser-rpc` is not. Minting and the signing fallback are unavailable. Start the browser container, or import cookies by hand and stay on the native signer |
| `components_unreachable` | **Fail** | Postgres and/or Redis are unreachable. Check that those containers are running and that `DTK_DATABASE_URL` and `DTK_REDIS_URL` point at them. Nothing else in the report can be trusted while this is red |

#### Step 2 — egress

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `egress_ok` | Pass | Both platform domains reachable directly |
| `egress_partial` | Warning | One of the two is unreachable. Requests for that platform will depend entirely on a working proxy |
| `egress_unreachable` | **Fail** | No route to either. Check DNS, the host firewall, and whether outbound traffic on this host has to go through a proxy |

#### Step 3 — proxies

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `proxies_ok` | Pass | Every proxy that was probed answered. That is up to 20, oldest first — on a larger pool the untested remainder is neither passed nor reported |
| `proxies_none` | Warning | No proxies configured. Every identity shares this machine's egress IP — fine for a first look, a correlation risk for a busy pool |
| `proxies_partial` | Warning | Some answered, some did not. Retire or replace the failures; identities bound to them will keep failing |
| `proxies_all_failed` | **Fail** | None answered. Check credentials and whether the provider still allows this machine's IP. Identities bound to a dead proxy cannot recover on their own |
| `proxies_no_session` / `proxies_unreadable` | Skipped | The proxy table could not be read — look at step 1 first |

#### Step 4 — pool

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `pool_ok` | Pass | At or above the low-water mark |
| `pool_below_minimum` | Warning | Fewer active identities than `pool.min_size` (default 3). Mint more, or lower the setting if this is deliberate |
| `pool_empty` | **Fail** | No active identity at all. Every request is rejected or queued until you mint or import one |
| `pool_no_session` / `pool_unreadable` | Skipped | The identity table could not be read — look at step 1 first |

#### Step 5 — signing

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `signing_ok` | Pass | Native and browser signatures agree on both platforms |
| `signing_not_comparable` | Warning | The browser reference implements a different version of the algorithm, so a structural comparison says nothing either way. **The native path is unaffected and still serving requests.** This reads like a fault and is not one |
| `signing_mismatch` | **Fail** | The native algorithm has drifted from the platform's. Those platforms now sign through `browser-rpc`; open an issue with this report attached so the algorithm can be updated |
| `signing_no_browser_rpc` | Skipped | No `browser-rpc` configured, so there is no second signer to compare against. Setting `DTK_BROWSER_RPC_URL` enables the shadow comparison, which catches a stale algorithm before the risk rate does |
| `signing_no_registry` | Skipped | No signer registry was supplied to the run |

A partial comparison is reported as a warning even when only one platform could not be compared. "Native and browser signatures agree" over one compared platform and one uncompared reads as evidence about both, and the one it says nothing about is exactly the one you need told.

#### Step 6 — smoke

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `smoke_ok` | Pass | A real link came back through the whole pipeline |
| `smoke_failed` | **Fail** | Read the failing step above first. A smoke failure is usually a symptom of the proxy, pool or signing step rather than its own fault |
| `smoke_not_configured` | Skipped | You turned the end-to-end fetch off |

#### Any step

| Outcome | Status | What it means and what to do |
| --- | --- | --- |
| `step_crashed` | **Fail** | The check itself raised. This is a bug in dtk — please report it with the report attached |

### The same check from a shell

The console and the CLI run identical code, so if the console is what is broken you are not stuck:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --skip-smoke
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --json
```

The CLI additionally accepts `--platform`, `--smoke-url`, `--probe-url` and `-o/--output`. See [CLI reference](./13-cli.md).

## Which page answers which question

| Question | Page |
| --- | --- |
| Is anything on fire right now? | Overview |
| Which endpoint is failing, and how badly? | Overview → endpoint health table |
| What happened to request `0c9f…`? | Logs → request log, filter by request id |
| Why did the scheduler refuse to send it? | Logs → the Reject reason column |
| Who changed this setting, and when? | Logs → audit log |
| What version am I running, and can it reach Postgres? | System |
| Are the browser and the TLS profile in step? | System → Chromium and wreq profile |
| How big is the database getting? | System → Storage |
| I deployed it and nothing works | Diagnose |
| What do I attach to a bug report? | Diagnose → Copy report |

## Where to go next

- [Configuration](./03-configuration.md) — every setting these pages read, and where each one comes from
- [Concepts](./04-concepts.md) — identities, the scheduler, the circuit breaker and the outcome vocabulary
- [Identities and proxies](./06-identities-and-proxies.md) — the pages the Overview alerts send you to
- [Playground and tools](./07-playground-and-tools.md) — the Playground, Building blocks and API docs pages
- [Users and API keys](./09-users-and-api-keys.md) — roles, and what each one may do in the console
- [Operations](./10-operations.md) — the Settings, Backup and Notifications pages, retention and running this thing over time
- [MCP and AI agents](./12-mcp.md) — the MCP page, and the endpoint it documents
- [Troubleshooting](./14-troubleshooting.md) — symptom-first, when you already know what broke
