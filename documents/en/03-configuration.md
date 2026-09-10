# Configuration reference

After reading this you will know which settings need a restart and which do not, how to read and change the ones that do not from the console, the CLI or the API, and what every one of the 54 runtime settings does.

## The two layers

Configuration is split in two, and the split is not cosmetic.

| Layer | Lives in | Changed by | Takes effect |
| --- | --- | --- | --- |
| Bootstrap | Environment variables, normally `.env` at the repository root | Editing the file, then restarting the containers | On restart |
| Runtime | The `settings` table in Postgres | Console, `dtk config set`, or the admin API | Within seconds, no restart |

The reason the bootstrap layer cannot be folded into the database is `DTK_SECRET_KEY`: it is the master key that decrypts every stored cookie and proxy credential, so it can never live inside the thing it decrypts. `DTK_DATABASE_URL` and `DTK_REDIS_URL` have the same shape of problem — they are needed before the database is reachable at all. Everything that is *not* needed before the database exists was moved into the database on purpose, so that tuning an instance does not mean restarting it.

The registry of runtime settings is `RUNTIME_SETTINGS` in `src/dtk/core/config.py`. A key that is not in that dictionary cannot be stored: a typo becomes an error at the boundary instead of a config key nothing reads.

## Where a value actually comes from

At read time the order is short: **a row in the `settings` table, otherwise the built-in code default.** The environment is not consulted on the read path.

The environment gets exactly one chance to matter. When the first administrator account is created — the `/setup` flow, once per instance — every `RUNTIME` and `SENSITIVE` key that has a matching environment variable is copied into the `settings` table. The variable name is `DTK_` plus the key uppercased with dots turned into underscores:

| Setting key | Environment variable that seeds it |
| --- | --- |
| `cache.content_ttl` | `DTK_CACHE_CONTENT_TTL` |
| `security.request_proxy` | `DTK_SECURITY_REQUEST_PROXY` |
| `media.max_bytes` | `DTK_MEDIA_MAX_BYTES` |

After that pass the database is authoritative. This is the single most common surprise on a self-hosted instance: **you edit `.env`, restart, and nothing changes**, because the key already has a row and the row wins. The fix is to change the value where it now lives (any of the three ways below), or to reset the key so it falls back to the inherited value.

Two details worth knowing before you trust a listing:

- The console and the admin API report a `source` for every key — `database`, `environment` or `default` — precisely so this is visible rather than mysterious.
- `source: environment` means "there is no database row and this variable exists in the process environment". If you added the variable *after* the seeding pass, the value actually in force is still the code default. Setting the key through the console, the CLI or the API is what makes it real.

## Bootstrap settings

Read once at process start by `BootstrapSettings` in `src/dtk/core/config.py`. All are prefixed `DTK_`; the process also reads a `.env` file from its working directory, and the compose file passes the repository-root `.env` into every container.

| Variable | Default | What it does |
| --- | --- | --- |
| `DTK_SECRET_KEY` | none — required, minimum 32 characters | Master key for credential encryption. The image ships no default, and every role refuses to start without it. Changing it makes already-stored cookies and proxy credentials undecryptable. |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | Postgres DSN. Must be the asyncpg driver. |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | Redis URL. Queue, cache, token buckets and console sessions live here. |
| `DTK_BIND_HOST` | `127.0.0.1` | Address the API binds to. The container overrides this to `0.0.0.0`; what limits exposure there is the published port. |
| `DTK_BIND_PORT` | `8000` | Port the API binds to. |
| `DTK_LOG_LEVEL` | `info` | Log level, passed through to uvicorn. |
| `DTK_LOG_JSON` | `true` | Structured JSON logs. Set to `false` for human-readable lines in a terminal. |
| `DTK_BROWSER_RPC_URL` | empty | Where the headless browser service listens, e.g. `http://browser-rpc:9000`. Empty disables automatic identity minting; the pool then depends on manually imported cookies, which is a supported degraded mode, not an error. |
| `DTK_BACKUP_DIR` | `backups` | Where backup archives are written and listed from. The API and worker must agree: the worker writes an archive and the API serves it. The container image is read-only, so compose points this at a shared volume. |
| `DTK_DOWNLOADER_URL` | empty | Where the Go media downloader listens, e.g. `http://downloader:9100`. Empty disables media downloads entirely; `POST /api/v1/downloads` then answers 501 with an actionable message and nothing else is affected. |
| `DTK_DOWNLOADER_TOKEN` | empty | Optional shared secret for that sidecar. It listens on an internal network no other container is on, so this is defence in depth; empty means the sidecar checks nothing. |

