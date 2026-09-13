# Operations

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/10-operations.md)

This page is about day two and everything after it: keeping an instance healthy for months without babysitting it. After reading it you will know what the background loops do, how a settings change reaches a running process, how to take and restore a backup (and prove the restore works), how alerting is wired, what gets pruned and when, what to watch, and how to upgrade without losing anything.

Everything here assumes the compose deployment from [Installation and deployment](./02-installation.md). Commands are run from the repository root and always name the compose project:

```bash
docker compose -p dtk -f docker/compose.yml ps
```

## What runs in the background

Almost all day-two behaviour comes from four loops inside the `worker` container. They are the reason an unattended instance stays usable, and they are the first place to look when it stops being usable.

| Loop | Interval | What one tick does |
| --- | --- | --- |
| `pool_filler` | 60s | Counts identities per platform. Mints when the usable count is under `pool.min_size`, until it reaches `pool.target_size`. Raises `pool_empty` / `pool_below_min`. |
| `proxy_prober` | 300s | Probes every proxy row, writes back health and exit address, cools identities behind a failed proxy for 900s. Raises `proxy_unhealthy`. |
| `maintenance` | 300s | Nine jobs, listed below. |
| `watchlist` | 60s | Queues watchlist entries that are due, at most `watchlist.batch_size` per tick; queues an availability recheck every 6 hours. |

All four share one loop implementation (`src/dtk/worker/loop.py`) with three properties worth knowing:

- **A raised exception never ends a loop.** It is logged as `worker.loop.failed` with a `consecutive_failures` count, and the next tick runs anyway.
- **Failures back off.** The interval is multiplied by `2^failures`, capped at 8× the configured interval. A job whose dependency is down stops hammering it without ever giving up. Recovery logs `worker.loop.recovered`.
- **Ticks are jittered ±10%,** so several worker replicas do not fire the same job at the same instant.

One `maintenance` tick runs these jobs in order, and one failing job never stops the others:

| Job | What it does |
| --- | --- |
| `apply_retention` | Re-applies the retention windows, blanks aged task payloads, deletes aged task rows, applies snapshot compression. |
| `purge_retired_identities` | Deletes identity rows retired longer than `retention.retired_identity_days`. |
| `requeue_stale_tasks` | Gives back tasks that have been `running` for more than 900s (at most 200 per pass). |
| `requeue_orphaned_tasks` | Re-publishes tasks `queued` for more than 300s with no entry on the Redis queue. |
| `refresh_aggregates` | Refreshes TimescaleDB continuous aggregates that have no refresh policy, over the last day. |
| `warn_expiring_sessions` | Raises `cookie_expiring` for imported logged-in identities expiring within 3 days. |
| `check_capacity` | Measures the disk, raises `capacity_warning` / `capacity_paused`. Deletes nothing. |
| `enforce_media_ceiling` | Evicts stored media over `media.max_bytes`, oldest first, never pinned. Raises `media_evicted`. |
| `fail_stale_downloads` | Settles downloads still claiming to be in flight after 7200s. Touches no file. |

The whole pass logs one `worker.maintenance.done` line with the counts. If you see that line every five minutes, the background half of the instance is alive.

## Settings and how a change takes effect

Runtime configuration lives in the database, not in `.env`. The console page is **Settings** at `/settings`, grouped by key prefix: signing, cache, snapshot, archive, media, watchlist, capacity, retention, api, security, notify, system, and `other` for anything with an unlisted prefix. Scheduler and identity-pool keys (`sched.*`, `pool.*`) are deliberately not here — they live on `/scheduler`, next to the pool they act on.

Every row names its **source**, which is the single most useful thing on the page:

| Source label | Meaning |
| --- | --- |
| Database override | A row exists in the `settings` table. It wins, and the `.env` entry is ignored until you reset the row. |
| .env seed | No database row exists and the matching `DTK_*` variable is present in the environment. |
| Code default | Neither the database nor the environment sets this key. |

There is a trap in that middle row. The environment is copied into the settings table exactly **once**, when the first administrator account is created (`POST /api/setup/init`). After that, the configuration loader reads the settings table and the built-in defaults and nothing else. So adding `DTK_CACHE_CONTENT_TTL=3600` to `.env` on an already-initialised instance and restarting will relabel the row as `.env seed` while the value actually in force is still the code default. On a live instance, change runtime settings in the console or with `dtk config set` — never by editing `.env`.

**How a change propagates.** Writing a setting validates and coerces it, stores it, bumps a single `settings_version` counter and publishes on the Redis channel `config:changed`. Every process — API, worker, MCP — subscribes to that channel and *also* polls the version counter every 30 seconds, because pub/sub can drop a message across a reconnect. So:

- the API process that served the write reloads immediately;
- every other process picks the change up on the next message, or within 30 seconds at the latest;
- the snapshot is replaced wholesale, never mutated field by field, so a request already in flight keeps the values it started with.

