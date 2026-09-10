# CLI reference

`dtk` is the operations and rescue command line that ships inside the application image. After reading this you will know how to reach it on a running deployment, what every command and option does, and which commands are safe to fire at a live instance versus which ones you only reach for during an incident.

## What the CLI is, and what it is not

`dtk` is **not** a client for the REST API. It opens Postgres directly, opens Redis when it needs to, and makes platform requests itself. That is deliberate: everything in it is something you want when the console cannot help you — nobody can log in, the instance has to move to another machine, or an endpoint has gone quiet and you need the raw answer with no authentication, no rate limit and no cache in the way.

The consequences are worth stating plainly:

- It works when the API container is down, as long as Postgres is reachable.
- It bypasses API keys, scopes, the per-key rate limit and the response cache. There is no audit trail for most of what it does.
- `dtk fetch` and `dtk identity test` make **real** platform requests outside the scheduler. They take no lease and respect no token bucket, so they do not pace themselves the way the worker does.

If you want the authenticated, rate-limited, cached path, use the REST API — see [REST API guide](./11-api.md).

## Running the command

### Inside the container (the normal way)

The `api`, `worker` and `migrate` services are all built from the same image, and `dtk` is on `PATH` in it. `docker compose exec` runs the command directly and does not go through the image entrypoint, so the role argument (`api` / `worker` / `migrate`) is not involved:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk --version
```

```text
dtk 5.0.0
```

Every example on this page uses plain `exec`, the same spelling the rest of these documents use. It matters most for the commands that prompt — `dtk user create`, `dtk user passwd` and `dtk backup restore` all ask for something and need a terminal. Add `-T` only when you are scripting, piping or redirecting input into the command, as the recipes below do:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
```

```text
id        username   role   created                    last login
9bb3e8ad  evil0ctal  admin  2026-09-08T03:40:08+00:00  2026-09-08T03:40:09+00:00
```

Either `api` or `worker` will do — same image, same environment file, same database. The examples below use `api`.

