# Quick start

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/01-quickstart.md)

By the end of this page you will have the stack running on one host, an
administrator account, and one link parsed successfully — first from the web
console, then from `curl` with an API key. Budget about ten minutes of your
attention plus however long the first image build takes.

Everything here is run from the repository root, and every compose command
carries both the project name `-p dtk` and the compose file
`-f docker/compose.yml`. `docker/compose.yml` already declares `name: dtk`, so
`-p dtk` only makes the target explicit rather than changing it — keep it
anyway. `-f` is the half you cannot drop: there is no compose file at the
repository root, so without it the command finds no configuration at all.

## Before you start

| Requirement | Detail |
|---|---|
| Docker Engine + Compose v2 | Check with `docker compose version`. The compose file uses the `env_file` long syntax (`path:` / `required:`), which needs Compose v2.24 or newer. |
| `git` | To obtain the source. There is no published image registry for this project; the application images are built locally on the first `up`. |
| `openssl` | Generates the secrets in step 2. Any CSPRNG will do, but the commands below use `openssl`. |
| RAM | 2 GB is enough for the core stack in practice; the container ceilings in `docker/compose.yml` sum to 3.5 GiB, so 4 GB is the safe figure. 8 GB if you also run the browser container. |
| Disk | Roughly 4.5 GB of images for the core stack, ~6.3 GB with the browser container, before volumes and build cache. 15 GB free is comfortable. |
| Outbound network | The containers need to reach Docker Hub to pull Postgres and Redis, and the platforms (directly or through a proxy) to fetch anything. |

Image sizes, measured with `docker images` on a working install:

| Image | Where it comes from | On disk |
|---|---|---|
| `timescale/timescaledb-ha:pg17` | pulled | ~3.9 GB |
| `dtk-app:dev` — api, worker, migrate | built here | ~420 MB |
| `redis:8-alpine` | pulled | ~160 MB |
| `dtk-browser-rpc:dev` — `--profile browser` | built here | ~1.7 GB |
| `dtk-downloader:dev` — `--profile downloader` | built here | ~9 MB |

Memory, as measured at rest against the ceiling `docker/compose.yml` sets for
each service. The ceilings exist so that one overloaded component cannot
destabilise the rest; they are not sized budgets the services have to live
inside:

| Service | At rest | compose ceiling |
|---|---|---|
| `postgres` | 112 MiB | `mem_limit: 2g` |
| `api` | 104 MiB | `mem_limit: 512m` |
| `worker` | 71 MiB | `mem_limit: 512m` |
| `redis` | 8 MiB | `mem_limit: 512m` |
| `browser-rpc` | 2.57 GiB warm, 629% CPU | `mem_limit: 4g`, `cpus: 4.0` |
| `downloader` | streams with 32 KiB buffers per file | `mem_limit: 512m` |

`browser-rpc` is the whole reason for the 8 GB figure. Its browser profiles live
on tmpfs (`/tmp` at 3g, `/profiles` at 1g), and tmpfs is RAM, not free disk.

## Step 1 — Get the source

v5 is a rewrite that shares no code with V4. Clone the repository — the default
branch is v5:

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

Confirm you have the right tree before going further — if this file is missing,
you are on V4 and nothing below will work:

```bash
ls docker/compose.yml
```

## Step 2 — Write `.env`

Nothing in this repository ships a default password or key, and the containers
refuse to start without one. `DTK_SECRET_KEY` is the master key that encrypts
every stored cookie and proxy credential; a key that is identical on every
install is not encryption, which is why there is no default to fall back on.