**Sensitive keys** (`security.url_allowlist`, `security.cors_allow_origins`, `security.cors_allow_credentials`, `security.enable_task_webhook`, `security.webhook_secret`, `security.request_proxy`, `api.public_endpoints`) need the admin role, an explicit confirmation — the console makes you type the key name — and they leave an audit row with your account and the previous value. See [Security](./15-security.md) for what each one widens.

**What a change does not reach until the worker restarts.** A handful of values are frozen into objects when the worker process builds them:

| Setting | Frozen into |
| --- | --- |
| `sched.max_wait_seconds` | the scheduler |
| `pool.health_prior` | the scheduler |
| `sched.circuit_risk_threshold`, `sched.circuit_min_samples`, `sched.circuit_min_identities` | the circuit breaker |
| `sched.cooldown_base_seconds`, `sched.cooldown_max_seconds` | the fetch service |

Everything else the worker uses — `notify.*` (channels included), `signing.*`, `retention.*`, `pool.min_size` / `target_size` / `max_fail_streak`, `capacity.*`, `media.*`, `watchlist.*`, `archive.*` — is read live. After changing one of the frozen four groups:

```bash
docker compose -p dtk -f docker/compose.yml restart worker
```

Bootstrap settings (`DTK_SECRET_KEY`, `DTK_DATABASE_URL`, `DTK_REDIS_URL`, `DTK_LOG_LEVEL`, `DTK_LOG_JSON`, `DTK_BROWSER_RPC_URL`, `DTK_DOWNLOADER_URL`, `DTK_BACKUP_DIR`, …) are environment-only and always need the container restarted. The full list is in [Configuration reference](./03-configuration.md).

From a terminal, the same three operations:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed
docker compose -p dtk -f docker/compose.yml exec api dtk config get retention.request_log_days
docker compose -p dtk -f docker/compose.yml exec api dtk config set retention.request_log_days 30
```

`dtk config set` announces the change on the same Redis channel the console uses, so a running deployment picks it up without a restart.

## What is in a backup, and what is not

An archive is a gzipped tar containing `manifest.json` and one JSON-lines file per table under `data/`, written with mode `0600`.

| Table | In a backup? | Why |
| --- | --- | --- |
| `users` | always | Console accounts, including password hashes. |
| `api_keys` | always | Key records (hashes, scopes, limits). |
| `proxies` | always | Encrypted proxy URLs. |
| `settings` | always | Everything you configured. |
| `content_snapshots` | always | Months of accumulated history, and the thing that cannot be re-fetched. |
| `identities` | only with `--include-identities` | Cookie jars bound to one proxy and one exit address. |
| `request_log` | never | Operational history of a run that is over. |
| `identity_events` | never | Same. |
| `tasks` | never | In-flight work. |
| `audit_log` | never | The record of actions taken against the instance being replaced. |
| `settings_version` | never | A change counter for running processes, not user data. |

Two properties decide how you must handle archives:

**Credentials are exported as ciphertext, and the archive never holds the master key.** Proxy URLs and cookie jars are copied byte-for-byte out of their `bytea` columns; nothing is decrypted on the way out. Credential fields inside `settings` — a Telegram bot token, a DingTalk signing secret, an SMTP password — are encrypted on the way out, which is what archive schema version 2 added. The manifest carries an HMAC fingerprint of the derived key, not the key: enough to say "this is a different key", not enough to guess it. **Restoring requires the same `DTK_SECRET_KEY`.** Losing that key makes every archive undecryptable; back it up somewhere the archive is not.

**Identities are excluded by default,** and that is the correct semantics rather than a shortcut. An identity is a cookie jar bound to one proxy and one egress IP. After a move the egress has changed, and replaying those cookies from a new exit address is exactly what gets them risk-controlled. Include them only when you are moving a deployment to a machine with the same egress. The console warns about this in the same words when you flip the switch.

Because the archive still holds settings — and `notify.channels` carries webhook URLs and tokens — an archive is a credential file. Store it like one.

## Creating a backup

**Nothing creates a backup on a schedule.** There is no periodic backup job, and scheduling is yours. Nor does every path alert: `backup_failed` fires only for a backup the worker ran — the console button and `POST /api/v1/admin/backup`. A `dtk backup create` that fails raises no alert; it prints the reason and exits non-zero, so a cron'd backup needs its own failure handling — check the exit status, or watch `dtk backup list`.

From the console, `/backup` → **Create backup**. Administrators only. The request is queued as a task and the worker writes it into the backup directory; the page polls and the new archive appears in the table. The **Include identities** switch is off by default and shows a warning when you turn it on.

From the API — the key must belong to an administrator and carry the `admin` scope:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/admin/backup \
  -H "X-API-Key: $DTK_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"include_identities": false}'
```

It answers `202` with a `task_id`; poll `/api/v1/tasks/{task_id}` for the file name, size and row count. See the [REST API guide](./11-api.md) for the task-polling pattern.