**The container's root filesystem is read-only.** Only `/tmp` and the `backup-data` volume mounted at `/var/lib/dtk/backups` are writable. Any command that writes a file must be told to write there. This is the single most common surprise:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk backup create
```

```text
error cannot reach a dependency: [Errno 30] Read-only file system: 'backups'
```

The fix is to pass a writable path, which is covered under [`dtk backup`](#dtk-backup) below.

### Locally from a checkout

```bash
uv sync --all-extras
uv run dtk --help
```

This is the right form when you are developing, when you want `dtk serve` with `--reload`, or when the containers will not start at all.

The catch is reachability. `BootstrapSettings` reads a `.env` file from the **current working directory**, so run `uv run dtk` from the repository root. But the `.env` that the shipped Compose stack uses names in-container hostnames:

```text
DTK_DATABASE_URL=postgresql+asyncpg://dtk:...@postgres:5432/dtk
DTK_REDIS_URL=redis://:...@redis:6379/0
```

`postgres` and `redis` do not resolve on the host, and `docker/compose.yml` puts both of them on an `internal: true` network with no published port on purpose. So a locally-run CLI against the Compose stack fails like this:

```bash
uv run dtk user list
```

```text
error cannot reach a dependency: [Errno 8] nodename nor servname provided, or not known
```

Either publish those ports yourself (and accept what that means for exposure — see [Security](./15-security.md)), or use `docker compose exec`. For a checkout running against the test fixtures or its own local Postgres, override the two URLs on the command line:

```bash
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk@127.0.0.1:5432/dtk uv run dtk user list
```

There is no shell completion. The application deliberately builds its Typer app with completion turned off, so there is no `--install-completion` option to find.

## What the CLI reads from the environment

Every command that opens the database goes through one bootstrap step that reads `DTK_*` from the environment (and from `.env` in the working directory). If it cannot, it says what is missing and exits 1 rather than printing a traceback:

```text
error DTK_SECRET_KEY must be set and at least 32 characters. Generate one with: openssl rand -base64 48
hint: export DTK_SECRET_KEY, or run the CLI inside the container that has it
```

| Variable | Default | Which commands need it |
|---|---|---|
| `DTK_SECRET_KEY` | none — required, at least 32 characters | Everything except `dtk --version`, `dtk migrate --show` and `dtk backup list` |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | Every command that touches state |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | `dtk config set`, `dtk identity retire`, `dtk diagnose`, `dtk worker` |
| `DTK_BROWSER_RPC_URL` | empty (disabled) | `dtk identity mint` requires it; `dtk fetch`, `dtk identity test` and step 5 of `dtk diagnose` use it as the signing fallback when it is set |
| `DTK_BIND_HOST` | `127.0.0.1` | `dtk serve`, when `--host` is not given |
| `DTK_BIND_PORT` | `8000` | `dtk serve`, when `--port` is not given |
| `DTK_LOG_LEVEL` | `info` | `dtk serve` (handed to uvicorn, and read again by the API app it starts) and `dtk worker`, which configures the logging stack from it. It does **not** change the log output of the other commands — `fetch`, `diagnose`, `config`, `identity`, `proxy`, `user`, `backup`, `migrate` |
| `DTK_LOG_JSON` | `true` | `dtk worker` and the app `dtk serve` starts: `true` renders log lines as JSON, `false` as human-readable console lines. The other commands do not configure logging, so it does nothing there |

`DTK_BACKUP_DIR` is deliberately absent from that table. The API and the worker read it; the CLI's `dtk backup create` and `dtk backup list` do not — they default to the literal relative path `backups`. See [`dtk backup`](#dtk-backup).

Three commands need less than the rest, which is what makes them usable on a broken box:

- `dtk --version` needs nothing.
- `dtk migrate --show` reads the migration scripts off disk and opens no connection, so it works with no key and no database.
- `dtk backup list` only reads a directory, so it works with no key and no database either.

## Output, exit codes and machine-readable output

Three exit codes, and nothing else leaves the process:

| Code | Meaning |
|---|---|
| `0` | The command did what it was asked to do |
| `1` | It ran and failed — unreachable database, unknown user, dead proxy, a platform that answered with a block |
| `2` | The invocation was wrong — unknown option, bad value, missing argument, unknown setting key |

Keeping `1` and `2` apart is what lets a deploy script tell "the tool is being called wrong" from "the system is broken". Running a command group with no subcommand (`dtk`, `dtk identity`) prints help and exits `2`.

Data goes to stdout, and so do the `ok` and `info` lines; only `warn` and `error` go to stderr. Redirecting stderr away therefore does not leave the pipe carrying data alone — a successful command still writes its `ok` line into it. Every string the CLI prints is scrubbed first: proxy passwords, cookie values, bot tokens and signature parameters are masked on the way out, because a terminal is the least private place there is and this output is meant to be pasteable into a bug report.

### Getting clean JSON out

Two commands take `--json`: `dtk fetch` and `dtk diagnose`. Three things get in the way of piping their output straight into `jq`:

1. **Log lines land on stdout.** `dtk fetch` and `dtk diagnose` do not configure the logging stack, so `structlog` uses its own defaults and writes to stdout, interleaved with your data. `DTK_LOG_LEVEL` does not suppress them. The JSON document starts at a `{` in column 0, so `sed` can find it.
2. **Rich wraps at 80 columns when there is no terminal**, which inserts newlines inside long JSON strings and makes the document unparseable. Set `COLUMNS` wide.
3. **`dtk diagnose --json` prints its status line after the JSON**, on stdout. A run in which every step passed ends with `ok  all steps passed`; `-o` adds a `text report written to ...` line the same way. A run that only warned does not, because the warning goes to stderr — which is why this is easy to miss. Drop the trailing line as well.

Together:

```bash
docker compose -p dtk -f docker/compose.yml exec -T -e COLUMNS=10000 api \
  dtk diagnose --skip-smoke --json 2>/dev/null | sed -n '/^{/,$p' | sed '/^ok  /d'
```

That parses. Commands that make no network calls — `dtk config get`, `dtk migrate --show`, `dtk user list` — emit no log lines and need neither trick.

## Command map

| Command | What it does | Touches |
|---|---|---|
| `dtk fetch` | Resolve one link and print the normalized result | DB, platform |
| `dtk diagnose` | Six-step self-check, printed as a report | DB, Redis, proxies, browser-rpc, platform |
| `dtk worker` | Run the task worker in the foreground | DB, Redis |
| `dtk migrate` | Apply database migrations | DB |
| `dtk serve` | Run the API server | — |
| `dtk user create/passwd/list` | Console accounts | DB |
| `dtk backup create/list/restore` | Archive and restore an instance | DB, filesystem |
| `dtk config list/get/set` | Runtime settings stored in the database | DB, Redis on `set` |
| `dtk identity list/mint/retire/test` | The identity pool | DB, browser-rpc, platform |
| `dtk proxy list/add/import/test` | Egress | DB, network |

Root options are only `--version` and `--help`.

## `dtk fetch`

Resolve one link and print what came back. This is the fastest way to answer "is this endpoint dead?": no login, no API key, no rate limit and no cache — the URL is resolved, one signed request goes out through a real identity, and the answer is printed.

```text
dtk fetch [OPTIONS] {url}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `url` | string, required | — | A Douyin or TikTok link, or pasted share text |
| `--raw` | flag | off | Include the platform's own payload in the output |
| `--json` | flag | off | Print only the JSON result, for piping |
| `--identity` | string | next identity | Use this identity. **Must be the full UUID**, not the 8-character prefix the tables print |
| `--proxy` | string | none | Proxy for the short-link expansion hop only — see below |
| `--timeout` | float | `25.0` | Seconds to wait for the platform |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk fetch "https://www.tiktok.com/@owlcitymusic/video/7218694761253735723"
```

```text
field        value
platform     tiktok
resource     video
endpoint     tiktok.content_detail
url          https://www.tiktok.com/@owlcitymusic/video/7218694761253735723
identity     af4a98f6
proxy        -
http status  200
outcome      ok
rule         default.ok
latency ms   343
bytes        24670
{
  "platform": "tiktok",
  "content_id": "7218694761253735723",
  "kind": "video",
  ...
}
ok  endpoint answered
```

Things worth knowing before you rely on it:

- **`--proxy` is not the request proxy.** It applies only to the hop that expands a short link (`v.douyin.com/...`). The platform request itself goes out through whatever proxy the chosen identity is bound to, which is what the `proxy` row of the summary reports.
- **When no identity is named, it picks the least recently used one** for that platform — the one the scheduler would have reached for next, not the healthiest.
- **It exits 1 on any outcome that is not `ok`**, including a business error such as a deleted video. `dtk identity test` treats a business error as a pass; `fetch` does not, because you asked for data and did not get it.
- **It archives what it parsed**, on the same terms the worker uses, when `archive.enabled` is on (it is by default). So a fetch is not read-only: it can add a row to `content_snapshots`. Archiving failures are logged and dropped rather than failing the command.
- **`--raw` is the point when parsing fails.** An `UPSTREAM_CHANGED` error names the field that went missing, and the raw body next to it is what tells you whether the platform renamed it or stopped returning it:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw "<url>"
```

Two links only ever resolve to two things: a video and a user profile. A live room or a music page is recognized by the URL parser but has no endpoint, and the command says which resource it refused rather than failing generically.

## `dtk diagnose`

The one-command self-check, and the first thing to run when the instance is up but returns no data. It runs the same six steps the console runs and prints one report designed to be pasted into a bug report — the report is redacted by the ops layer before it is printed, not by you.

```text
dtk diagnose [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--json` | flag | off | Print the report as JSON instead of a table |
| `--output`, `-o` | path | none | Also write the text report to a file |
| `--skip-smoke` | flag | off | Do not make the end-to-end request |
| `--platform` | `douyin` \| `tiktok` | `douyin` | Platform for the smoke test |
| `--smoke-url` | string | the built-in link for that platform | Link to use for the smoke test |
| `--probe-url` | string | `https://api.ipify.org?format=json` | Service that reports a proxy's exit address. Note that this is **not** the default `dtk proxy test` uses, which is `https://ipinfo.io/json` |

The six steps, in order:

| # | Step | What it checks |
|---|---|---|
| 1 | `components` | Postgres, Redis and browser-rpc are reachable |
| 2 | `egress` | This machine can reach the platform domains directly |
| 3 | `proxies` | Each proxy: reachability and exit IP. The country it shows is the one already stored on the proxy row, not a fresh GeoIP lookup |
| 4 | `pool` | How many usable identities there are, and how stale the oldest is |
| 5 | `signing` | The pure-Python signature against browser-rpc's, per platform — the same shadow comparison the registry runs in production |
| 6 | `smoke` | One fixed public link through the whole pipeline |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --skip-smoke
```

```text
#  step        status   reason
1  components  pass     all components reachable
2  egress      pass     all platform domains reachable
3  proxies     warn     no proxies configured
4  pool        pass     18 active identities
5  signing     pass     native and browser signatures agree
6  smoke       skipped  no smoke test link configured
proxies: Every identity will share this machine's egress IP. That is fine for a
first look and a correlation risk for a busy pool.
warn 1 step(s) raised a warning
```

Behaviour that matters when you script it:

- **A warning does not fail the command.** Any failed step exits 1; warnings print to stderr and exit 0. Failing over a warning is how a scheduled health check turns into a false alarm the operator learns to ignore.
- **A step that cannot run is skipped, not failed.** No browser-rpc configured means step 5 is `skipped`, because an optional dependency you never installed is not a fault.
- **It writes nothing to the database.** Step 3 probes proxies but does not record their health, unlike `dtk proxy test`.
- **Step 5 is slow the first time.** The comparison drives a real browser page, so the first signature after a cold browser-rpc is measured in seconds and every later one in milliseconds. The figure recorded in the code is a ~4s cold bind against a ~10ms warm one, and browser-rpc's own ceiling — 45s to open a context plus 8s to sign — is why the signing timeout is 60s.
- **`-o` needs a writable path.** In the container that means `/tmp/report.txt` or a path under `/var/lib/dtk/backups`.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk diagnose -o /tmp/dtk-report.txt
docker compose -p dtk -f docker/compose.yml exec api cat /tmp/dtk-report.txt
```

The JSON form carries `version`, `started_at`, `finished_at`, `passed`, `verdict` (`pass` / `warn` / `fail`), a `steps` array and a pre-rendered `text` field holding exactly the plain-text report.

More on reading the output: [Troubleshooting](./14-troubleshooting.md).

## `dtk worker`

Run the task worker until it is stopped.

```text
dtk worker
```

It takes no options, on purpose. Concurrency, claim timeouts and the background intervals belong to the process configuration, not to the invocation — two workers on one machine disagreeing about their limits is a problem nobody can see from the outside. The values it runs with are code constants: concurrency 4, a 5-second queue claim timeout, 3 attempts before a task is failed, and 60 seconds to drain in-flight work after `SIGTERM`.

This is the exact same process the `worker` container runs as `python -m dtk.worker`. Having one implementation is the entire point of being able to start one by hand during an incident: it behaves identically to the one in the Compose file.

```bash
uv run dtk worker
```

Starting one alongside the Compose worker is legitimate — they consume the same Redis queue and simply share it. Throughput is bounded by the identity pool, not by the number of workers, so a second one on the same pool buys much less than it looks like it should. To scale properly, use Compose:

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=2
```

`Ctrl-C` prints `stopped` and exits 0.

## `dtk migrate`

Apply database migrations.

```text
dtk migrate [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--revision` | string | `head` | Target revision; `head` applies everything |
| `--show` | flag | off | Print the revision on disk and exit |

Normally the `migrate` container does this at startup, and `api` and `worker` will not start until it has exited successfully. Running it by hand is for an upgrade that has to be timed, or for a database restored from a backup taken at an older schema.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate --show
```

```text
field             value
revision on disk  0007
```

`--show` reads the migration scripts shipped inside the package. It opens no database connection and reads no configuration, so it answers "which schema does this image expect?" on a box where nothing else works.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate
```

```text
ok  database migrated to head (head on disk: 0007)
```

There is no rollback here. `--revision` is passed to an upgrade, so naming an older revision does not downgrade to it. Downgrading exists in the code but is not exposed on the command line, which is a deliberate refusal: the safe way back from a bad upgrade is a backup taken before it.

## `dtk serve`

Run the API server.

```text
dtk serve [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--host` | string | `DTK_BIND_HOST`, itself `127.0.0.1` | Bind address |
| `--port` | int | `DTK_BIND_PORT`, itself `8000` | Bind port |
| `--reload` | flag | off | Reload on source changes |
| `--workers` | int, 1–32 | `1` | Worker processes |

This is a development and rescue entry point. The container starts uvicorn through its own entrypoint script, which adds `--no-server-header` and the forwarded-header handling that a reverse proxy needs; `dtk serve` does not. Use it from a checkout, not to replace the `api` container.

```bash
uv run dtk serve --reload
```

`--reload` and `--workers` are mutually exclusive, and the invocation is rejected before the environment is even read — a bad invocation must not be reported as a runtime failure just because `DTK_SECRET_KEY` happens to be missing too:

```bash
uv run dtk serve --reload --workers 2
```

```text
Usage: dtk serve [OPTIONS]
Try 'dtk serve --help' for help.
╭─ Error ──────────────────────────────────────────────────────────────────────╮
│ Invalid value: --reload cannot be combined with --workers                     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Exit code 2.

## `dtk user`

Console accounts: create one, reset a password, list them.

A self-hosted tool has no password-reset email and no support desk, so an administrator who forgets the console password has exactly one way back in: a shell on the box. That is why this group exists — without it the honest instruction would be "drop the database", which throws away every identity, every snapshot and every setting to fix a forgotten string.

Passwords are hashed with argon2id at the OWASP-recommended parameters (19 MiB of memory, two iterations, one lane). Neither the password nor its digest is ever printed, echoed or logged. The minimum length is 8 characters, the same floor the console enforces.

### `dtk user create`

```text
dtk user create [OPTIONS] {username}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `username` | string, required | — | Login name, unique across the instance |
| `--role` | `admin` \| `operator` \| `viewer` | `admin` | Permission level |
| `--stdin` | flag | off | Read the password from stdin instead of prompting |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user create alice --role operator
```

It prompts twice for the password (hidden, with confirmation), then prints the new account's id, username and role.

For provisioning scripts there is `--stdin`. It reads exactly one line, so a here-doc cannot smuggle a second command in, and it strips a trailing `CR` as well as the `LF` — a password piped from a CRLF file would otherwise be hashed with a carriage return on the end, and nothing anyone could type afterwards would match it.

```bash
read -rs -p 'password: ' DTK_NEW_PASSWORD; echo
printf '%s\n' "$DTK_NEW_PASSWORD" | docker compose -p dtk -f docker/compose.yml \
  exec -T api dtk user create alice --role operator --stdin
unset DTK_NEW_PASSWORD
```

`read -rs` rather than typing the password into the command line keeps it out of shell history. If the username already exists the command fails with exit 1 and points you at `dtk user passwd`.

### `dtk user passwd`

```text
dtk user passwd [OPTIONS] {username}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `username` | string, required | — | Account to reset |
| `--stdin` | flag | off | Read the password from stdin instead of prompting |

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd evil0ctal
```

```text
ok  password changed for evil0ctal
```

This command deliberately does not require Redis. An unreachable cache has nothing to do with resetting a password, and making it a hard dependency would take away the rescue path the command exists for.

### `dtk user list`

```text
dtk user list
```

No options. Columns: `id` (shortened to 8 characters), `username`, `role`, `created`, `last login`. Never shows a password digest.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
```

```text
id        username   role   created                    last login
9bb3e8ad  evil0ctal  admin  2026-09-08T03:40:08+00:00  2026-09-08T03:40:09+00:00
```

There is no `dtk user delete`. Removing an account is a console operation — see [Users and API keys](./09-users-and-api-keys.md).

## `dtk backup`

Create, inspect and restore backups. The archive format, the manifest, the key check and the row encoding all live in the shared ops layer, so the console and the CLI cannot produce archives that disagree.

Two properties of the format surprise nearly everybody, so they are worth stating before the commands:

- **Credentials are exported still encrypted.** Restoring needs the *same* `DTK_SECRET_KEY`. Without it the cookies and proxy URLs in the archive cannot be decrypted, and the restore is refused rather than half-applied.
- **Identities are left out unless you ask for them.** They are bound to a proxy and an exit address that a new machine does not have.

### The default directory is not `DTK_BACKUP_DIR`

`dtk backup create -o` and `dtk backup list --dir` both default to the literal relative path `backups`, resolved against the current working directory. The API and the worker read `DTK_BACKUP_DIR`; these two commands do not. In the container the working directory is `/app`, which is read-only, so a bare `dtk backup create` fails and a bare `dtk backup list` looks in the wrong place. Always pass the path:

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup list --dir /var/lib/dtk/backups
```

`/var/lib/dtk/backups` is where `docker/compose.yml` mounts the shared `backup-data` volume and where it points `DTK_BACKUP_DIR`, so archives written there are the ones the console lists.

### `dtk backup create`

```text
dtk backup create [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--output`, `-o` | path | `backups` | Archive path, or a directory to write into |
| `--include-identities` | flag | off | Also export login credentials; for moving a deployment, not for routine backups |

If the path is a directory, or does not end in `.tar.gz`, a timestamped filename is generated inside it: `dtk-backup-20260910T084830Z.tar.gz`.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup create -o /var/lib/dtk/backups
```

```text
table              rows
api_keys           31
content_snapshots  138
proxies            0
settings           4
users              1
ok  wrote /var/lib/dtk/backups/dtk-backup-20260910T084830Z.tar.gz (0.0 MiB)
identities were not exported; pass --include-identities when moving the whole
deployment to a machine with the same egress
```

What goes in and what does not:

| Tables | When |
|---|---|
| `users`, `api_keys`, `proxies`, `settings`, `content_snapshots` | Always |
| `identities` | Only with `--include-identities` |
| `request_log`, `identity_events`, `tasks`, `audit_log`, `settings_version` | Never |

The excluded set is not an oversight: `request_log` and `identity_events` are operational history that means nothing on another machine, `tasks` are in-flight work, and `audit_log` records actions taken against the instance being replaced.

The archive and every member inside it are written mode `0600`. The credential columns are ciphertext, but `settings` holds alert-channel webhooks in the clear, so neither the file nor a hand-extracted copy of it may be readable by other accounts on the host.

Cost: the whole database is dumped to JSON-lines and gzipped. On a large `content_snapshots` this is minutes of CPU and a file that can be gigabytes. The gzip runs on a worker thread so it does not stall the process, and the staging directory is created *beside* the archive rather than in `/tmp` — `/tmp` in the container is a 64 MB tmpfs, which is RAM, and an archive of any size filled it.

### `dtk backup list`

```text
dtk backup list [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--dir` | path | `backups` | Directory to scan |

Newest first. Columns: `file`, `created`, `version` (the dtk version that wrote it), `rows`, `identities`, `size`, `state`. A corrupt archive is listed too, with `state` `error` and its reason printed as a warning — hiding it is exactly the wrong thing to do to somebody checking their backups.

This command opens no database connection and needs no `DTK_SECRET_KEY`, so it works on a box where nothing else does.

### `dtk backup restore`

```text
dtk backup restore [OPTIONS] {path}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `path` | path, required | — | Archive to restore |
| `--yes`, `-y` | flag | off | Do not ask for confirmation |

It prints the manifest first — created date, the version that wrote it, schema version, whether identities are in it, and the row count — then asks before writing anything.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk backup restore /var/lib/dtk/backups/dtk-backup-20260910T084830Z.tar.gz
```

**Rows that already exist are kept.** A restore fills in what is missing rather than overwriting an instance that is already in use. That makes it safe to run against a live instance in the sense that it will not destroy data — but it also means it is *not* a way to roll back: restoring an older archive over a newer database adds back deleted rows and changes nothing else.

If the key does not match, the command stops before writing:

```text
error DTK_SECRET_KEY does not match the key this backup was taken with
hint: restore with the original key; without it the cookies and proxy URLs in the archive cannot be decrypted
```

Archive schema versions 1 and 2 can both be read; anything newer is refused rather than misinterpreted.

## `dtk config`

Runtime configuration stored in the database.

The `settings` table is authoritative once an instance is initialized, so a misconfigured value **cannot** be fixed by editing `.env` and restarting — a fact that surprises everybody exactly once. These commands are how it gets fixed without the console. There are 54 runtime settings; the full catalogue with meanings and defaults is in [Configuration reference](./03-configuration.md).

### `dtk config list`

```text
dtk config list [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--scope` | `bootstrap` \| `env_only` \| `runtime` \| `sensitive` | all | Show only settings in this scope |
| `--changed` | flag | off | Only settings that differ from their default |

Columns: `key`, `value`, `default`, `scope`, `description`. A value that differs from its default is shown in bold. The footer prints the settings version — the counter that running processes watch to know a change happened.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config list --changed
```

```text
no settings match that filter
settings version 112
```

Two things about the scopes. Of the 54 settings, 47 are `runtime` and 7 are `sensitive`; `--scope bootstrap` and `--scope env_only` are accepted but always match nothing, because those settings live in the environment and never in this table.

And **a `sensitive` setting whose value is a plain string is masked.** That is by declaration, not by shape: the registry says which settings are sensitive, and every surface — this table, the admin API, the audit row a write leaves behind — masks them the same way. The mask keeps the first four characters and replaces the rest with `***`; a value of four characters or fewer is masked whole, because showing three of four characters is not a mask. So `security.webhook_secret` set to `s3cr3t-value-abcdef` reads `s3cr***`, and `security.request_proxy` reads `***` at its default `deny` but `allo***` or `publ***` once it is set to one of its other choices. It applies to genuinely secret values and also to a sensitive setting that is not itself a secret, such as `security.request_proxy`. Lists, booleans and numbers are shown as they are, and an empty string is shown as empty, because "not set" is worth being able to see.

### `dtk config get`

```text
dtk config get {key}
```

Prints one setting as JSON. An unknown key is an invocation error, exit 2.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config get cache.content_ttl
```

```json
{
  "key": "cache.content_ttl",
  "value": 1800
}
```

### `dtk config set`

```text
dtk config set [OPTIONS] {key} {value}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `key` | string, required | — | Setting key |
| `value` | string, required | — | New value; lists are comma separated |
| `--yes`, `-y` | flag | off | Do not ask before changing a sensitive setting |

The value is coerced to the declared type and validated *before* anything is written; a type mismatch is an invocation error, exit 2. The write then bumps the settings version and announces it on the Redis channel the API and workers listen on, so a running deployment picks the change up without a restart. This is the one `config` subcommand that needs Redis.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk config set cache.content_ttl 900
```

```text
ok  cache.content_ttl = 900
```

Sensitive settings widen the attack surface — the URL allowlist, the CORS origins, the caller-supplied request proxy — so they ask for confirmation first. In a script, `-y` skips the prompt; do not reach for it habitually.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk config set security.url_allowlist "example.com,cdn.example.com" -y
```

There is no `dtk config reset`. To return a setting to its default, set it explicitly to the default value shown by `dtk config list`, or clear the override from the console.

## `dtk identity`

Inspect, mint, retire and probe pool identities. What an identity is and why the pool is shaped this way is in [Identities and proxies](./06-identities-and-proxies.md); this section is only the commands.

Cookie jars never appear in this output. `list` shows nothing of them, `mint` reports which cookie *names* came back, and `test` reports what the platform answered — not what was sent.

`retire` and `test` accept either the full UUID or the 8-character prefix the tables print; an ambiguous prefix is refused rather than guessed. `mint --proxy` and `fetch --identity` need the **full** id.

### `dtk identity list`

```text
dtk identity list [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--platform` | `douyin` \| `tiktok` | all | Only this platform |
| `--state` | `minting` \| `active` \| `cooling` \| `degraded` \| `retired` | all | Only identities in this state |
| `--limit` | int, 1–500 | `50` | Rows to show |

Columns: `id`, `platform`, `state`, `auth` (whether the identity is logged in), `source` (`minted` or `imported`), `browser`, `proxy` (its label, or `direct`), `fails` (consecutive failures), `cooldown`, `last used`.

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity list --platform tiktok --state active --limit 10
```

This table has ten columns, so it wraps hard in a narrow terminal — and at the 80 columns Rich falls back to when there is no terminal at all (under `-T`, or from cron) it is unreadable. Set `COLUMNS` when you want it wide:

```bash
docker compose -p dtk -f docker/compose.yml exec -e COLUMNS=200 api dtk identity list
```

### `dtk identity mint`

```text
dtk identity mint [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--platform` | `douyin` \| `tiktok`, **required** | — | Platform to mint for |
| `--count` | int, 1–20 | `1` | How many to mint |
| `--proxy` | string (full UUID) | a free proxy | Use this proxy instead of picking a free one |

Requires `DTK_BROWSER_RPC_URL`. Without it the command fails with exit 1 and tells you to set it or import cookies instead:

```text
error browser-rpc is not configured
hint: set DTK_BROWSER_RPC_URL, or import cookies instead
```

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity mint --platform douyin --count 2
```

Output columns: `id` (full), `proxy`, `exit ip`, `cookies` (names only).

Each identity is bound to one proxy for life. When `--proxy` is not given, a proxy with no identity on that platform yet is chosen, because two identities sharing an exit address on the same platform is exactly the correlation the pool exists to avoid. If there are no proxies at all, the identity is minted `direct` — usable, and a correlation risk once the pool is busy.

Cost: minting drives a real headless browser. The `browser-rpc` container is the heaviest thing in the stack (measured at 2.57 GiB resident and six cores' worth of CPU while warm), and `--count 20` is twenty page loads. This is not a command to run in a loop.

### `dtk identity retire`

```text
dtk identity retire [OPTIONS] {identity_id}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `identity_id` | string, required | — | Identity id, full or the shown prefix |
| `--reason` | string | `retired from the cli` | Recorded on the identity event |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk identity retire 9d8daf26 --reason "cookies rejected since the 9th"
```

**This wipes the cookie jar immediately and is not reversible.** There is no un-retire; a retired identity is dead and the pool has to mint or import a replacement.

Retiring an already-retired identity is refused rather than repeated, and that refusal is on purpose: a second call would overwrite the recorded reason and timestamp with `retired from the cli`, losing the record of why the identity was actually taken out of the pool.

This subcommand needs Redis, because retirement is announced to the running processes holding that identity's transport client.

### `dtk identity test`

```text
dtk identity test [OPTIONS] {identity_id}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `identity_id` | string, required | — | Identity id, full or the shown prefix |
| `--url` | string | the built-in smoke link for that identity's platform | Probe with this link instead |
| `--timeout` | float | `25.0` | Seconds for the request |

Makes one real signed request as that identity and reports what came back: `identity`, `platform`, `endpoint`, `proxy` (masked), `cookies` (names only), `ok`, `outcome`, `status`, `latency ms`, `rule`, `detail`.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk identity test 9d8daf26
```

No lease, no rate limit, no bookkeeping: the probe answers whether the platform still talks to this identity, and changes nothing. Probing an identity must not be the thing that cools it.

**A business error counts as a pass.** If the built-in link points at a post that has since been deleted or restricted, the platform explains why, and that is a real answer proving the identity works. The command exits 1 only when there was no usable answer at all — risk control, or a network failure.

## `dtk proxy`

Proxies: list, add, import and probe.

A proxy URL carries its own credentials, which is why the column is encrypted with `DTK_SECRET_KEY` and why every rendering goes through a mask. The plaintext exists for exactly as long as one probe takes.

Accepted schemes are `http`, `https`, `socks5` and `socks5h`; anything else is a typo or a copied web page. A URL must carry a host and a port.

The scheme and host rejections never quote the input, on purpose. `urlsplit` reads everything before the first colon as the scheme, so a vendor line pasted without one — `user:password@host:3128` — would put the account name, and in the wrong paste order the password, into the error message.

**One rejection does quote it, and it is worth knowing about.** The port check reads `urlsplit(...).port`, and when the text after the last colon is not a number `urllib` raises the message itself, with the offending token included: a line pasted as `http://myuser:hunter2` is rejected with `Port could not be cast to integer value as 'hunter2'` — the password. That message reaches the terminal, and `dtk proxy import` prints it next to the line number. Treat a rejection message as something that may carry a credential until this is fixed at the source.

### `dtk proxy list`

```text
dtk proxy list [OPTIONS]
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `--healthy` | flag | off | Only proxies that passed their last probe |

Columns: `id`, `label`, `url` (credentials masked — host and port survive, the username keeps two characters at most), `country`, `timezone`, `state`, `last check`.

`state` is `unchecked` until the proxy has been probed once, then `healthy` or `failed`. The underlying column defaults to true, so an unprobed proxy would otherwise read as healthy next to a last check of `never`.

### `dtk proxy add`

```text
dtk proxy add [OPTIONS] {url}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `url` | string, required | — | Proxy URL, for example `http://user:pass@host:3128` |
| `--label` | string | none | Name shown in listings |
| `--country` | string | none | ISO country of the exit |
| `--timezone` | string | none | IANA zone of the exit |

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk proxy add "http://user:pass@198.51.100.7:3128" --label "vendor-a-01" --country US --timezone America/New_York
```

The URL is encrypted before it reaches the database, and only the masked form is printed back.

`--country` and `--timezone` describe the exit and are used as a geo hint when an identity is minted on this proxy, so that a browser fingerprint does not claim a locale its IP address contradicts. You do not have to fill them in by hand: `dtk proxy test` learns both from the probe and writes them back.

Passing the URL on the command line puts credentials in your shell history. Prefer `dtk proxy import` with a file for anything you care about.

### `dtk proxy import`

```text
dtk proxy import {path}
```

The bulk path. Proxy vendors hand out a text file, and retyping forty lines into `add` is how people end up pasting the file into a chat window instead.

Format: one proxy URL per line, optionally followed by one or more **spaces** and a label. Blank lines and lines beginning with `#` are skipped, so a vendor's notes survive the import. A tab between the URL and the label is not supported — the line is split on the first space only, so a tab-separated line folds the label into the port and is rejected. If a vendor's file uses tabs, convert them first (`tr '\t' ' ' < vendor.txt > proxies.txt`).

```text
# vendor A, residential, delivered 2026-09-01
http://user:pass@198.51.100.7:3128    vendor-a-01
http://user:pass@198.51.100.8:3128    vendor-a-02
socks5://user:pass@203.0.113.9:1080   vendor-a-03
```

```bash
docker compose -p dtk -f docker/compose.yml exec api \
  dtk proxy import /tmp/proxies.txt
```

```text
id                                    url
b1f0c2d4-8e3a-4f51-9c07-2ad61e5b4c99  http://us***:***@198.51.100.7:3128
c3e2a7f1-55b9-4d0e-8a63-7f19c4d2e880  http://us***:***@198.51.100.8:3128
ok  added 2, skipped 1 duplicate(s), rejected 0 line(s)
```

Duplicates are detected by comparing against the decrypted URLs already stored, so re-importing a vendor's updated file adds only what is new. Rejected lines are reported by **line number and reason**, not by echoing the line. The reason is content-free for a bad scheme or a missing host — but not for a bad port, where `urllib`'s own message quotes the token it could not parse, and on a line whose credentials were pasted without an `@` that token is the password. See the caveat under [`dtk proxy`](#dtk-proxy) above before pasting a rejection list into a ticket.

To get the file into the container, pipe it in. `docker compose cp` does not work for `/tmp`: on a container whose root filesystem is marked read-only the daemon refuses any copy whose destination is not inside a volume, and a tmpfs is not one — a copy to `/tmp` fails with `Error response from daemon: container rootfs is marked read-only`. A path under `/var/lib/dtk/backups` does copy, because that is the `backup-data` volume, and the restore drill in [Operations](./10-operations.md) depends on exactly that. For `/tmp`, redirect into a shell inside the container instead:

```bash
docker compose -p dtk -f docker/compose.yml exec -T api sh -c 'cat > /tmp/proxies.txt' < ./proxies.txt
docker compose -p dtk -f docker/compose.yml exec api dtk proxy import /tmp/proxies.txt
docker compose -p dtk -f docker/compose.yml exec api rm -f /tmp/proxies.txt
```

Delete it afterwards, as above: `/tmp` is RAM, but the file holds proxy passwords in the clear until the container restarts.

### `dtk proxy test`

```text
dtk proxy test [OPTIONS] {proxy_id}
```

| Argument / option | Type | Default | Meaning |
|---|---|---|---|
| `proxy_id` | string, required | — | Proxy id, full or the shown prefix |
| `--probe-url` | string | `https://ipinfo.io/json` | Service that reports the exit address |
| `--write` / `--no-write` | flag | `--write` | Record the result on the proxy row |

Reports `id`, `state`, `latency ms`, `exit ip`, `country`, `timezone`, `detail`. The probe timeout is 10 seconds and is not exposed as a flag.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk proxy test b1f0
```

By default the result is written back: the healthy flag always, and country and timezone when the probe learned them. `--no-write` makes it a pure read, which is what you want when you are testing a probe URL rather than the proxy.

`--probe-url` exists because `ipinfo.io` is not reachable everywhere. Any service that answers with a JSON object carrying `ip`, `country` and `timezone` fields will do.

**One caveat that costs people an hour:** the probe is made with `httpx`, and `httpx` needs the optional `socksio` package to speak SOCKS. It is not among this project's dependencies, so a `socks5://` proxy that `dtk proxy add` accepted reports a *failed* probe whose detail names the missing package. Actual platform requests do not go through `httpx` — they go through a different HTTP client that handles SOCKS itself — so the proxy may well be working. Read a failed SOCKS probe as "the probe cannot tell you", not as "the proxy is dead".

## Safe on a live instance, and rescue commands

### Read-only — run these any time

Nothing here writes to the database or makes an outbound request.

| Command | Notes |
|---|---|
| `dtk --version` | Needs nothing at all |
| `dtk migrate --show` | No database connection, no `DTK_SECRET_KEY` |
| `dtk backup list` | Filesystem only |
| `dtk user list` | |
| `dtk config list`, `dtk config get` | |
| `dtk identity list` | |
| `dtk proxy list` | |

### Safe, but they cost something

These are fine on a live instance; know what you are spending.

| Command | What it costs |
|---|---|
| `dtk diagnose` | Real outbound requests: up to the 20 oldest proxies are probed (`MAX_PROXIES_PROBED`, so a larger pool is only partly covered), browser-rpc signs once per platform for the shadow comparison, and the smoke step spends one identity's request. Writes nothing. `--skip-smoke` removes the platform request |
| `dtk identity test` | One real platform request outside the scheduler. Records nothing |
| `dtk fetch` | One real platform request outside the scheduler, plus an archive row when `archive.enabled` is on |
| `dtk proxy test` | One outbound request; writes health and geo back unless `--no-write` |
| `dtk backup create` | Reads every exported table and gzips it. Minutes of CPU and possibly gigabytes of disk on a large archive |
| `dtk proxy add`, `dtk proxy import` | Adds rows, and they take effect at once: short-link expansion picks a random *healthy* proxy from the whole table, and a new row counts as healthy before it has ever been probed. It cuts the other way too — once any proxy row exists and none of them is healthy, expansion fails instead of going direct, so a table of dead proxies breaks short links. Nothing *signs* requests through a new proxy until an identity is minted on it |
| `dtk config set` | Applied immediately across every running process. Correct, and therefore capable of changing behaviour instantly |
| `dtk identity mint` | Drives the heaviest container in the stack, once per identity |

### Rescue commands — deliberate acts only

| Command | Why it is here |
|---|---|
| `dtk user passwd` | The way back in when nobody can log in. Works with Redis down |
| `dtk user create` | Creating an administrator from a shell, outside the setup flow |
| `dtk migrate` | Schema changes. Normally the `migrate` container's job; run by hand for a timed upgrade or a restored database |
| `dtk backup restore` | Rebuilding an instance. Needs the original `DTK_SECRET_KEY`; not a rollback |
| `dtk identity retire` | Irreversible. Wipes the cookie jar |
| `dtk serve` | Starting an API by hand. Lacks the hardening flags the container entrypoint adds |
| `dtk worker` | Starting a worker by hand. Use `--scale worker=N` for real scaling |

The one thing to avoid outright: `dtk backup restore -y` against a live instance you have not read the manifest for. The prompt exists because the manifest tells you what you are about to merge in, and `-y` is for a script that already knows.

## Where to go next

- [Operations](./10-operations.md) — backups, upgrades, monitoring and what to watch
- [Troubleshooting](./14-troubleshooting.md) — reading a `dtk diagnose` report, and what each failure means
- [Configuration reference](./03-configuration.md) — every setting `dtk config` can read and write
- [Identities and proxies](./06-identities-and-proxies.md) — what the pool is doing and why
- [Users and API keys](./09-users-and-api-keys.md) — roles, scopes and the console side of accounts
- [Security](./15-security.md) — what `DTK_SECRET_KEY` protects and what happens if it changes