Write the file at the **repository root**, not in `docker/`:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
cat > .env <<EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
DTK_DATABASE_URL=postgresql+asyncpg://dtk:${POSTGRES_PASSWORD}@postgres:5432/dtk
DTK_REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
EOF
```

| Variable | What it is |
|---|---|
| `DTK_SECRET_KEY` | Master key for credential encryption. Must be at least 32 characters or every role exits `78` (`EX_CONFIG`) before touching the database. |
| `POSTGRES_PASSWORD` | Password for the `dtk` database role, consumed by the postgres container. |
| `REDIS_PASSWORD` | Redis `requirepass`. The redis container refuses to start if it is empty. |
| `DTK_DATABASE_URL` | How the application reaches Postgres. It repeats `POSTGRES_PASSWORD`, which is why the recipe above assembles it rather than asking you to retype it. |
| `DTK_REDIS_URL` | The same for Redis. |

Two details that save time later:

- The two passwords are generated as **hex, not base64**, because they end up
  inside URLs where `+` and `/` would have to be escaped.
- `.env` is git-ignored and listed in `.dockerignore`, so it reaches the
  containers as an env file and never as image content.

`.env` is only the bootstrap layer. Everything else — cache TTLs, pool sizes,
retention, rate limits — is seeded from the environment once at first init and
then lives in the database, editable from the console. See
[Configuration reference](./03-configuration.md).

> Changing `DTK_SECRET_KEY` after the fact makes already-stored cookies and
> proxy credentials undecryptable. That is recoverable — retire the affected
> identities and mint or import them again — but it is not free. Generate it
> once and back it up with the same care as the database.

## Step 3 — Decide about the browser container

This is the decision that determines whether your first request works, so make
it now rather than discovering it in step 9.

The service maintains a pool of *identities*: cookies, a browser fingerprint and
an optional proxy, bound together and rotated by the scheduler. Identities come
from exactly two places, and one of them is optional infrastructure.

**With the browser container** (`--profile browser`, plus
`DTK_BROWSER_RPC_URL` pointing at it): a headless browser mints guest identities
by itself. The worker checks each platform every 60 seconds and mints **one
identity per tick**, backing off exponentially on failure, until the pool is
back at `pool.target_size` (8 by default) from its low-water mark
`pool.min_size` (3). Refilling therefore takes minutes, on purpose: five fresh
visitors appearing from one deployment inside a second is a stronger anomaly
than anything those identities would go on to do.

**Without it** (the default — `DTK_BROWSER_RPC_URL` empty): the refill job is
skipped entirely, so **the pool starts empty and stays empty**. Concretely:

- The console's Identities page shows minting as unavailable, and the setup
  wizard's mint step says so rather than failing.
- `POST /api/v1/admin/identities/mint` still accepts the request — it answers
  `202` with `task_ids` — and the queued task then fails with
  `501 NOT_CONFIGURED`, "This feature is not configured on this instance.",
  carrying `error.details.reason: "browser_rpc_unconfigured"`. The mint call
  takes no `?wait=`, so you read that verdict on the task
  (`GET /api/v1/tasks/<task_id>`) or in the console, not in the reply to the
  mint itself.
- Every content request fails with `503 IDENTITY_POOL_EXHAUSTED` — "No identity
  is available; the pool is expected to recover in N seconds" — until you import
  at least one cookie jar.

Running without the browser is a supported deployment, not a broken one: a user
who imports a cookie jar by hand does not need the heaviest container in the
stack, and the pool falls back to manual import on its own when `browser-rpc` is
unreachable. But it is not a deployment that works out of the box. Pick one:

| Path | Do this | Cost |
|---|---|---|
| Automatic minting | Start with `--profile browser` in step 4 | ~1.7 GB image, 2.57 GiB RAM warm, up to 4 CPUs, a longer build |
| Manual cookies | Skip the profile; import a jar on the Identities page after step 6 | You supply and refresh the cookies; a logged-in jar is equivalent to the account password |

If you choose the browser, note one thing that trips everyone up:
`CLOAKBROWSER_COMMIT` is read at **build time**, and it is a compose
*interpolation* variable — compose reads those from `docker/.env` or from your
shell, **not** from the repository-root `.env`. Passing it on the command line
is the reliable way. It is deliberately pinned to a commit rather than a tag
because several GitHub organizations publish identically described
`cloakbrowser` repositories, so a floating tag would not identify any particular
piece of software. An image built with it empty contains no browser at all and
says so on `/rpc/health`, rather than quietly minting synthetic identities that
no platform accepts.

## Step 4 — Start the stack

Core stack (postgres, redis, migrate, api, worker):

```bash
docker compose -p dtk -f docker/compose.yml up -d --wait --wait-timeout 300
```

The first run builds `dtk-app` — a Node stage for the console, a `uv sync
--frozen` stage for Python — so expect several minutes. `--wait` returns when
the API's `/readyz` probe passes, so a successful exit means the stack can
actually serve, not merely that containers exist.

With the browser container as well:

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
docker compose -p dtk -f docker/compose.yml --profile browser ps
```