From a terminal — the path that still works when the worker is down, because the CLI does the export in its own process:

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
```

The `-o` is not optional inside the container. `dtk backup create` defaults to `backups` relative to the working directory, and the container's filesystem is read-only apart from the backup volume and `/tmp`, so the default fails. `/var/lib/dtk/backups` is the named volume `backup-data`, mounted into both `api` and `worker` — the API lists and serves what the worker writes, so both must name the same directory. Outside a container the default is fine: from a checkout, `dtk backup create` writes into `./backups`.

Archives are named `dtk-backup-<UTC timestamp>.tar.gz`, e.g. `dtk-backup-20260910T031500Z.tar.gz`. The export stages its tables in a hidden directory beside the archive and renames the finished file into place, so a reader never sees a half-written `.tar.gz`.

A daily cron job on the host, keeping 14 days:

```bash
15 3 * * * cd /srv/dtk && docker compose -p dtk -f docker/compose.yml exec -T api \
  dtk backup create -o /var/lib/dtk/backups >> /var/log/dtk-backup.log 2>&1
20 3 * * * docker run --rm -v dtk_backup-data:/b alpine \
  sh -c 'find /b -name "dtk-backup-*.tar.gz" -mtime +14 -delete'
```

`-T` is not decoration in that cron entry: cron has no terminal, and `docker compose exec` without `-T` tries to allocate a TTY and fails when there is none. Every other command on this page is typed at a terminal, so it uses plain `exec`; add `-T` whenever you script or pipe one of them.

If a worker-run backup fails — a full disk, a read-only filesystem, a database that went away mid-export — the `backup_failed` alert fires (once per 24 hours) and the reason is in the worker log. For the cron job above, the exit status is the equivalent signal. Either way, it is the only thing standing between you and discovering the problem during a restore.

## Listing archives and reading a manifest

The console table on `/backup` shows one row per archive: file name, creation time, the version and archive schema version that wrote it, whether identities are included, row count, size, and a **Key check** column (hidden by default; enable it from the column menu) that says `This key` or `Another key`. That column is one bit computed on the server — the manifest's key fingerprint never leaves the API process, because publishing it would let someone confirm a guess at `DTK_SECRET_KEY` offline.

An archive that cannot be parsed is listed anyway, with its reason. That is deliberate: a corrupt archive is the single most important thing this page has to be able to show, because it is why someone opened it.

From a terminal:

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
```

The columns are file, created, version, rows, identities and size, plus a `state` of `ok` or `error`. Unlike the console it prints neither the archive schema version nor the key check — for those, read the row on `/backup`. Like `create`, `--dir` defaults to `backups` relative to the working directory, so pass it inside the container.

## Restoring an archive

Three things are checked before a single row is written, and all three are refused in a way that names the problem:

1. the file exists and is a readable archive;
2. its `schema_version` is one this build can read (1 or 2 today; the current writer emits 2);
3. the manifest's key fingerprint matches this instance's `DTK_SECRET_KEY`.

**A restore fills gaps; it does not overwrite.** Rows are inserted with `ON CONFLICT DO NOTHING`, in the order `users → api_keys → proxies → identities → settings → content_snapshots` so foreign keys resolve. Rows already present win, so restoring onto a live instance adds what is missing rather than reverting what is there — and restoring the same archive twice is harmless. `content_snapshots` has no unique index (it is a hypertable), so duplicates there are filtered explicitly on `(ts, platform, content_type, content_id)`. The per-table counts you get back are what the archive *offered*, not how many rows were new; the database does not report the difference and inventing a number would be worse than saying what was read.

Note the consequence: a restore cannot roll an instance back. If you need the archived state exactly, restore into an empty database.

From the console, `/backup` → **Restore** on a row. Administrators only, and you must type the file name to confirm. The restore is queued as a worker task, so this path needs a healthy worker. Reload the console afterwards so every page reads the new data.

From a terminal — in-process, so this is the path during an incident:

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/dtk-backup-20260910T031500Z.tar.gz
```

It prints the manifest, asks for confirmation, then prints the per-table counts. Add `--yes` to skip the prompt in a script. If the key does not match you get an explicit message saying so, and nothing is written.

When the archive contains `settings`, the restore bumps `settings_version`, so every running process reloads its configuration instead of sitting on the pre-restore snapshot until the next restart.

## A restore drill

An untested backup is a hypothesis. Test it quarterly; it takes ten minutes and it is the only way to learn that your `DTK_SECRET_KEY` is not what you thought it was while that is still a cheap discovery.

The drill runs a second, isolated stack from the same checkout: a different compose project name gets its own volumes, a different published port avoids the running instance, and the shared `.env` means the drill instance holds the same `DTK_SECRET_KEY` — which is what makes the archive restorable at all.

```bash
# 1. Take a fresh archive and copy it out of the volume.
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml cp \
  api:/var/lib/dtk/backups/dtk-backup-20260910T031500Z.tar.gz ./drill.tar.gz