Read directly by the process or by the container entrypoint, outside that class:

| Variable | Default | What it does |
| --- | --- | --- |
| `DTK_CONSOLE_DIR` | unset | Where the built console lives. The image sets it; a checkout falls back to its own build output. |
| `DTK_FORWARDED_ALLOW_IPS` | unset | Set it to the address of your reverse proxy, and the entrypoint starts uvicorn with `--proxy-headers --forwarded-allow-ips`. Without it `X-Forwarded-For` is ignored — unset, the header is attacker-controlled. A `*` is not a declaration: it makes the client address forgeable, and the code treats a wildcard as untrustworthy. |
| `DTK_WORKER_COMMAND` | unset | Overrides the worker command line. Only needed if the worker module is not importable under its usual name. |
| `DTK_COMMIT`, `DTK_GIT_COMMIT`, `GIT_COMMIT` | unset | Commit stamp reported by the system status endpoint, in that order of preference. |

Four more are read by compose itself rather than by the application: `DTK_IMAGE_TAG` (image tag, default `dev`), `DTK_REDIS_MAXMEMORY` (Redis ceiling, default `320mb`), and `CLOAKBROWSER_REPO` / `CLOAKBROWSER_COMMIT`, which are build arguments for the browser image rather than settings anything reads at runtime — see [Installation and deployment](./02-installation.md). `DTK_BIND_HOST` and `DTK_BIND_PORT` do double duty there — they also decide the published address. Compose interpolation reads `docker/.env` or the shell for all six of these, never the root `.env`, which is why a `CLOAKBROWSER_COMMIT` or a `DTK_IMAGE_TAG` written into the root file does nothing. To publish on all interfaces:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

See [Installation and deployment](./02-installation.md) for what to put in front of that port, and [Security](./15-security.md) for why loopback is the default.

## Sidecar environment

The two optional containers have their own environment, read once at start. Neither talks to the database, and compose blanks `DTK_SECRET_KEY` for both.

### browser-rpc (`DTK_BROWSER_*`)

From `docker/browser_rpc/settings.py`. Set these in the root `.env`; compose pins only the three that the container's own topology fixes.

| Variable | Default | What it does |
| --- | --- | --- |
| `DTK_BROWSER_BACKEND` | `cloak` | Which browser backend to drive. There is no fallback between backends: an unset value means `cloak`, never "whatever starts". |
| `DTK_BROWSER_BACKEND_PIN` | unset | Provenance string stamped in by the image and reported on `/rpc/health`. |
| `DTK_BROWSER_BIND_HOST` | `127.0.0.1` | Bind address. Compose pins `0.0.0.0` inside the container. |
| `DTK_BROWSER_BIND_PORT` | `9000` | Bind port. Compose pins `9000`. |
| `DTK_BROWSER_PROFILE_ROOT` | `/tmp/dtk-browser-profiles` | Parent directory for browser profiles. Compose pins `/profiles`, which is a tmpfs, so a single-use mint profile never touches a disk. |
| `DTK_BROWSER_WARM_CONTEXTS` | `1` | Warm signing contexts kept per platform. Each resident context costs roughly 300 MB of the container's `/tmp`, times two platforms — this is a RAM budget, not free space. |
| `DTK_BROWSER_WARM_REFRESH_SECONDS` | `1800` | How old a warm context may get before it is rebuilt. The platforms ship new JavaScript regularly and a stale page signs with stale code. |
| `DTK_BROWSER_PREWARM` | `true` | Prewarm at startup so the first signature does not pay for a cold page. |
| `DTK_BROWSER_MAX_CONCURRENT_MINTS` | `2` | How many identities may be minted at once. |
| `DTK_BROWSER_MINT_TIMEOUT_SECONDS` | `75` | Service-side budget for one mint. |
| `DTK_BROWSER_SIGN_TIMEOUT_SECONDS` | `8` | Service-side budget for one signature. |
| `DTK_BROWSER_CONTEXT_OPEN_TIMEOUT_SECONDS` | `45` | How long opening a browser context may take. |
| `DTK_BROWSER_SDK_READY_TIMEOUT_SECONDS` | `25` | How long a freshly opened page may take to become able to sign, counted after navigation. A page is navigable well before its security bundle has loaded, and signing in that window fails in a way that reads like an algorithm change. |
| `DTK_BROWSER_SIGN_PROXY_URL` | unset | Optional exit for the warm signing pages. Without it they load the platform's site from the container's own address. |
| `DTK_BROWSER_GEO_PROBE_URL` | `https://ipinfo.io/json` | Queried *through the identity's proxy* to learn where the exit actually is. Empty disables the probe, leaving the caller's geo hint as the only source of timezone and locale. |
| `DTK_BROWSER_GEO_PROBE_TIMEOUT_SECONDS` | `8` | Budget for that probe. |
| `DTK_BROWSER_DEFAULT_COUNTRY` | `US` | Two-letter code used when neither the caller nor the probe says where the exit is. |
| `DTK_BROWSER_HEADLESS` | `true` | Off only for debugging on a machine with a display. |
| `DTK_BROWSER_LOG_LEVEL` | `info` | Log level for the sidecar. |