That commit is CloakBrowser 0.5.10 / Chromium 146, the revision this repository
has been verified against end to end; it is in `.env.example` for the same
reason — as a reference value, not as a working configuration. Copying
`.env.example` to the repository-root `.env` does **not** get the pin into the
build, because compose interpolation does not read that file: pass it on the
command line as above, put it in `docker/.env`, or feed the root file to Compose
with `COMPOSE_ENV_FILES=.env`. Change it deliberately. `browser-rpc` has a
60-second healthcheck `start_period`, so give it a minute before reading `ps` as
a verdict.

### Where the API is published

Only `api` publishes a port, and by default it binds to loopback:
`127.0.0.1:8000`. That address comes from compose *interpolation*, which reads
`docker/.env` or your shell — **not** the repository-root `.env`. Setting
`DTK_BIND_HOST` or `DTK_BIND_PORT` in the root `.env` therefore changes nothing:
the api container's own values are pinned in `compose.yml`, and the published
address is not read from that file.

| You want | Command |
|---|---|
| Loopback only (default) | `docker compose -p dtk -f docker/compose.yml up -d` |
| Every interface | `DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d` |
| The root `.env` to drive it | `COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d` |

Serving the outside world means putting TLS in front of it yourself, and setting
`DTK_FORWARDED_ALLOW_IPS` so the per-address login limit means one caller rather
than the whole internet. Neither is done for you. See
[Installation and deployment](./02-installation.md) and
[Security](./15-security.md) before you publish beyond loopback.

Postgres and Redis sit on a network declared `internal: true` — no route off the
host at all — and neither is published. That is deliberate and you should not
change it to "make debugging easier".

## Step 5 — Find the setup token

A fresh deployment has no account, which leaves a window in which whoever
arrives first becomes the administrator. Source-address checks cannot close that
window: Docker's published-port userland proxy rewrites the peer address to the
bridge gateway, so "only accept private addresses" would admit the entire
internet. The gate is instead a one-time token that exists only in the container
log.

```bash
docker compose -p dtk -f docker/compose.yml logs api
```

Look for the banner:

```text
==========================================================================
  dtk is not initialized yet.
  Open this URL to create the administrator account:

    http://127.0.0.1:8000/setup?token=Xf3k...

  The token expires in 24 hours.
  To issue a new one: docker compose restart api
==========================================================================
```

The banner's last line is a shorthand, and it is incomplete: `docker compose
restart api` carries neither `-p dtk` nor `-f docker/compose.yml`, and there is
no compose file at the repository root, so run as printed it fails with `no
configuration file provided`. The command that works is
`docker compose -p dtk -f docker/compose.yml restart api`.

Or pull the URL straight out:

```bash
docker compose -p dtk -f docker/compose.yml logs api \
  | grep -oE 'http://[^ ]*/setup\?token=[A-Za-z0-9_-]+' | tail -1