# 2. Start an empty stack beside it, on another port.
DTK_BIND_PORT=8010 docker compose -p dtk-drill -f docker/compose.yml up -d --wait

# 3. Restore into it.
docker compose -p dtk-drill -f docker/compose.yml cp ./drill.tar.gz \
  api:/var/lib/dtk/backups/drill.tar.gz
docker compose -p dtk-drill -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/drill.tar.gz --yes

# 4. Prove it.
curl -fsS http://127.0.0.1:8010/readyz
```

Then open `http://127.0.0.1:8010`, and check three things:

- **You can log in** with an account from the archive. A fresh instance is "uninitialised" only while no user row exists; restoring `users` closes the setup window, so the archived administrator password is the one that works.
- **Settings came back** — `/settings` should show your overrides, not the defaults.
- **History came back** — the Overview chart and the Library have the snapshots you expect.

Two things about the drill stack itself. It has no identities (unless you drilled an archive taken with `--include-identities`), so it does no platform traffic; and it does have your proxies, so its proxy prober will dial each of them once every five minutes until you stop it. Tear it down as soon as you are done, volumes included:

```bash
docker compose -p dtk-drill -f docker/compose.yml down -v --remove-orphans
rm drill.tar.gz
```

## Notification channels

Console alerts are not alerts: nobody is looking at the console when the pool empties at 03:00. Channels are configured on `/notifications`, which is a typed editor over three runtime settings — `notify.enabled`, `notify.language` and `notify.channels` — rather than a second source of truth.

`notify.enabled` is **off by default**. While it is off, triggers still fire and are still logged; nothing is delivered.

Six channel types are supported. Every channel carries a `name` (used in tests and audit entries), an optional `language` (the alert is rendered for whoever receives it, not for the console operator), an `enabled` switch and an optional event subset.

| Type | Required | Optional | Notes |
| --- | --- | --- | --- |
| `webhook` | `url` | — | Generic JSON POST; the payload shape is ours and is stable. |
| `bark` | `url` | `group` | Severity is mapped to Bark's `level`. |
| `wecom` | `url` | — | WeCom group bot. |
| `dingtalk` | `url` | `secret` | With a secret, the URL is signed per request. |
| `telegram` | `token`, `chat_id` | — | The URL is built as `https://api.telegram.org/bot<token>/sendMessage`. |
| `smtp` | `host`, `sender`, `recipients` | `port` (587), `username`, `password`, `ssl`, `starttls` | Delivery is blocking SMTP, run off the event loop. |

The generic webhook payload:

```json
{
  "source": "dtk",
  "event": "pool_empty",
  "severity": "error",
  "title": "Identity pool empty",
  "body": "No active identity is left for douyin. Requests are queued or rejected until the pool recovers.",
  "language": "en",
  "sent_at": "2026-09-10T03:15:00+00:00",
  "details": { "platform": "douyin" }
}
```

Targets are validated when the channel is saved, not when an alert fires, so a bad target is refused while you are still looking at the form. The rules are strict and they will bite a home setup:

- **https only.** Plain http would put the alert body and any token in the URL on the wire in clear text.
- **No credentials in the authority** (`https://user:pass@host/...` is refused).
- **No private, loopback or link-local host** — for the SMTP `host` too. A webhook receiver or mail server on your LAN cannot be a channel target. A non-standard port is fine; the port is not what makes an address internal.

Delivery is deliberately cheap: an 8-second timeout, at most 2 attempts with a 1-second backoff, and a retry only on 429, 500, 502, 503 or 504. Alerting that retries hard becomes the outage it is reporting. Failure reasons are scrubbed of credentials before they are logged or stored — a Telegram token lives in the URL path, and any exception quoting that URL is a credential.

Stored credentials are masked on read and merged back on write. A field showing `***` in the editor keeps its stored value if you leave it alone; type a new value to replace it, or clear it to remove it. Rename a channel or point it at a different host and the stored credential is *not* carried over — you will be asked for it again, on purpose.

One malformed channel never silences the others: `build_channels` skips an entry it cannot validate, logs `ops.notify.channel_rejected`, and delivers to the rest.

## Triggers and deduplication windows

The event set is fixed. Deduplication is the feature, not an optimisation: without a window, one tripped endpoint sends a notification per request, ten minutes of that and you switch alerting off for good. Each event has a window and a scope, and a second alert within that window for the same scope is suppressed. The windows are not tunable per install — that is what makes the alerts trustworthy.