### downloader (`DTK_DOWNLOADER_*`)

From `docker/downloader/main.go`. A value that is empty, unparseable or not positive falls back to the default.

| Variable | Default | What it does |
| --- | --- | --- |
| `DTK_DOWNLOADER_BIND` | `0.0.0.0:9100` | Listen address. Compose pins it to match `expose` and the healthcheck. |
| `DTK_DOWNLOADER_ROOT` | `/var/lib/dtk/media` | The one writable path. Compose pins it to the media volume. |
| `DTK_DOWNLOADER_TOKEN` | empty | Shared secret the sidecar expects; pair it with `DTK_DOWNLOADER_TOKEN` on the app side. |
| `DTK_DOWNLOADER_WORKERS` | `4` | Concurrent download jobs. |
| `DTK_DOWNLOADER_ITEM_WORKERS` | `4` | Concurrent files within one job. |
| `DTK_DOWNLOADER_QUEUE` | `64` | In-memory job queue depth. |
| `DTK_DOWNLOADER_HISTORY` | `500` | How many finished jobs it remembers. |
| `DTK_DOWNLOADER_MAX_REDIRECTS` | `5` | Redirect ceiling per transfer. |
| `DTK_DOWNLOADER_TIMEOUT_SECONDS` | `900` | Ceiling for one transfer. |

## Changing a runtime setting

Three surfaces, one registry, one validation path. Whichever you use, the value is coerced to the declared type, checked against the allowed choices where it has them, run through any extra validator, stored, and announced.

### In the console

**Settings** groups every key by prefix, and each row carries its key, its source tag, its default, its environment-variable name, and a control matched to its type. `sched.*` and `pool.*` are deliberately not here: they live on the **Scheduler** page, beside the pool they act on. `api.public_endpoints` also has a dedicated **Endpoint access** page, and `notify.*` a dedicated **Notifications** page — the same rows, edited where the explanation is.

A row whose source is `database` offers **Reset to inherited**, which deletes the override. A viewer sees the page read-only. The listing polls every 10 seconds, and an untouched row follows the server while a row you are typing in keeps what you typed.

### With `dtk config`

The CLI is the rescue path: it works when nobody can log in to the console. It needs the same environment the service does, so the simplest way to run it against a compose deployment is inside the API container.

```bash
# Every setting, with its current value, default, scope and description
docker compose -p dtk -f docker/compose.yml exec api dtk config list

# Only what differs from the built-in default
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed

# Only the keys that widen the attack surface
docker compose -p dtk -f docker/compose.yml exec api dtk config list --scope sensitive

# One value, as JSON
docker compose -p dtk -f docker/compose.yml exec api dtk config get cache.content_ttl

# Change one; lists are comma separated
docker compose -p dtk -f docker/compose.yml exec api dtk config set cache.content_ttl 3600
```

From a checkout with the environment already exported, drop the compose prefix: `uv run dtk config list`.

`--scope` accepts `bootstrap`, `env_only`, `runtime` and `sensitive`; only the last two match anything, because every key in the registry is one or the other. `dtk config set` on a sensitive key asks for confirmation first; `--yes` skips the prompt, which is what you want in a provisioning script and nowhere else.

Values that can carry a credential are masked on the way out — a settings dump is the single most likely thing to end up pasted into a bug report.

### Through the admin API

| Method and path | What it does |
| --- | --- |
| `GET /api/v1/admin/settings` | Every setting with `value`, `default`, `scope`, `type`, `choices`, `description`, `sensitive`, `source`, `env_var`, `masked`, `updated_at`, `updated_by`, plus the current settings `version`. |
| `PUT /api/v1/admin/settings/{key}` | Body `{"value": ..., "confirm": false}`. `confirm` is required for a sensitive key. |
| `DELETE /api/v1/admin/settings/{key}?confirm=` | Deletes the override so the key falls back to its inherited value. `confirm=true` is required for a sensitive key. |