```

What is true about that token:

| Property | Value |
|---|---|
| Where it lives | Redis only. Never in an HTTP response, never in the database, never on disk. |
| Lifetime | 24 hours from issue. |
| Wrong attempts | Five invalidate the current token. |
| After an account exists | `POST /api/setup/init` answers `409` permanently, and the token is deleted. |
| Restarting `api` | Re-prints the token. A live token is reused and re-announced, not replaced — a link you are already holding keeps working. |

The banner always says `127.0.0.1:8000` because the api container's
`DTK_BIND_PORT` is pinned to `8000` inside compose. If you published somewhere
else, keep the `?token=` part and change the host and port yourself.

## Step 6 — Run the setup wizard

Open the URL. The wizard is four steps, and the fourth is the point: a
deployment that only proves it can create an account teaches you nothing about
whether the proxy, the pool and the signer work — you would find that out at
your first real API call, which is the worst possible moment.

| Step | What it does | Skippable |
|---|---|---|
| Create an administrator | Consumes the token and creates the first account, with the `admin` role. | No |
| Add a proxy | Paste one per line, then probe. Accepted forms: `host:port`, `host:port:user:pass`, `user:pass@host:port`, `scheme://user:pass@host:port`. | Yes |
| Mint the first identity | Queues a mint job per identity. Takes a few seconds each. Unavailable without the browser container. | Yes |
| Smoke test | `POST /api/v1/parse` on a real public link, `?wait=20`, falling back to polling — the same path any client takes. | Yes |

The administrator's credentials:

- Username must match `^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$` — letters, digits,
  dot, dash, underscore, 3 to 64 characters, not starting with punctuation.
- Password: the API's floor is 8 characters, the wizard asks for 12.
- **There is no password reset by email.** Recovery is a CLI command:
  `docker compose -p dtk -f docker/compose.yml exec api dtk user passwd <name>`.

Without a proxy, every identity shares the server's own egress address. That is
workable for a personal instance and is the wrong choice for anything with real
traffic — see [Identities and proxies](./06-identities-and-proxies.md).

If you skipped the mint step because you have no browser container, go to
**Identities → Import cookies** now. The importer accepts a request header, an
extension's JSON export, a Netscape `cookies.txt`, or one `key=value` per line,
and shows you what it understood before storing anything. Supply the User-Agent
from the same browser session, so the fingerprint matches the cookies. Use an
account kept for this purpose: a logged-in cookie set is equivalent to that
account's password.

The smoke test shows you the platform, the content id, the duration, the request
id and the parsed JSON. That is your first successful parse.

## Step 7 — Parse a link from the console

The wizard's smoke test runs once. For a repeatable call, use **Playground** in
the sidebar (under *Tools*).

1. Pick `POST /api/v1/parse` from the catalogue on the left.
2. Put a share link, a bare post URL, or the whole clipboard text a platform app
   produces into `url`. Short links (`v.douyin.com`, `vm.tiktok.com`) are
   followed for you.
3. Set `wait` to something like `20`.
4. Submit.

The Playground exists so a dead endpoint can be diagnosed in three minutes: a
failure shows the stable error code, the request id, which identity served the
call, whether that endpoint's circuit is open, and what the pool looks like —
the four facts that separate "the platform changed" from "we are out of
identities". It also generates a copy-ready `curl` line from the form as it
stands, which is the fastest way to get to the next step.

## Step 8 — Create an API key

The console uses a session cookie. Programs use an API key, because a key
carries scopes and can be revoked on its own.

Go to **API keys → Create key**. Give it a name you will recognise in the audit
log, and tick only what it needs — for this walkthrough, `douyin:read` and
`tiktok:read`.

| Scope | What it opens |
|---|---|
| `douyin:read` | Parse, post, author and comment endpoints for Douyin |
| `tiktok:read` | The same endpoints for TikTok |
| `archive:read` | What this instance has already stored, answered from local storage |
| `archive:export` | Walk the entire archive in one request — the single call that turns a read key into a copy of the database |
| `media:read` | What media is on the server's disk, and how much space it uses |
| `media:write` | Start a download, pin one against cleanup, or cancel one |
| `identity:manage` | Mint, import, test and retire identities. An ordinary read key must never carry this |
| `admin` | Everything, settings and users included |