| Event | Severity | Window | Scope | Fires when |
| --- | --- | --- | --- | --- |
| `endpoint_circuit_open` | error | 30 min | endpoint | An endpoint tripped its circuit and is refusing requests. |
| `pool_empty` | error | 15 min | platform | No active identity is left for a platform. |
| `pool_below_min` | warning | 60 min | platform | Active identities fell under `pool.min_size`. |
| `proxy_unhealthy` | warning | 60 min | proxy | A proxy failed its probes and was taken out of rotation. |
| `signature_stale` | error | 24 h | endpoint | The in-process signature disagreed with the browser's. |
| `cookie_expiring` | warning | 24 h | identity | An imported logged-in identity expires within 3 days. |
| `backup_failed` | error | 24 h | instance | A backup did not finish. |
| `capacity_warning` | warning | 6 h | volume | Disk use passed `capacity.warn_percent`. |
| `capacity_paused` | error | 1 h | volume | Disk use passed `capacity.hard_stop_percent`; background writers stood down. |
| `media_evicted` | warning | 6 h | instance | Stored media was deleted to stay under `media.max_bytes`. |

A window is only held for an alert that actually reached somebody. If every channel refused it, the mark is released so the next occurrence gets a fresh attempt — otherwise one refused connection would buy up to a day of silence.

**Narrowing a channel's events has a sharp edge.** By default a channel receives every event, which is stored by *omitting* the event list. The console's checklist offers only the first seven events in the table above; the three disk-related ones (`capacity_warning`, `capacity_paused`, `media_evicted`) are not on it. So the moment you untick a single box, the channel is stored with an explicit list that cannot contain the disk events, and it stops receiving them. If you want capacity alerts, leave every box ticked on at least one channel.

## Testing a channel

`/notifications` has **Send test alert** in the header (all channels) and a **Test** button on each row (that channel). Operator role or better.

A test does not consult the deduplication windows at all — neither claiming one nor reading one — so pressing the button twice sends twice, and proving a channel does not consume the suppression window of a real alert. The test body names the channel it was sent to, which is how you tell two phones apart.

Read the result carefully: a finished task is not a delivery that worked. The console reports three distinct outcomes — sent, refused (with the channel name and the reason), and "alerts are switched off, so nothing was sent". The last one means `notify.enabled` is `false`; delivering anyway would prove a channel that is not going to be used.

Testing every channel after any change to `notify.channels` is a two-second habit that is worth having, because the alternative is discovering a typo during the incident the alert was for.

## Retention: what is pruned and when

| Setting | Default | Bounds what | Enforced by |
| --- | --- | --- | --- |
| `retention.request_log_days` | 14 | `request_log` rows | TimescaleDB retention policy |
| `retention.identity_events_days` | 90 | `identity_events` rows | TimescaleDB retention policy |
| `retention.task_result_hours` | 24 | the `result`/`error` payload of finished tasks | `UPDATE` in the maintenance pass |
| `retention.task_days` | 90 | finished (`done`/`failed`) task rows | `DELETE` in the maintenance pass |
| `retention.retired_identity_days` | 90 | rows of retired identities | `DELETE` in the maintenance pass |
| `retention.content_days` | 0 | nothing today — no job reads this key | — |

Windows are validated between 1 and 3650 days. A value outside that range, or a table name that is not one of the two hypertables, is reported on its own and the rest of the maintenance pass still runs — one bad number must not stop task payloads being blanked.

Some detail that matters:

- **The two hypertable windows are enforced by TimescaleDB, not by dtk.** Changing one drops the existing policy and adds it again with the new interval, because TimescaleDB has no "change the interval" call and `add_retention_policy` with `if_not_exists` would silently keep the old window. That re-application happens on the next maintenance pass, so within five minutes. The actual chunk-dropping then runs on TimescaleDB's own background schedule. On a Postgres without the TimescaleDB extension the policy calls fail, the report carries `the retention policy could not be applied; is TimescaleDB installed?`, and nothing prunes those two tables.
- **Blanking a task payload is not deleting the task.** The row survives so the statistics do; asking for that task's result afterwards answers `TASK_NOT_FOUND`.
- **`content_snapshots` never gets a retention policy.** It is your accumulated data, it cannot be re-fetched once a platform drops a post, and deleting it stays an explicit action. Only compression is applied automatically: chunks older than 7 days are moved to the columnstore, which makes them cheaper without making them less readable.
- **`retention.content_days` is inert.** It is declared in the settings registry and shows up on `/settings`, but no code reads it, so its default of "never" is also its behaviour at any value. To remove archived posts, use the delete action in the [Library](./08-downloads-and-library.md).
- **`audit_log` is never trimmed** by anything. That is intentional — see [Security](./15-security.md) — but it means the table grows for the life of the instance.

## The capacity guard

The guard is not retention: nothing here deletes anything. It measures the filesystem, warns, and past a threshold stops the writers nobody is waiting on.

| Setting | Default | Effect |
| --- | --- | --- |
| `capacity.warn_percent` | 80 | Raises `capacity_warning`. Nothing changes behaviour. |
| `capacity.hard_stop_percent` | 92 | Raises `capacity_paused`. Scheduled collection stands down and `POST /api/v1/downloads` refuses new jobs with a 300-second retry hint. |