Reaching these needs a console session or an API key with the `admin` or `identity:manage` scope. Changing a non-sensitive key additionally needs the `operator` role or higher — a viewer is refused. Changing a sensitive key needs the `admin` scope *and* the `admin` role *and* `confirm`.

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/admin/settings/cache.content_ttl \
  -H "Authorization: Bearer $DTK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"value": 3600, "confirm": false}'
```

Every write leaves an audit row (`settings.updated`, or `settings.updated_sensitive` for a sensitive key) recording the account, the previous value and the new one, both masked. See [REST API guide](./11-api.md) for authentication and the response envelope, and [Users and API keys](./09-users-and-api-keys.md) for roles and scopes.

## What SENSITIVE means

Seven keys are declared `SENSITIVE` rather than `RUNTIME`. The scope is not a styling variation — it means *widening this enlarges the attack surface*, and it changes who may write it and what it costs to do so.

| Key | What widening it exposes |
| --- | --- |
| `security.url_allowlist` | The SSRF boundary on short-link expansion. |
| `security.cors_allow_origins` | Which sites' JavaScript may call this API with a user's console cookie. |
| `security.cors_allow_credentials` | Whether those cross-origin calls may carry cookies at all. |
| `security.enable_task_webhook` | Whether a caller-supplied `callback_url` gets dialled by this server. |
| `security.webhook_secret` | The secret that makes those callbacks verifiable. |
| `security.request_proxy` | Whether an API key holder may pick this server's egress. |
| `api.public_endpoints` | Every entry removes a credential check from one endpoint. |

Six of them are `security.*`; the seventh, `api.public_endpoints`, sits in the `api` group but carries the same scope, because every entry in it removes a credential check.

A sensitive write requires all of:

- the `admin` scope on the credential, not merely `identity:manage` — on a self-hosted box nearly every key belongs to the administrator, so the role alone would not be a boundary;
- the `admin` role on the account;
- an explicit confirmation — `confirm: true` on the API, typing the key name in the console dialog, answering the prompt in the CLI;
- and it writes a distinct audit action.

Sensitive *string* values are also masked wherever they are shown. Submitting the mask back means "keep the stored value", and only that: the submitted mask has to be the mask of the value actually stored, so a caller cannot invent one and have a credential handed back to them. The same rule applies field by field inside `notify.channels`, and nothing is carried over into a record whose target host or URL changed.

## Types, and how a value is written

| Declared type | Console control | `dtk config set` | API body |
| --- | --- | --- | --- |
| `bool` | Switch | `true` / `false` (also `1`, `yes`, `on`) | JSON `true` / `false` |
| `int`, `float` | Numeric field | The number | JSON number |
| `str` with `choices` | Picker | One of the choices, case-insensitive | JSON string |
| `str` | Text field | The string | JSON string |
| `list` | Textarea, one entry per line | Comma separated | JSON array |

A wrong value is refused at the write, with the reason and — for a constrained key — the list of valid values. A value hand-edited into the table that no longer validates is *not* fatal: it is logged and the code default is used instead, which is the fail-closed direction for an allowlist.

Six keys carry extra validation beyond their type, under four rules:

| Key | Rule | Why the rule exists |
| --- | --- | --- |
| `capacity.warn_percent`, `capacity.hard_stop_percent` | Whole number, 1–99 | `0` would pause an instance the moment it started; `150` would mean the guard never fires — silently, which is the failure it exists to prevent. |
| `media.max_bytes`, `media.max_file_bytes` | `0`, or at least 1 MiB | For `media.max_bytes`, `0` means "no ceiling" and is a real choice; for `media.max_file_bytes` the validator accepts it but the downloader does not (see below). `1000` is refused for both: it would refuse every transfer, and it would do it as a per-file error rather than as "your setting is wrong". |
| `watchlist.min_interval_seconds` | At least 60 | Below a minute a schedule becomes a loop — the entry is re-queued before its previous run finished, and the pool spends itself on one target. |
| `security.url_allowlist` | Bare public hostnames; lowercased, deduplicated, sorted | A URL instead of a hostname, or a name that resolves to this machine or a private range, is refused *by name* rather than stored and silently ignored. |

## The complete runtime reference

All 54 keys, grouped by prefix. Defaults are exactly what the code declares.

### `sched` — scheduler (7)

Edited on the **Scheduler** page in the console. See [Concepts](./04-concepts.md) for what the scheduler and the circuit breaker do.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `sched.max_wait_seconds` | `10` | How long a task waits for a free identity before it fails with `IDENTITY_POOL_EXHAUSTED` (HTTP 503) and a `retry_after`. The lease is taken in the worker, so the submission was already answered with a task id before this timer starts. Also used as the `Retry-After` when the queue is full. | Raise it on a small pool where brief spikes are normal — waiting beats failing. Lower it to give up sooner and free the worker for the next task. |
| `sched.queue_max` | `500` | Maximum tasks allowed to queue. Beyond it the API rejects immediately with `Retry-After` instead of accepting work nobody will get to. | Lower it on a small pool, where a deep queue only means longer waits. `0` disables the check. |
| `sched.cooldown_base_seconds` | `60` | How long an identity rests after its first risk-control hit. Each consecutive hit doubles it. | Raise it if a platform is punishing your address harder than usual. |
| `sched.cooldown_max_seconds` | `21600` | Cooldown ceiling. An identity whose backoff reaches it is marked `degraded` instead of cooling again. | Lower it to recycle identities sooner, at the cost of poking a platform that is still angry. |
| `sched.circuit_risk_threshold` | `0.6` | Risk-control rate above which an endpoint is tripped, 0–1. | Lower it to give up on a failing endpoint sooner. |
| `sched.circuit_min_samples` | `20` | Minimum requests observed before an endpoint may trip, so a short burst of failures cannot open the circuit. | Raise it on a busy instance where 20 samples is noise. |
| `sched.circuit_min_identities` | `3` | How many distinct identities must be failing before an endpoint trips. Separates a broken endpoint from one broken identity. | Lower it to `1` only while debugging; it removes the distinction the setting exists to make. |

### `pool` — identity pool (4)

Also on the **Scheduler** page. See [Identities and proxies](./06-identities-and-proxies.md).

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `pool.min_size` | `3` | Low-water mark per platform. Dropping below it raises an alert and triggers minting. | Raise it if bursts routinely exhaust the pool; keep it at or below `target_size`. |
| `pool.target_size` | `8` | How many identities the pool tries to keep per platform. | Raise it for more throughput, at the cost of more minting and more proxies. Values below `min_size` are ignored. |
| `pool.max_fail_streak` | `3` | Consecutive failures after which an identity stops counting towards the pool level, so the filler mints a replacement instead of counting a session that serves nothing. The streak is used rather than the health score because the score is empty until there is traffic, and scoring a fresh identity as unusable would turn the low-water mark into a minting loop. | Rarely. Raise it if a flaky proxy is causing needless re-minting. |
| `pool.health_prior` | `0.8` | Assumed success rate for an identity with no history, 0–1. Decides how eagerly a freshly minted identity is scheduled. | Lower it if new identities are getting risk-controlled before they prove themselves. |

### `signing` — request signatures (3)

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `signing.mode` | `'native'` | Which signer produces the signature. `native` runs the in-process algorithms and never reaches for a browser. `rpc` always asks browser-rpc. `auto` starts native and crosses to the browser for endpoints the platform's own SDK signs, or once an endpoint's risk rate suggests the native signature is being rejected. | Switch to `rpc` the day a platform ships a new SDK and the in-process port has not caught up. `native` is the only coherent mode with no browser-rpc container. |
| `signing.fallback_enabled` | `True` | Whether the mode's non-preferred signer may be used when the preferred one is unavailable. | Turn it off to find out which signer is actually carrying traffic — with fallback on, a broken signer looks healthy because its traffic quietly moves to the other one. In `auto` the risk-driven switch *is* the fallback, so turning it off pins `auto` to native. |
| `signing.rpc_timeout_seconds` | `60` | Ceiling for one browser-rpc signing call. It has to cover a cold one: the first call for an identity launches a browser and loads the platform through that identity's proxy — measured at about 4s without a proxy — while later calls for the same identity are answered by the resident page in milliseconds. | Raise it if minting through slow proxies times out; raise `DTK_BROWSER_WARM_CONTEXTS` instead if the problem is cold starts. |

### `cache` — response cache (3)

Cached responses live in Redis, keyed per distinct request.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `cache.content_ttl` | `1800` | How long a parsed video detail stays reusable before it is fetched again. | Lower it if you need fresh counters; raise it to spend fewer identities on repeat lookups. Entries run 16–200 KB, so a long TTL costs Redis memory. |
| `cache.author_ttl` | `900` | How long a parsed user profile stays reusable. | Same trade-off; profiles move slower than posts. |
| `cache.list_ttl` | `300` | How long a parsed list page (author posts, comments, replies) stays reusable. | Lists change more often than a single item, so this is usually the shortest of the three. |

### `snapshot` — content history (1)

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `snapshot.min_interval_seconds` | `300` | Deduplication window for `content_snapshots`. A second snapshot of the same item inside this window is skipped rather than stored. | Lower it if you are deliberately sampling a viral post minute by minute; raise it if the history table is growing faster than the questions you ask of it. |

### `archive` — the content archive (5)

See [Downloads, library and watchlist](./08-downloads-and-library.md).

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `archive.enabled` | `True` | Keep parsed posts and authors in the database after the task result expires. | Turn it off to make the instance stateless beyond its logs — a post parsed today then cannot be looked up tomorrow. |
| `archive.store_raw` | `False` | Also keep each platform's untouched payload beside the normalized record, so a new field can be back-computed later without re-crawling. | Turn it on if you expect to re-derive fields. It is the largest single thing this instance can choose to store: the parsers produce one for every object. |
| `archive.recheck_after_days` | `7` | How old a post's last existence check may be before it is checked again. This is what makes "which of the things I saved are gone" answerable at all. `0` turns rechecking off. | Lower it if deletions matter to you; raise it to spend fewer requests on the question. |
| `archive.recheck_batch` | `25` | How many posts one recheck pass verifies. Each is a real request through the identity pool, so this knob decides what the answer costs. | Raise it on a large pool. Values above 200 are capped by the sweep itself. |
| `archive.recheck_pause_seconds` | `3` | Seconds between the individual requests a recheck or backfill makes. The scheduler paces per identity, which does nothing about a platform limit that counts requests per address. | Raise it if these passes are getting rate-limited. Nobody is waiting on them, so slow costs nothing. |

### `retention` — how long data is kept (6)

The two hypertable windows are enforced by TimescaleDB policies, re-applied by the worker's maintenance pass. `content_snapshots` never gets a retention policy: it is your accumulated data, and deleting it stays a manual, confirmed action.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `retention.request_log_days` | `14` | How long per-request log rows are kept. | Raise it if you investigate incidents days later; lower it on a small disk. Must be between 1 and 3650 — outside that range the policy is skipped and the error is logged. |
| `retention.identity_events_days` | `90` | How long identity state changes are kept. Long enough to explain why an identity was retired. | Same bounds as above. |
| `retention.task_days` | `90` | How long finished task rows are kept. | Lower it if the task table is the thing growing. |
| `retention.task_result_hours` | `24` | How long a task's result payload is kept. Shorter than the row itself, because the payload is the bulky part. | Raise it if callers poll for results long after submitting. |
| `retention.retired_identity_days` | `90` | How long a retired identity's row survives its retirement. The cookies are wiped at retirement; this is only the record of the identity having existed. | Lower it on an instance that churns identities quickly. |
| `retention.content_days` | `0` | Intended to delete archived posts older than this many days. `0` means never, which is the default — the archive exists precisely to outlive the platform. **No code currently reads this key**, so changing it has no effect; disk is protected by the capacity guard instead. | Leave it alone. |

### `watchlist` — scheduled collection (4)

The watcher ticks once a minute; entries carry their own intervals.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `watchlist.enabled` | `True` | Run scheduled collection. | Turn it off to stop submitting runs while leaving the entries and their history alone. |
| `watchlist.min_interval_seconds` | `900` | Shortest interval any watched target may be given. | Lower it (floor 60) only for a genuinely fast-moving target. A target collected every few seconds does not produce a better series, it spends the whole pool on one author. |
| `watchlist.default_interval_seconds` | `21600` | Interval a new entry gets when none is specified. Six hours means a watchlist of a hundred authors is about 400 requests a day. | Lower it if your default expectation is finer-grained. |
| `watchlist.batch_size` | `10` | How many due entries one tick may queue. The rest stay due and go on the next tick, so a large watchlist is spread over minutes rather than landing in front of whoever is using the API. | Raise it if a large watchlist never catches up; at the default, up to 600 runs an hour. |

### `media` — downloads (4)

Requires the `downloader` profile and `DTK_DOWNLOADER_URL`. Both ceilings are counted in bytes actually written — `Content-Length` and the platform's own `size_bytes` are claims, and neither is trusted.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `media.enabled` | `True` | Allow media downloads. Off refuses new jobs and leaves stored files alone; nothing is deleted by this switch. | Turn it off to freeze storage growth without losing what you have. |
| `media.max_bytes` | `2147483648` (2 GiB) | How much disk stored media may occupy. Past it the oldest unpinned downloads are removed until the volume is back under, and an alert says what went. Pinned downloads are never removed. `0` disables eviction entirely. | Raise it on a large volume. Set `0` only if you are managing the volume yourself. |
| `media.max_file_bytes` | `536870912` (512 MiB) | Ceiling for one file. A transfer that reaches it is refused and leaves nothing behind. Roughly twice the largest single video normally encountered. `0` is accepted by the validator but the downloader refuses any item without a byte ceiling (`item "…" has no byte ceiling`, answered as a 400), so `0` disables downloads rather than unlimiting them. | Raise it for long-form video — raise the number, never set `0`. |
| `media.mirror_max_age_seconds` | `600` | How old an archived media link may be before a download re-parses the post to get fresh ones. Signed CDN links expire within hours. | `0` always re-parses, which costs one request from the identity pool per download. |

### `capacity` — the disk guard (2)

Measured with `shutil.disk_usage` on the database and media paths, so it accounts for WAL, indexes, other containers and anything else sharing the volume. Nothing here deletes anything.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `capacity.warn_percent` | `80` | Disk usage percent at which this instance raises a warning. | Lower it on a volume you cannot grow quickly. |
| `capacity.hard_stop_percent` | `92` | Disk usage percent at which background collection and new download jobs stand down. Interactive API reads are never paused, and nothing is deleted — a full disk should degrade the instance, not take it offline or destroy what it collected. | Lower it if Postgres needs more headroom than 8% of the volume. |

### `api` — the HTTP surface (5)

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `api.default_rate_limit_per_min` | `120` | Requests per minute for one API key, as a fixed window. Abuse protection, not billing. A key with its own limit overrides this; `0` disables the check. | Raise it for a trusted internal caller — or better, set a per-key limit. Anonymous callers on an opened endpoint are counted by client address instead. |
| `api.max_wait_seconds` | `30` | Largest value a caller may pass to `?wait=`. A larger value is **rejected**, not silently clamped, so a caller that asked to block for five minutes learns that it cannot. | Raise it only if your reverse proxy's own timeout is higher. |
| `api.mcp_tool_timeout` | `60` | How long one MCP tool call may block before falling back to a task id. Floor of 1 second. | Raise it for agents that tolerate long calls. See [MCP and AI agents](./12-mcp.md). |
| `api.default_language` | `'en'` | Language used when a request specifies none. `en` and `zh` are supported; a request may always override with `?lang=` or `Accept-Language`. | Set `zh` for a Chinese-speaking team. An unsupported value falls back to `en`. |
| `api.public_endpoints` | `[]` **(sensitive)** | Endpoints served without an API key, each written as `<METHOD> <path>` exactly as the API document shows it, e.g. `GET /api/v1/{platform}/video`. Empty means everything needs a credential. `/api/v1/admin/*`, `/api/v1/auth/*` and `/api/setup/*` can never be opened, whatever is listed, and there is no override. | Open one endpoint for a public read-only mirror or an embedded link parser. An anonymous caller gets read scopes only and is metered by address. |

### `security` — the widening knobs (6, all sensitive)

See [Security](./15-security.md) for the reasoning behind each default.

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `security.url_allowlist` | `[]` | Extra hostnames a short link may redirect through. The built-in platform list always applies; each entry is matched as a whole hostname, belongs to no platform, and widens expansion — never routing. | Add one only to rescue a redirect chain that dies on an unlisted hop. Subdomains are not implied; a private or loopback name is refused. |
| `security.cors_allow_origins` | `[]` | Browser origins allowed to call this API. Empty means same-origin only, and no CORS headers are sent at all. | List your own front-end's origin. `'*'` would hand your API keys to any site a signed-in user visits. |
| `security.cors_allow_credentials` | `False` | Whether cross-origin requests may carry cookies. Only meaningful with an explicit origin list — with `'*'` the wildcard wins and credentials are dropped, because a browser would refuse the pair anyway. | Turn it on only for a first-party front-end on a named origin. |
| `security.enable_task_webhook` | `False` | Whether a task may call back to a caller-supplied `callback_url`. That URL is an SSRF vector. | Turn it on when you control every API key and want push instead of polling. Turning it off between submission and completion silently skips the callback, which is the safe direction. |
| `security.webhook_secret` | `''` | Shared secret used to sign task callbacks, sent as `X-Dtk-Signature: sha256=<HMAC of the exact body>`. | Set it whenever webhooks are on. Without it a receiver cannot tell a real notification from anyone who guessed the URL. Shown masked once stored. |
| `security.request_proxy` | `'deny'` | Whether a caller may pass `?proxy=` to route their request through their own egress. `deny` refuses the parameter outright — a disabled feature refuses rather than silently ignores, because dropping the parameter would send the request from this server's address while the caller believed otherwise. `public` allows only publicly routable addresses. `any` also allows loopback and private ranges. | `any` is only coherent when every API key is trusted with this instance's own network; it is the largest single widening available here. |

### `notify` — alerting (3)

Easiest to edit on the **Notifications** page, which validates a channel as you add it. See [Operations](./10-operations.md).

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `notify.enabled` | `False` | Master switch for outgoing alerts. Off means alerts are still recorded but nothing is sent. | Turn it on once you have a channel that works. |
| `notify.channels` | `[]` | Where alerts are delivered: a list of descriptors, each with a `type`, a `url` and optionally a `language`, `name` and `enabled`. Supported types are `webhook`, `bark`, `wecom`, `dingtalk`, `telegram` and `smtp`; `telegram` takes `token` and `chat_id` instead of a URL, and `smtp` takes `host`, `sender` and recipients. One malformed entry is skipped and logged rather than silencing the others. | Add or move a channel. Credentials inside are masked on read and preserved on write, but never carried over to a channel whose target changed. |
| `notify.language` | `'en'` | Language for alert text, for channels that do not specify their own. | Set `zh` for a Chinese-speaking on-call. |

### `system` (1)

| Key | Default | What it does | When you would change it |
| --- | --- | --- | --- |
| `system.check_updates` | `False` | Whether the console offers its update check. The request goes from your browser to GitHub, never from the server, and it is opt-in because some deployments have no egress at all. | Turn it on if you want to be told about releases. |

## When a change takes effect

A write bumps a version counter and publishes on a Redis channel. The API process that made the write reloads its snapshot immediately — a console that showed a stale value right after saving would read as a bug. Every other process reloads on the message, and, because pub/sub may drop messages across a reconnect, every process also polls the version counter every 30 seconds. Pub/sub is the fast path; the poll is the one that is guaranteed to converge.

The snapshot is replaced wholesale, never mutated field by field, so a request in flight keeps the values it started with.

You can see the version in the console header (`settings v<n>`), at the end of `dtk config list`, and in the `version` field of the settings API response. If two operators are editing at once, that number is how you tell whether you are looking at the current state.

Two changes do not follow this path: the retention windows are applied as TimescaleDB policies by the worker's maintenance pass, which runs every 5 minutes, and media eviction happens in the same pass.

## Limits the settings table cannot raise

Some numbers are constants in the code, deliberately. They are worth knowing because they bound what a setting can do.

| Limit | Value | Where |
| --- | --- | --- |
| Minimum master key length | 32 characters | `src/dtk/core/crypto.py` |
| Recheck batch ceiling | 200 posts per pass, whatever `archive.recheck_batch` says | `src/dtk/worker/ops/archive_sweep.py` |
| Retention window bounds | 1–3650 days for the two hypertable policies | `src/dtk/ops/retention.py` |
| Snapshot compression | Applied after 7 days | `src/dtk/db/models.py` |
| Watchlist tick | Every 60 seconds | `src/dtk/worker/watcher.py` |
| Availability sweep | Queued every 6 hours | `src/dtk/worker/watcher.py` |
| Maintenance pass | Every 5 minutes | `src/dtk/worker/maintenance.py` |
| Pool filler tick | Every 60 seconds | `src/dtk/worker/pool_filler.py` |
| Signing risk thresholds | 0.6 over 20 samples, independent of `sched.circuit_*` | `src/dtk/signing/registry.py` |

## Where to go next

- [Installation and deployment](./02-installation.md) — writing `.env` and starting the stack.
- [Operations](./10-operations.md) — alerts, backups and what to watch.
- [Security](./15-security.md) — the reasoning behind every sensitive default.
- [Troubleshooting](./14-troubleshooting.md) — including "I changed a setting and nothing happened".
- [CLI reference](./13-cli.md) — the rest of the `dtk` commands.