Two rules worth knowing before you design your key layout:

- A key can never be given a scope its creator does not hold. An API key used to
  create another key cannot escalate.
- Scopes bind even for a key owned by an administrator. A plain read key cannot
  reach identity management no matter who created it.

`rate_limit` is requests per minute and defaults to the instance's
`api.default_rate_limit_per_min` (120). It is abuse protection. This project has
no billing, plans or quota sales, and `rate_limit` and `quota` never mean money
anywhere in these documents.

The full key — `dtk_<prefix>_<secret>` — is shown **once**, immediately after
creation. The server stores only the prefix and a SHA-256 hash of the whole
thing. Nobody, administrators included, can read it again; the only recovery
from losing it is revoking the key and creating another.

## Step 9 — The same parse from `curl`

Every endpoint requires an API key or a console session by default. Authenticate
with the `X-API-Key` header:

```bash
API_KEY='dtk_...'   # the key from step 8

curl -sS -X POST 'http://127.0.0.1:8000/api/v1/parse?wait=25' \
  -H "X-API-Key: ${API_KEY}" \
  -H 'content-type: application/json' \
  -d '{"url":"https://v.douyin.com/iRNBho6G/"}'
```

Fetching from a platform costs a real upstream call on a real identity and can
take seconds, so the data endpoints are asynchronous by default. `?wait=` moves
the polling loop to the server — it is a convenience for clients that cannot
poll, and it changes nothing internally.

| `?wait=` | Result |
|---|---|
| omitted, or `0` | `202` immediately, with `task_id` and `state` |
| a value up to the ceiling | Held until the task settles. Finished in time: `200` with the result. Not finished: `202` with `state: "running"` — **not an error, and nothing is lost** |
| above `api.max_wait_seconds` (30 by default) | `400`, rejected rather than quietly shortened |
| negative | `400` |

A success looks like this — the same four-key envelope every response uses:

```json
{
  "success": true,
  "data": {
    "platform": "douyin",
    "content_id": "7300000000000000000",
    "kind": "video",
    "web_url": "https://www.douyin.com/video/7300000000000000000",
    "title": "…",
    "author": { "uid": "MS4wLjABAAAA…", "nickname": "…" },
    "stats": { "play_count": 0, "digg_count": 0, "comment_count": 0 },
    "media": { "covers": [], "video": { "url": "https://…" } }
  },
  "error": null,
  "meta": {
    "request_id": "0f0a…",
    "cached": false,
    "duration_ms": 1840,
    "task_id": "8c1e…"
  }
}
```

If you got a `202` instead, poll the task with the id it handed back:

```bash
curl -sS -H "X-API-Key: ${API_KEY}" \
  'http://127.0.0.1:8000/api/v1/tasks/<task_id>'
```

A failure uses the identical envelope, with `data: null` and a **stable,
untranslated** `error.code` beside a message that is localized. Branch on the
code; never parse the message. Append `?lang=zh` to any endpoint — this document
included — for Chinese messages.

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "IDENTITY_POOL_EXHAUSTED",
    "message": "No identity is available; the pool is expected to recover in 10 seconds.",
    "retry_after": 10
  },
  "meta": { "request_id": "0f0a…" }
}
```

The `message` is English here because the command above names no language and
the instance default `api.default_language` is `en`; add `?lang=zh` and the same
error renders in Chinese. The `code` is identical either way.

That particular code means step 3 caught up with you: there is no usable
identity. Mint one, or import a cookie jar.

## Did it work?

Run through these. Each one is a different failure, so the first that fails tells
you where to look.

```bash
# 1. Every service up, migrate exited 0, api and worker healthy
#    -a because `migrate` is a one-shot task and has already exited
docker compose -p dtk -f docker/compose.yml ps -a

# 2. The API can serve: postgres and redis both reachable
curl -sS http://127.0.0.1:8000/readyz

# 3. The console is served
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/