Both are percentages between 1 and 99, checked on write — 0 would pause an instance the moment it starts and 150 would mean the guard never fires.

Usage is measured with `disk_usage` on `/var/lib/dtk` (the database volume) and `/var/lib/dtk/media` (the media volume) as the containers see them, deduplicated by device so one filesystem mounted twice is not counted twice. The *fullest* volume decides, not the average: the disk that fills first is the one that breaks things. A path that does not exist is skipped — an instance without the downloader profile has no media volume, and that is not a fault.

Interactive API reads are never paused. Turning a full disk into "my API is down" would be a worse outage than the one being prevented, and deleting an archive to free space would be worse than either.

The media ceiling is a separate mechanism with the opposite policy — it *does* delete:

| Setting | Default | Effect |
| --- | --- | --- |
| `media.max_bytes` | 2 GiB | Over this, the oldest unpinned downloads have their files removed until the volume is back under. `0` disables eviction entirely. |
| `media.max_file_bytes` | 512 MiB | One file. Counted on bytes actually written, not on what the server claims. A transfer that reaches it is refused and leaves nothing behind. |

Eviction takes the total from the volume rather than from the database, skips pinned downloads always, removes only files (the row, its digests and its file list stay, so the Library still shows what was collected and says it was evicted), and raises `media_evicted` — which is the only notice you will ever get that your disk policy just ran.

## Monitoring

There are two unauthenticated probes and three authenticated views. Point your monitoring at the probes; use the views when something looks wrong.

| Signal | Where | Healthy looks like |
| --- | --- | --- |
| Liveness | `GET /healthz` | `200`, `{"status":"ok","uptime_seconds":…}`. Touches no dependency on purpose: a liveness probe that hits the database turns a brief hiccup into a restart storm. |
| Readiness | `GET /readyz` | `200` with `postgres` and `redis` both `ok`; `503` otherwise. Browser RPC is deliberately excluded — minting is off the request path, so an instance without it is degraded, not unready. |
| Instance status | `GET /api/v1/system/status`, console `/system` | Components reachable with sane latencies; Chromium major and wreq profile major aligned; `settings_version` matching what you expect. |
| Endpoint health | `GET /api/v1/admin/endpoints/health`, console Overview | `circuit_open: false` everywhere; risk rate well under `sched.circuit_risk_threshold` (0.6). |
| Traffic mix | `GET /api/v1/admin/metrics/timeseries`, console Overview | Mostly `ok`; `risk_control` rare and not concentrated on one endpoint. |
| Pool level | console `/identities`, `/system` | Active identities at or above `pool.min_size` (3) for each platform you use. |
| Disk | console `/system`, `capacity_*` alerts | Below `capacity.warn_percent`. |
| Background loops | `worker` logs | A `worker.maintenance.done` line every ~5 minutes; no repeating `worker.loop.failed`. |
| Backups | console `/backup` | An archive from within your backup interval, with the key check reading `This key`. |

Compose's own healthchecks cover the containers: `pg_isready` for postgres, an authenticated `redis-cli ping` for redis, `GET /readyz` for api, a Redis TCP probe for the worker (it has no port of its own), and `GET /rpc/health` for browser-rpc — that one reads the `status` field rather than settling for a 200, because the service answers the probe even when the browser backend failed to start.

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
docker compose -p dtk -f docker/compose.yml ps
```

The console's **Logs** page (`/logs`) has two tabs: the request log, searchable by request id, endpoint, outcome and time range, and the audit trail — who changed what, kept in its own table so it is never buried by ordinary traffic. Settings changes, backup and restore requests, notification tests and diagnostics all leave an audit row.

For a structured self-check covering egress, proxies, signing and an end-to-end request, run the diagnosis — from the console at `/diagnose`, or:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

Its report is redacted at the source, so it is safe to paste into an issue. [Troubleshooting](./14-troubleshooting.md) walks through reading it.

## Upgrading safely

The `migrate` service is a one-shot container that runs before `api` and `worker` start, and Alembic is idempotent, so migrations are not something you have to remember. Named volumes (`postgres-data`, `redis-data`, `backup-data`, `media-data`) survive rebuilds.

```bash
# 1. Take a backup first, and check it is listed.
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups

# 2. Pull, rebuild, restart.
git pull
docker compose -p dtk -f docker/compose.yml build
docker compose -p dtk -f docker/compose.yml up -d --wait

# 3. Confirm.
curl -fsS http://127.0.0.1:8000/readyz
docker compose -p dtk -f docker/compose.yml logs --tail 50 migrate
```

`up -d --wait` returns when the healthchecks pass, and `api`'s healthcheck is readiness, so a green return means the API can actually serve. If the migrate container exited non-zero, `api` and `worker` will not have started — that is `service_completed_successfully` doing its job, and the migrate log says why.

Points worth knowing before you upgrade:

- **Keep `DTK_SECRET_KEY` unchanged.** Changing it makes every stored cookie jar and proxy URL undecryptable and every existing archive unrestorable. If it must be rotated, retire the affected identities and re-import or re-mint them.
- **Roll back by tag, not by database.** Migrations move forward; there is no downgrade path documented here. If a release misbehaves, check out the previous tag, rebuild, and restore from the backup you took in step 1 — into an empty database, since a restore fills gaps rather than reverting.
- **`build` and `up` only touch the services in the active profiles.** If you deploy with `--profile browser` or `--profile downloader`, repeat those flags on both commands. Compose reads the file rather than the running stack, so without them nothing rebuilds `browser-rpc` or `downloader`: they keep serving the old image while `api` and `worker` move forward, and nothing warns you. [Installation and deployment](./02-installation.md) has the flags in full.
- **Browser images are pinned deliberately.** `CLOAKBROWSER_COMMIT` is a build argument, and like the published address it is compose interpolation: it is read from `docker/.env` or your shell, not from the repository-root `.env`. Pass it on the command line when you rebuild the browser image, or point compose at the root file with `COMPOSE_ENV_FILES=.env`. It identifies exactly one revision; several unrelated repositories publish identically described "cloakbrowser" projects, so a floating tag would not identify any particular software. Change it on purpose, and re-check signing afterwards.
- **Watch for version drift** on `/system` after an upgrade: the Chromium major that produces signatures and the wreq TLS profile major that replays fingerprints have to agree.
- **Scale by restarting workers, not by editing intervals.** `docker compose -p dtk -f docker/compose.yml up -d --scale worker=4`.

## Capacity planning: identities and workers

Throughput is bounded by the identity pool, not by the worker count. The numbers are readable, so plan with them rather than guessing.

Each `(identity, endpoint)` pair has a token bucket, and each identity serves **one upstream request at a time** whatever the endpoint. The buckets from `src/dtk/scheduler/policies.py`:

| Endpoint | Burst | Refill | Per identity |
| --- | --- | --- | --- |
| `*.content_detail` | 5 | 0.30/s | 18 requests/min |
| `*.author_profile` | 4 | 0.20/s | 12 requests/min |
| `*.comments`, `*.comment_replies`, `*.mix_posts` | 3 | 0.15/s | 9 requests/min |
| `*.author_posts`, `*.author_likes`, `tiktok.author_followers`, `tiktok.author_following` | 3 | 0.12/s | 7.2 requests/min |
| `*.session_check` | 3 | 0.20/s | 12 requests/min |
| anything unregistered | 3 | 0.15/s | 9 requests/min |

So:

- **Steady rate** for one endpoint ≈ *identities × refill*. Sixty video lookups a minute needs 60 ÷ 18 ≈ **4 identities** at full spend, and you want headroom: identities go into cooldown after a risk-control hit, and one in a `cooling` or `degraded` state does not serve. Six to eight is a realistic pool for that rate, which is why `pool.target_size` defaults to 8 and `pool.min_size` to 3.
- **Burst** is *identities × capacity* requests before the buckets are empty — about 20 detail lookups across 4 identities, then it settles to the steady rate.
- **Concurrency** is capped by the number of usable identities, because a second request cannot start on an identity that is busy. Per-endpoint concurrency is 1 by design and should stay 1: a real session does not issue parallel API calls. Scale with more identities, never with more concurrency.
- **Workers**: each `worker` process runs 4 tasks at once. Extra slots simply wait on the scheduler, so workers beyond ⌈identities ÷ 4⌉ add queueing and nothing else. One worker is right for a pool of up to 8; scale the pool first.
- **The queue sheds load rather than growing.** A submission is already a task before any identity is involved — the API answers `202` first — and that task waits `sched.max_wait_seconds` (10s) for a free identity before failing with `IDENTITY_POOL_EXHAUSTED` and a `Retry-After`. A *new* submission is refused earlier still, with `QUEUE_FULL` and its own `Retry-After`, once the task queue is already at `sched.queue_max` (500) entries. The two refusals surface in different places: `wait_timeout` is the scheduler's reject reason and is written to `request_log`, so you will see it on `/logs`; `queue_full` is raised in the API process before a task exists, never reaches `request_log`, and appears only in the caller's error envelope as `details.reject_reason`. Either one means you are short of identities, not short of workers.
- **API-side limits are separate.** `api.default_rate_limit_per_min` (120) is abuse protection per credential, not a throughput plan.

Growing the pool means growing the proxies behind it: one identity is bound to one egress, and stacking identities behind a single exit address is what gets that address noticed. [Identities and proxies](./06-identities-and-proxies.md) covers that side.

Resource ceilings are set in `docker/compose.yml` and are worth reading before you scale: 512 MiB and 2 CPUs each for `api` and `worker`, 2 GiB for postgres, 4 GiB and 4 CPUs for `browser-rpc` (Chromium legitimately uses six cores' worth when warm). Raising `DTK_BROWSER_WARM_CONTEXTS` costs roughly 300 MB of the browser container's `/tmp` tmpfs per resident page, per platform — raise the tmpfs alongside it or Chromium crashes in a way that reads exactly like a platform block.

## Reading the logs

Logs are structured, English-only, and written to stdout. `DTK_LOG_LEVEL` (default `info`) and `DTK_LOG_JSON` (default `true`) are bootstrap settings: change them in `.env` and restart the container.

```bash
docker compose -p dtk -f docker/compose.yml logs -f worker
docker compose -p dtk -f docker/compose.yml logs --tail 200 api
```

Event names are dotted identifiers, never sentences, so grep stays stable across releases and aggregation by event type is possible: `worker.loop.started`, `worker.maintenance.done`, `worker.maintenance.capacity`, `worker.maintenance.media_evicted`, `ops.notify.dispatched`, `ops.notify.delivery_failed`, `ops.retention.policy_applied`, `ops.backup.created`, `config.updated`, `config.reloaded`, `system.probe_failed`.

With `DTK_LOG_JSON=true`, filter with `jq`. Use `fromjson?` so the entrypoint's plain-text startup lines do not abort the pipeline:

```bash
docker compose -p dtk -f docker/compose.yml logs --no-log-prefix --tail 2000 worker \
  | jq -R 'fromjson? | select(.event | startswith("worker.maintenance"))'