# 4. Initialization is closed, i.e. an administrator exists
curl -sS http://127.0.0.1:8000/api/setup/status

# 5. The full six-step self-check, including a real end-to-end request
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

| Check | What you should see |
|---|---|
| `ps -a` | `postgres`, `redis`, `api`, `worker` running; `api` and the two stores healthy; `migrate` `Exited (0)`. With the browser profile, `browser-rpc` healthy after ~1 minute. |
| `/readyz` | `{"status":"ok","components":{"postgres":{"ok":true,…},"redis":{"ok":true,…}}}` and HTTP 200. A 503 means one of the two stores is unreachable. |
| `/` | `200`. A `404` means the console build stage produced no `dist/`. |
| `/api/setup/status` | `{"success":true,"data":{"initialized":true},…}` |
| `dtk diagnose` | Every step `PASS`, or a `WARN` you understand. The output is redacted and meant to be pasted into a bug report. |
| Console → Identities | At least one identity in `active`. An empty pool is the single most common reason a correct request still fails. |
| Console → Playground | `POST /api/v1/parse` returns `200` with a `data.content_id`. |

`/healthz` and `/readyz` are deliberately different: `/healthz` touches no
dependency, so a database outage cannot fail it, while `/readyz` probes Postgres
and Redis. `browser-rpc` is excluded from readiness on purpose — minting is not
on the request path, so an instance without it is degraded, not unready.

## If something went wrong

| Symptom | Cause |
|---|---|
| `refusing to start: DTK_SECRET_KEY is not set` (exit `78`) | `.env` is missing at the repository root, or is in `docker/` instead. Write it with the recipe in step 2. |
| `redis: REDIS_PASSWORD is empty` | Same cause, same fix. |
| No setup banner in the api log | An account already exists — the token is issued only while the `users` table is empty. Sign in at `/login` instead. |
| `SETUP_TOKEN_INVALID` | The token expired, was spent, or was invalidated by five wrong attempts. `docker compose -p dtk -f docker/compose.yml restart api` prints a live one, or issues a new one if the old is gone. |
| `503 IDENTITY_POOL_EXHAUSTED` | The pool has no usable identity. Mint one, or import a cookie jar. See step 3. |
| A mint task fails `501 NOT_CONFIGURED` (the mint call itself returned `202`) | No browser container. Start `--profile browser` and set `DTK_BROWSER_RPC_URL`, or import cookies instead. |
| `browser-rpc` unhealthy, log says "not installed in this image" | Built without `CLOAKBROWSER_COMMIT`, which is read at build time from the shell, not from the root `.env`. |
| Minting always times out | The proxy cannot reach the platform. Test the proxy on its own before blaming the browser. |
| `RATE_LIMITED` on a correct password | The per-account login limit is a real lockout: five failures, fifteen minutes. Wait, or clear the counter — see [Troubleshooting](./14-troubleshooting.md). |

For anything not on that list, [Troubleshooting](./14-troubleshooting.md) is
organized by symptom.

## What to read next

- [Installation and deployment](./02-installation.md) — reverse proxies, the
  downloader sidecar, backups, upgrading, scaling workers.
- [Concepts](./04-concepts.md) — identities, the scheduler, signing, the request
  log. Read this before you tune anything.
- [Identities and proxies](./06-identities-and-proxies.md) — how the pool refills
  itself, and what to do when it will not.
- [Console overview](./05-console-overview.md) — what each page is for.
- [REST API guide](./11-api.md) — the full endpoint surface, pagination,
  callbacks and error codes. The live reference is at `/docs` in the console, or
  at `/swagger` and `/redoc` without a login.
- [Users and API keys](./09-users-and-api-keys.md) — roles, scopes and what to
  hand a script.
- [MCP and AI agents](./12-mcp.md) — the same service layer at `/mcp`, with
  client setup at `/mcp-guide` in the console.
- [Security](./15-security.md) — read this before you expose the port.