docker compose -p dtk -f docker/compose.yml logs --no-log-prefix --tail 2000 worker \
  | jq -R 'fromjson? | select(.level == "error")'
```

Set `DTK_LOG_JSON=false` while you are reading along live; that switches to a coloured console renderer, which is far easier on the eye and useless to a log aggregator.

Two things about what is *not* in the logs:

- **Redaction happens in the processor chain, not at call sites.** Keys named `cookie`, `authorization`, `api_key`, `password`, `secret`, `token`, `proxy_url`, `dtk_secret_key` and friends are replaced wholesale, and signature/session parameters (`msToken`, `a_bogus`, `X-Bogus`, `_signature`, `sessionid`, `ttwid`, …) are truncated to six characters wherever they appear inside a string. A call site that forgets is how the previous generation of this project leaked a live session cookie.
- **`httpx`, `httpcore` and `hpack` are pinned to WARNING** regardless of your level. `httpx` logs every request line at INFO, URL included — that is a leak, not just noise, since a callback URL routinely carries a token and a signed CDN link carries a signature. Everything those lines said is in this project's own `transport.request.done`, with the URL masked.

Per-request history is not in the container log at all: it is one row per request in `request_log`, which is what the Overview charts and the Logs page read. The circuit breaker does not read it: the trip decision runs off its own 300-second rolling window in Redis (`sched:window:<endpoint>`), which is why the endpoint-health numbers and the log-derived charts answer slightly different questions and will not always agree. Each row carries the timestamp, `request_id` (the same one the API envelope returns), task id, platform, endpoint, identity and proxy, API key, outcome (`ok`, `business_error`, `risk_control`, `network_error`), HTTP status, duration, cache hit, which signer produced the signature, an error code, and the scheduler's reject reason when no lease was issued. The row is written by `FetchService` on the request's own database session, so it commits with the request rather than being batched, and a refused lease is logged before the error is raised — which is why `/logs` keeps filling up during exactly the outage you opened it to read.

## A day-two routine

| Cadence | Do |
| --- | --- |
| Automatic | Backups by cron; alerts on at least one channel that receives every event. |
| Weekly | Glance at `/system` (drift, disk) and `/backup` (an archive from within your interval, key check `This key`). |
| Monthly | Skim `/logs` → Audit for changes you do not recognise. Check whether identities are being retired faster than usual on `/identities`. |
| Quarterly | Run the restore drill above. Rotate API keys you no longer recognise ([Users and API keys](./09-users-and-api-keys.md)). |
| Before every upgrade | Take a backup, read the release notes, upgrade, check `/readyz` and `/system`. |
| After every `notify.channels` change | Press **Send test alert**. |

## Related pages

- [Installation and deployment](./02-installation.md) — the compose stack, profiles and volumes this page operates.
- [Configuration reference](./03-configuration.md) — every setting, its scope and its default.
- [Console overview](./05-console-overview.md) — what each page shows.
- [Identities and proxies](./06-identities-and-proxies.md) — growing and repairing the pool.
- [Downloads, library and watchlist](./08-downloads-and-library.md) — the writers the capacity guard pauses.
- [Users and API keys](./09-users-and-api-keys.md) — roles, and who may do what on this page.
- [CLI reference](./13-cli.md) — every `dtk` command this page runs, with its full option list.
- [Troubleshooting](./14-troubleshooting.md) — reading a diagnosis, and the usual failures.
- [Security](./15-security.md) — sensitive settings, the audit trail and key handling.
