# Troubleshooting

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/14-troubleshooting.md)

Find your symptom, confirm the cause with one command or one console page, and apply the fix. This page also carries the complete list of error codes this instance can return, with what each one means and whether retrying it can ever help.

Every command below is written to be run from the repository root. Compose commands always carry the compose file (`-f docker/compose.yml`) — there is no `compose.yml` at the repository root, so without it compose finds nothing to act on — and the project name (`-p dtk`). The compose file already declares `name: dtk`, so `-p dtk` only makes the target explicit; keep it anyway, because it is also what overrides a `COMPOSE_PROJECT_NAME` already set in your shell.

---

## Start here: the self-check

Almost every "I deployed it and I get no data" report comes down to one of four things — the network, a proxy, the cookie jar, or the signature — and you cannot inspect any of them from the outside. The self-check walks all six layers and prints one report.

**From the console:** open `/diagnose`, press **Run self check**. The report is rendered on the page and there is a **Copy report** button for the whole text.

**From the command line:**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose
```

The two run the same code. The difference is where: the console submits a background task, so the worker container has to be running for it to finish, while the CLI runs the six steps in the process you just started. **If the Diagnose page never leaves "running", the worker is down** — use the CLI instead, then read [The stack will not start](#the-stack-will-not-start). **And if `exec api` itself fails** — compose answers that the service is not running, or that there is no such container — then there is no api container to run the check in and neither surface can help: go straight to that same section.

### The six steps

| # | Step | What it proves |
|---|---|---|
| 1 | `components` | This process can reach PostgreSQL, Redis and browser-rpc. Postgres and Redis failing is a `fail`; browser-rpc failing is only a `warn`, because it is optional |
| 2 | `egress` | This machine can reach `https://www.douyin.com/` and `https://www.tiktok.com/` **directly**, with no proxy. Separates "no internet" from "bad proxy" |
| 3 | `proxies` | Each configured proxy carries traffic, and where it comes out (exit IP, country). At most 20 proxies are probed |
| 4 | `pool` | How many identities are `active`, and when the last successful request was |
| 5 | `signing` | The in-process signing algorithms produce the same signature a real browser does, per platform. Skipped when browser-rpc is not configured — there is nothing to compare against |
| 6 | `smoke` | One fixed public link, through the whole pipeline. This is the last step of the setup wizard too |

Each step has a stable `code` (for example `pool_below_minimum`), a sentence, and — when there is something to do about it — a suggested action. The verdict is the worst status any step reached: `pass`, `warn` or `fail`. A warning is a note, not a broken instance: `dtk diagnose` exits 0 on warnings and 1 only when a step failed.

### The report is safe to paste

Proxy passwords, cookie values, API keys and signature parameters are masked by the renderer before the report leaves the process, and masked again when the text is laid out. That is deliberate: the report exists to be pasted into a public issue. Values that are only evidence — a database driver's error text, a step's raw detail — stay in English and stay verbatim, because that is the string a maintainer can search for.

Useful CLI flags:

```bash
# skip the end-to-end request (fastest, no identity spent)
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --skip-smoke

# machine-readable
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose --json

# smoke-test a specific link, on a specific platform
docker compose -p dtk -f docker/compose.yml exec api dtk diagnose \
  --platform tiktok --smoke-url 'https://www.tiktok.com/@someone/video/123'
```

---

## Symptom index

| Symptom | Section |
|---|---|
| `docker compose up` exits immediately, or a container restarts in a loop | [The stack will not start](#the-stack-will-not-start) |
| `migrate` exits non-zero; api never starts | [Migrations fail](#migrations-fail) |
| Browser cannot connect to `127.0.0.1:8000` | [Cannot reach the console](#cannot-reach-the-console) |
| The setup link is gone, or the token is rejected | [Lost the setup token](#lost-the-setup-token) |
| Nobody can log in to the console | [Forgot the admin password](#forgot-the-admin-password) |
| Correct password, still refused with `RATE_LIMITED` | [Locked out of the login page](#locked-out-of-the-login-page) |
| Every request answers `IDENTITY_POOL_EXHAUSTED` | [The identity pool is empty](#the-identity-pool-is-empty) |
| Identities go `cooling` as soon as they are used | [Identities go cooling immediately](#identities-go-cooling-immediately) |
| `ENDPOINT_CIRCUIT_OPEN` on one endpoint | [A circuit breaker is open](#a-circuit-breaker-is-open) |
| Requests take seconds, or return `202` instead of data | [Requests are slow](#requests-are-slow) |
| `RATE_LIMITED` on your own API key | [Rate limited](#rate-limited) |
| `401` or `403` and you cannot tell which is which | [401 or 403](#401-or-403) |
| A link is rejected as `INVALID_URL` | [INVALID_URL, and the eight reasons behind it](#invalid_url-and-the-eight-reasons-behind-it) |
| `NOT_FOUND` for a post you can open in a browser | [NOT_FOUND on a post that exists](#not_found-on-a-post-that-exists) |
| `UPSTREAM_RISK_CONTROL` | [UPSTREAM_RISK_CONTROL](#upstream_risk_control) |
| `SIGNING_FAILED`, or empty responses with no error | [Signature errors and the signing self-check](#signature-errors-and-the-signing-self-check) |
| browser-rpc is down or was never started | [The browser container is unavailable](#the-browser-container-is-unavailable) |
| Downloads fail, or the files are not there | [Downloads fail or files are missing](#downloads-fail-or-files-are-missing) |
| Collection stopped on its own; `CAPACITY_PAUSED` alert | [The disk is full](#the-disk-is-full) |
| A proxy is marked unhealthy | [Proxies fail their probe](#proxies-fail-their-probe) |
| An MCP client cannot connect to `/mcp` | [MCP client cannot connect](#mcp-client-cannot-connect) |
| Tasks stay `queued` forever, or `QUEUE_FULL` | [Tasks stay queued](#tasks-stay-queued) |
| `UPSTREAM_CHANGED` naming a field | [UPSTREAM_CHANGED](#upstream_changed) |

---

## How to read a failure

Every response from this API, success or failure, has the same four top-level keys:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "IDENTITY_POOL_EXHAUSTED",
    "message": "No identity is available; the pool is expected to recover in 10 seconds.",
    "retry_after": 10,
    "details": { "endpoint": "douyin.content_detail", "reject_reason": "no_token" }
  },
  "meta": { "request_id": "…" }
}
```

(`error.message` is rendered in this instance's default language — `api.default_language`, `en` out of the box. Add `?lang=zh` to a request to get it in Chinese.)

Three things matter when you are debugging:

- **`error.code`** is the stable contract. Branch on it, key runbooks off it, quote it in an issue. `error.message` is localized and must never be parsed.
- **`meta.request_id`** is the handle, with one qualification worth knowing. The id that comes back with a **finished** result — a `?wait=` call that completed, or `result_meta` on a polled task — is the worker's own fetch id, and that is the id on the `request_log` row, so the console's **Logs** page (`/logs`) can find exactly the request you are holding — filter by `request_id`. The id in a `202` submission response is the HTTP correlation id (the same value as the `X-Request-ID` header); nothing has been fetched yet, so it has no `request_log` row of its own. The Logs page cannot see further back than its window (default 60 minutes, ceiling 30 days) and the rows themselves are deleted after `retention.request_log_days` (14 days by default).
- **`retry_after`**, when present, is also sent as the `Retry-After` HTTP header.

MCP tool errors use the same envelope, so one error handler covers both surfaces.

---

## Every error code

Read from `src/dtk/core/errors.py`. "Retryable" is what the code itself declares: a code marked **no** is in the non-retryable set, which is also what the MCP tools tell an agent, so it does not burn its budget looping on a permanent failure.

| Code | HTTP | Retryable | What it means |
|---|---|---|---|
| `INVALID_URL` | 400 | no | The URL was not recognized as a supported Douyin or TikTok link. `error.details.reason` says why — see [below](#invalid_url-and-the-eight-reasons-behind-it) |
| `UNSUPPORTED_CONTENT` | 400 | no | The link or capability is real but this platform adapter does not serve it (for example Douyin follower lists without a signed-in identity) |
| `INVALID_PARAM` | 400 | no | A request parameter is invalid. `details.field` names it |
| `UNAUTHENTICATED` | 401 | no | No credential, or one that was rejected |
| `FORBIDDEN_SCOPE` | 403 | no | The credential is valid but lacks the scope or role this endpoint needs |
| `NOT_FOUND` | 404 | no | The resource does not exist or is unavailable. For content, this is what a platform business error becomes |
| `CONTENT_PRIVATE` | 403 | no | The content is private or was removed by its author |
| `RATE_LIMITED` | 429 | yes | Abuse protection tripped. Also what a login lockout returns |
| `IDENTITY_POOL_EXHAUSTED` | 503 | yes | No identity could be leased in time |
| `ENDPOINT_CIRCUIT_OPEN` | 503 | yes | The endpoint is paused after failures across several identities |
| `UPSTREAM_RISK_CONTROL` | 502 | yes | The platform refused this identity; it has been cooled |
| `UPSTREAM_CHANGED` | 502 | no | The platform response no longer matches what the parser expects. `details.path` names the field |
| `SIGNING_FAILED` | 502 | yes | No usable signer, or the signer raised |
| `TASK_NOT_FOUND` | 404 | yes | No task with that id, or its result already expired. Retryable in general — but once a result has expired, no retry brings it back; submit the work again |
| `SETUP_ALREADY_DONE` | 409 | no | This instance already has an administrator |
| `SETUP_TOKEN_INVALID` | 403 | no | The setup token is wrong or has expired |
| `NOT_CONFIGURED` | 501 | no | This deployment never had that component. No amount of waiting installs a browser container |
| `QUEUE_FULL` | 503 | yes | The task queue is at `sched.queue_max`, or new downloads are paused because the disk is full |
| `DOWNLOADER_UNAVAILABLE` | 503 | yes | The media sidecar is configured but did not answer |
| `CANCELLED` | 409 | no | Somebody stopped this task. Retrying ignores them |
| `METHOD_NOT_ALLOWED` | 405 | no | Wrong HTTP method for this path |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | no | Wrong request body media type |
| `INTERNAL` | 500 | yes | An unexpected failure. Check the server logs; a repeated one is a bug worth reporting |

`NOT_CONFIGURED` and `DOWNLOADER_UNAVAILABLE` look similar and are deliberately different: the first is a permanent property of the install ("there is no downloader here"), the second is a service that is down ("the downloader exists and did not answer"). They want opposite things from the caller.

---

## The stack will not start

### `refusing to start: DTK_SECRET_KEY is not set`

**What it means.** The entrypoint gates every role — `api`, `worker` and `migrate` — on `DTK_SECRET_KEY`, and exits `78` (`EX_CONFIG`: the configuration is wrong, retrying will not help). The image ships no default because a key that is identical on every install is the same as no encryption at all: that key encrypts every stored cookie and every proxy credential.

**Confirm it.**

```bash
docker compose -p dtk -f docker/compose.yml logs api | tail -20
```

**Fix.** Write a key at least 32 characters long into `.env` **at the repository root** (not `docker/.env`):

```bash
echo "DTK_SECRET_KEY=$(openssl rand -base64 48)" >> .env
docker compose -p dtk -f docker/compose.yml up -d
```

**The cost of changing it later.** Credentials already stored are encrypted under the old key and become undecryptable. Identities behind a rotated key have to be retired and minted or imported again; proxies have to be re-entered. See [Security](./15-security.md).

### `redis: REDIS_PASSWORD is empty; write .env first`

Redis refuses to start without a password. The same `.env` needs `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `DTK_DATABASE_URL` and `DTK_REDIS_URL`; see [Installation and deployment](./02-installation.md) for the block that generates all of them.

### The variables are in `.env` and the containers still do not see them

Compose reads that file in two different ways, and this catches people:

- **Service variables** come from `env_file: ../.env`, whose path compose resolves relative to `docker/compose.yml` — so the repository-root `.env` is used even though the project directory is `docker/`.
- **Interpolation inside `compose.yml`** — the `${…}` expressions in the file itself — does **not** read that file. It reads `docker/.env` or your shell. Six variables appear there: `DTK_BIND_HOST` and `DTK_BIND_PORT` (the published address, `${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000`), `DTK_IMAGE_TAG` (the image tags), `DTK_REDIS_MAXMEMORY` (the redis ceiling), and `CLOAKBROWSER_REPO` / `CLOAKBROWSER_COMMIT` (build arguments for the browser image). The full breakdown is in [Installation and deployment](./02-installation.md).

So any of those six in the root `.env` does nothing — `DTK_BIND_HOST` and `CLOAKBROWSER_COMMIT` are the two that catch people. Pass them on the command line, or point compose at the root file:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
# or point compose at the root file:
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

### A container is `unhealthy` and its dependants never start

`api` and `worker` both wait on `postgres` (healthy), `redis` (healthy) and `migrate` (completed successfully). One unhealthy dependency stops the rest.

```bash
docker compose -p dtk -f docker/compose.yml ps
docker compose -p dtk -f docker/compose.yml logs postgres redis migrate
```

The api's healthcheck is `GET /readyz` — readiness, not liveness — with a 40-second start period. That is why `up --wait` returns only when the API can actually serve. browser-rpc is deliberately absent from readiness: an unreachable RPC service must never take the instance out of rotation.

### The api container restarts in a loop

Check whether it is being OOM-killed before you look at the code. Both Python services are capped at 512 MiB with a 256-process limit; postgres gets 2 GiB, redis 512 MiB, browser-rpc 4 GiB and 4 CPUs, the downloader 512 MiB.

```bash
docker compose -p dtk -f docker/compose.yml logs api | grep -i -E 'killed|memory'
docker stats --no-stream
```

Those ceilings exist because without them a saturated disk queue or a runaway job slows every Postgres write, which slows the api, which fails `/readyz`, which restarts the api in a loop while the actual cause carries on.

---

## Migrations fail

### `the timescaledb extension is not available on this server`

**What it means.** `request_log`, `identity_events` and `content_snapshots` are hypertables, and the identity health score reads a continuous aggregate. Plain PostgreSQL cannot create them, and the first migration checks for the extension before it creates anything, so you get a legible message instead of a half-built schema.

**Fix.** Run PostgreSQL from the `timescale/timescaledb-ha:pg17` image, which is what `docker/compose.yml` does. If you pointed `DTK_DATABASE_URL` at your own PostgreSQL, either install TimescaleDB there or use the bundled container.

### `migrate` exits non-zero for another reason

```bash
docker compose -p dtk -f docker/compose.yml logs migrate
```

`migrate` is a one-shot container with no healthcheck: its exit status *is* its health, which is what `service_completed_successfully` reads. It runs on every `up`, and Alembic is idempotent, so re-running is safe.

Run it by hand when you need to time an upgrade, or after restoring a database taken at an older schema:

```bash
# what revision the code on disk expects (reads no database)
docker compose -p dtk -f docker/compose.yml run --rm --no-deps --entrypoint dtk api migrate --show

# apply everything
docker compose -p dtk -f docker/compose.yml run --rm migrate
```

A single-shot schema is deliberate: concurrent `CREATE TABLE` races between an api and a worker starting together is how a first boot corrupts itself.

### A `%` in the database password

Alembic keeps its options in a ConfigParser, which interpolates `%`. This is handled — the URL is escaped before it reaches Alembic — but if you are constructing `DTK_DATABASE_URL` by hand, percent-encode anything exotic in the password. Generating passwords as hex (as the install instructions do) avoids the whole class of problem, including `+` and `/` inside a URL.

---

## Cannot reach the console

### Connection refused on port 8000

**What it means.** `api` is the only container that publishes a port, and compose binds it to `127.0.0.1` by default. Serving the outside world is an explicit act, and putting TLS in front of it is yours to do.

**Confirm it.**

```bash
docker compose -p dtk -f docker/compose.yml ps api
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/readyz
```

**Fix.** Locally, use `http://127.0.0.1:8000`. To publish elsewhere:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

Inside the container the api always listens on `0.0.0.0`; what limits exposure is the published address, not the listen address.

### `/readyz` returns 503

Readiness requires PostgreSQL and Redis, each probed with a 3-second deadline. The body names which component failed and why. browser-rpc is absent from readiness entirely — an unreachable RPC service never takes the instance out of rotation. It is reported on `GET /api/v1/system/status` instead.

### The console loads but every API call fails

Check `/healthz` (liveness, touches nothing) against `/readyz` (readiness, touches the dependencies). Alive but not ready means the process is fine and a dependency is not — go to step 1 of the self-check.

### A path returns the console HTML instead of JSON

The console is a single-page app, so any path that is not a real file falls back to `index.html`. That fallback declines `/api`, `/healthz`, `/readyz`, `/swagger`, `/redoc`, `/openapi.json` and `/mcp`, so a typo under those prefixes gives you an honest 404. A typo elsewhere gives you the console shell with a 200 — if you get HTML where you expected JSON, re-read your path.

---

## Lost the setup token

**What it means.** A fresh deployment has no account, and whoever arrives first would become the administrator. The gate is a one-time token that exists **only in the container log**: generated at startup while the `users` table is empty, kept in Redis with a 24-hour TTL, compared in constant time, invalidated after 5 failed attempts, and permanently closed once an account exists. It never appears in an HTTP response, never reaches the database, never touches disk.

**Confirm and recover.** Restarting the api re-announces the token. An unexpired token is reused rather than replaced, so a restart does not invalidate a link you are already holding:

```bash
docker compose -p dtk -f docker/compose.yml restart api
docker compose -p dtk -f docker/compose.yml logs api | grep -A 6 'not initialized'
```

If the token has expired or was invalidated, that same restart mints a new one and prints it.

You can also read the stored token directly, which is faster than restarting when the api is busy:

```bash
docker compose -p dtk -f docker/compose.yml exec redis \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli GET setup:token'
```

### `SETUP_TOKEN_INVALID`

The token is wrong or expired. `error.details.attempts_remaining` counts down; at 5 failures the stored token is deleted and logged as `setup.token_invalidated`. Restart the api to issue a fresh one.

### `SETUP_ALREADY_DONE`

An account already exists, so the setup path is permanently closed — that check runs before the token is even looked at. Sign in normally, or reset a password with the command below.

---

## Forgot the admin password

**What it means.** A self-hosted tool has no password-reset email and no support desk. A shell on the box is the recovery path, and `dtk user passwd` exists so the honest alternative is not "drop the database".

```bash
# who exists
docker compose -p dtk -f docker/compose.yml exec api dtk user list

# reset one, prompting twice, never echoing
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd admin
```

For a provisioning script with no terminal, read one line from stdin instead:

```bash
printf '%s' "$NEW_PASSWORD" | \
  docker compose -p dtk -f docker/compose.yml exec -T api dtk user passwd admin --stdin
```

Passwords are at least 8 characters and hashed with argon2id. The CLI uses the OWASP-recommended parameters (19 MiB, two iterations, one lane); the console and the API use argon2-cffi's own defaults (64 MiB, three iterations, four lanes). The difference does not matter, because the parameters are encoded in each digest: a password set from the CLI verifies in the console and the other way round, and a digest written with older parameters is upgraded in place on the next successful login. Neither the password nor its digest is ever printed, echoed or logged. If no account exists at all, create one:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user create alice --role admin
```

---

## Locked out of the login page

**Symptom.** The password is right and the console answers `RATE_LIMITED`.

**What it means.** Five failed logins for one username lock that account for 15 minutes. The per-account limit is unconditional, because it only ever costs an attacker the account they are guessing at.

The **per-address** limit (20 failures) is enforced only when the source address actually identifies one caller — that is, when you have declared a trusted reverse proxy with `DTK_FORWARDED_ALLOW_IPS`. Without it, behind a TLS terminator or Docker's published-port userland proxy, every login on earth shares one peer address, and enforcing there would let a stranger lock the only administrator out repeatedly. In that topology the counter still runs, but only as a warning: look for `auth.login_spray_suspected` in the api log.

Separately, more than 30 failures across all accounts inside 60 seconds makes every further attempt wait 2 seconds before its password is verified. That is a delay, not a refusal.

**Fix.** Wait out the 15 minutes, or clear the counter:

```bash
docker compose -p dtk -f docker/compose.yml exec redis \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL login:fail:user:admin'
```

The username in that key is lowercased. Reading the password from the container's own environment keeps it out of your shell history and the host's process list.

---

## The identity pool is empty

**Symptom.** Every request answers `IDENTITY_POOL_EXHAUSTED` (HTTP 503) with a `retry_after`.

**What it means.** The scheduler could not lease an identity within `sched.max_wait_seconds` (10 by default). `error.details.reject_reason` says which flavour:

| `reject_reason` | Meaning |
|---|---|
| `no_token` | Identities exist but every one is out of quota on this endpoint |
| `all_inflight` | Identities exist but every one is mid-request. Concurrency per identity is 1 by design |
| `wait_timeout` | Neither cleared before the deadline |

**Confirm it.** Step 4 of the self-check counts identities by state, or:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk identity list
```

The console's **Identities** page (`/identities`) shows the same with health, and the pool card compares the `active` count against `pool.min_size`.

**Fix, by state.**

| What you see | What to do |
|---|---|
| Zero identities at all | Mint some (needs browser-rpc), or import a cookie jar from your own browser on `/identities` |
| Identities exist but all `cooling` | See [Identities go cooling immediately](#identities-go-cooling-immediately). They return on their own when the cooldown elapses |
| Identities `degraded` | They hit the cooldown ceiling. One success clears the failure streak, but they rejoin only when the ceiling cooldown elapses. Reset one by hand from `/identities` if you know the cause is gone |
| Identities `retired` | Retirement wipes the cookie jar and is not reversible. Mint or import replacements |
| Enough identities, still `no_token` | You are simply asking faster than the quota allows — see [Requests are slow](#requests-are-slow) |

**The knobs.** `pool.min_size` (3) is the low-water mark the filler tops up from; `pool.target_size` (8) is where it stops. The filler runs every 60 seconds. `pool.max_fail_streak` (3) is when an identity stops counting toward the pool level, so the filler replaces it rather than counting a corpse.

**One deliberate behaviour worth knowing:** when *every* live identity for a platform is failing, the filler holds the level rather than minting more. That is a platform-wide event, not a shortage, and adding five fresh visitors during an incident is the loudest signal this deployment could possibly send.

**Short links have their own version of this.** Expanding `v.douyin.com/…` needs an egress, and it goes through a proxy like everything else. With no proxies configured at all it expands directly from this host. With proxies configured but all unhealthy, expansion raises `IDENTITY_POOL_EXHAUSTED` with `details.reason = no_healthy_egress` — falling back to the host address is the one answer that would leak something new.

---

## Identities go cooling immediately

**Symptom.** A freshly minted or imported identity makes one or two requests and moves to `cooling`.

**What it means.** Cooling is what a `risk_control` outcome costs. The backoff is exponential: `sched.cooldown_base_seconds` (60) doubled per consecutive failure, multiplied by the endpoint's risk weight (1.0 to 1.8 depending on how sensitive the endpoint is), capped at `sched.cooldown_max_seconds` (21600, six hours). An identity that reaches the cap is marked `degraded` rather than `cooling`.

A business error — a deleted post, a private profile — changes nothing about the identity. That split is the single most important thing this system gets right, and getting it wrong is how one deleted video used to condemn a working cookie.

**Confirm it.** The **Logs** page (`/logs`), filtered to `outcome = risk_control`. On a risk or network row the `error_code` column carries the **classification rule** that fired, which tells you *why* the response was read that way:

| Rule | What was seen | What it usually means |
|---|---|---|
| `body.challenge_marker` | A captcha or verification interstitial in the first 4 KB of the body | Real risk control |
| `signature.refused` | Douyin answered 403 with `Blocked by ArgusSecurityPlugin …` | **Our signature**, not the identity — see [Signature errors](#signature-errors-and-the-signing-self-check) |
| `signature.rejected` | TikTok answered 200 with the `tt_orcas_res` header and a hollow body | Same: the signature did not verify |
| `http.risk_status` | 401, 403, 405, 412, 429 or 444 | The platform refused this caller |
| `body.empty` | HTTP 200 with no body at all | The plainest refusal there is |
| `payload.withheld` | 200, intact envelope, the payload key present and empty | The envelope survived, the content was withheld |
| `payload.bare_envelope` | 200 whose body is a status field and nothing else | A systematic refusal that used to look like a successful parse |
| `envelope.risk_code` | Body-level status `10000` | TikTok's verification envelope |

**Fix, by cause.**

- **`signature.refused` / `signature.rejected` dominate.** The identities are fine; the signer is not. Go to [Signature errors and the signing self-check](#signature-errors-and-the-signing-self-check). Cooling the pool here is the desired behaviour — it stops the pool hammering an endpoint with signatures it will never accept — but the fix is not in the identities.
- **Every identity behind one proxy cools at once.** The proxy prober cooled them: three network errors on one proxy inside five minutes marks it unhealthy and puts every identity behind it on a 900-second cooldown. Cookies are fine, the egress is not. See [Proxies fail their probe](#proxies-fail-their-probe). Identities are never moved to a different proxy — that recombination is exactly what the design forbids — so a permanently dead proxy eventually means retiring its identities.
- **No proxies at all, and a busy pool.** Every identity shares this machine's exit address. That is fine for a first look and a real correlation risk under load; the self-check says so at step 3.
- **The identity was imported with a mismatched fingerprint.** A cookie jar used with a different User-Agent is a weaker identity than no identity. Import the jar together with the User-Agent it was taken with.
- **A genuinely burnt identity.** Retire it and mint or import a replacement.

**Reset one by hand** from `/identities` (the identity's **Reset** action) when you know the cause is gone — a proxy you have just fixed, for example. A retired identity is deliberately not resettable: retirement wiped the session.

---

## A circuit breaker is open

**Symptom.** One endpoint answers `ENDPOINT_CIRCUIT_OPEN` (503) for every caller, with a `retry_after`. Everything else works.

**What it means.** One identity getting refused is routine. *Several distinct identities* failing on the same endpoint is a different event — usually a changed upstream API or a dead signer — and continuing only burns the pool. Three conditions must hold together in the rolling 300-second window:

| Condition | Setting | Default |
|---|---|---|
| Risk rate above the threshold | `sched.circuit_risk_threshold` | 0.6 |
| Enough observations to mean anything | `sched.circuit_min_samples` | 20 |
| Failures spanning several distinct identities | `sched.circuit_min_identities` | 3 |

The third is what separates "the endpoint broke" from "one identity broke"; without it a single flapping identity would trip the whole endpoint.

**Confirm it.** The **Overview** page (`/`) shows every declared endpoint — including the quiet ones, deliberately, because an endpoint missing from a board is exactly as invisible as one that was never called — with its circuit state, the observed success and risk rates, the sample count, how many distinct identities saw risk control, and how long until it is retried; an open circuit also raises a banner at the top of that page carrying the reason. Per-endpoint health is deliberately **not** on the **Scheduler** page (`/scheduler`), which holds the `sched.*` and `pool.*` settings instead. The same data is at `GET /api/v1/admin/endpoints/health`.

**How it closes.** By itself. The circuit stops being open 300 seconds after it tripped; the Redis key itself lingers a further 60 seconds. While it is open exactly one probe request is let through per 60 seconds, and the first probe that succeeds closes it immediately. Recovering at full allowance re-trips almost every time, which is why it is one probe and not a flood.

**Fix.** Find out why the risk rate rose — that is the real work, and the sections on [risk control](#upstream_risk_control) and [signatures](#signature-errors-and-the-signing-self-check) are where it lives. If you have fixed the cause and do not want to wait out the window, clear both Redis keys (the circuit itself, and the observation window that would otherwise re-trip it immediately):

```bash
docker compose -p dtk -f docker/compose.yml exec redis sh -c \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL \
     sched:circuit:douyin.author_posts sched:window:douyin.author_posts'
```

Substitute the endpoint name exactly as the Overview page spells it.

---

## Requests are slow

There are several different "slow", and they have different fixes.

### The response is `202` with a task id, not the data

That is not slow and not an error. Submitting returns `202` because the scheduler may have no identity free at this instant. To make a call synchronous, add `?wait=<seconds>`:

```bash
curl -H "X-API-Key: $KEY" \
  'http://127.0.0.1:8000/api/v1/douyin/video?url=https://www.douyin.com/video/123&wait=20'
```

Finished in time gives `200` with the result. Not finished gives `202` with the task id and `state: running` — which is not an error and loses nothing; poll `GET /api/v1/tasks/{id}`. Asking for more than `api.max_wait_seconds` (30) is a `400`, rejected rather than silently shortened.

### The first request per identity through browser-rpc takes seconds

Only when `signing.mode` is `rpc` or `auto` and the browser is chosen. A signature is taken in a page loaded with the identity's own cookies, so the first call for an identity launches a browser and loads the platform *through that identity's proxy* — measured at about 4 seconds without a proxy. Later calls for the same identity are answered by the resident page in milliseconds. `signing.rpc_timeout_seconds` (60) has to cover the cold case.

The default is `signing.mode = native`: the algorithms run in-process, no browser is in the request path, and this cost does not exist. Raise `DTK_BROWSER_WARM_CONTEXTS` to keep more identities resident if you deliberately run on `rpc`.

### Throughput is capped, and the cap is per identity per endpoint

Each `(identity, endpoint)` pair has a token bucket. A detail lookup gets capacity 5 and 0.30 tokens/second; a list endpoint gets capacity 3 and 0.12/second — roughly one request every 8 seconds, per identity, per endpoint, after the burst is spent. Concurrency per identity is fixed at 1 by design: a real session does not issue parallel API calls.

**Scale with identities, not with workers.** More worker containers only queue:

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=4
```

Quota is keyed on `(identity, endpoint)` rather than on the identity alone, because one identity spending its whole budget on the single most sensitive call reads as "this visitor only ever does one thing, and does it constantly" — a stronger signal than an even spread.

### Repeated identical calls are slower than you expect

They should be *faster*: an identical request that is already running is joined rather than duplicated, and shaped bodies are cached — `cache.content_ttl` 1800 s for one post, `cache.author_ttl` 900 s for a profile, `cache.list_ttl` 300 s for anything paged. `?refresh=true` turns both off for one call, costs an identity and an upstream request, and is for checking whether something changed — not for every call.

### Everything is slow, including the console

Check the api container is not being throttled by its CPU cap, and that Postgres is not the bottleneck. The **System** page (`/system`) shows each component's probe latency, the total database size and the row count per table.

---

## Rate limited

**Symptom.** `RATE_LIMITED` (429) on your own key, with `Retry-After`.

**What it means.** Fixed-window abuse protection, one window per minute. It is not metering and not billing: the only purpose is stopping one runaway script from draining the identity pool. The limit is the key's own `rate_limit` if it has one, otherwise `api.default_rate_limit_per_min` (120).

**Confirm it.** `error.details.limit` reports the ceiling that was applied. Which credential was metered follows from how you authenticated: an API key is metered per key, a console session per user.

**Fix.** Slow down, or raise the key's limit on the **API keys** page (`/api-keys`), or raise `api.default_rate_limit_per_min` in **Settings**. Setting a limit to 0 or below disables it for that subject.

**One caveat for opened endpoints.** Anonymous callers on an endpoint you opened via `api.public_endpoints` all share one user id, so they are metered by peer address instead. Behind Docker's userland proxy every caller looks like the bridge gateway, so that degrades to one shared bucket rather than to no limit at all — stricter, not looser, which is the right direction for a counter whose job is abuse.

Login failures are a different limiter entirely: see [Locked out of the login page](#locked-out-of-the-login-page).

---

## 401 or 403

The two are answering different questions, and telling them apart takes one look at the code.

| Code | HTTP | The question it answers | Typical cause |
|---|---|---|---|
| `UNAUTHENTICATED` | 401 | *Who are you?* — no credential, or one that was rejected | Missing header, typo'd key, revoked key, expired key, expired console session |
| `FORBIDDEN_SCOPE` | 403 | *You are known; may you do this?* | The key lacks the scope, or the account lacks the role |

**401 checklist.**

- The header is `X-API-Key`, or `Authorization: Bearer <key>`. The console's session cookie is a separate mechanism.
- A key that is revoked or past its `expires_at` resolves to nothing, which is a 401 — not a 403.
- **Sending a bad credential to an opened endpoint is still a 401.** A caller that sent a key we rejected is told so rather than silently downgraded to anonymous, because "your key works but sees less" is the harder failure to diagnose of the two.

**403 checklist.**

- Scopes are `douyin:read`, `tiktok:read`, `identity:manage`, `archive:read`, `archive:export`, `media:read`, `media:write`, `admin`. `error.details.required` names what the endpoint wanted.
- **An admin-owned key is still bounded by its scopes.** This is on purpose: almost every key on a self-hosted instance belongs to the admin user, so an admin short-circuit would make `archive:export` — the one call that hands back a copy of the database — unenforceable in exactly the deployment it was written for.
- A console *session* is bounded by the account's role instead, not by scopes.
- A few things need both a scope and an operator role: `?explain=true` and pinning `?identity=` need `identity:manage` **and** operator, because the answer contains a cookie jar. Those requests are written to the audit trail.
- `CONTENT_PRIVATE` is also a 403 but is about the content, not about you.

---

## INVALID_URL, and the eight reasons behind it

`error.details.reason` says which of these happened. They have different fixes.

| `reason` | What happened | What to do |
|---|---|---|
| `no_url` | There was no URL in what you sent | Send a link, or share text containing one |
| `host_not_allowed` | The host is not a Douyin or TikTok host | Check for a typo. Only `douyin.com`, `iesdouyin.com`, `amemv.com` and `tiktok.com` (and their subdomains) are fetched; `javascript:`, `data:` and `file:` are rejected before any host check |
| `platform_mismatch` | A TikTok link sent to `/api/v1/douyin/...`, or the reverse | Use `/api/v1/parse`, which routes by the link itself, or address the right platform |
| `unknown_resource` | The host is right, the path points at nothing this instance fetches | A music page, a hashtag, a search results page. Send a post or a profile link |
| `redirect_not_allowed` | The short link redirected off-platform | The chain left the allowlist. `security.url_allowlist` can admit one extra hop if you know the host and trust it |
| `too_many_redirects` | Still redirecting after 5 hops | Usually a link that is being bounced through an interstitial. Open it in a browser and use the address it lands on |
| `unresolved_short_link` | The short link expanded, and the result was still a short link | Rare. Open it in a browser and use the final URL |
| `short_link_dead_end` | The short link resolved, and it landed on a page this instance cannot fetch | See below |

### The short link that resolves to the platform's home page

This one deserves its own paragraph because the message is easy to misread as your mistake.

A `www.tiktok.com/t/<slug>` link, followed by a server-side client, answers a `302` to `https://www.tiktok.com/?_r=1` — the site's own front page. The link is fine, the paste is fine; TikTok simply declined to resolve it for a non-browser caller. `details.resolved_to` shows where it landed. The message says so directly: *the short link resolved to a page this instance cannot fetch; it has probably expired, or the platform will only resolve it in a browser*.

**Fix.** Open the short link in a real browser and copy the full URL it lands on (`https://www.tiktok.com/@handle/video/<id>`), and send that. There is nothing to configure.

### A note on Douyin's two-hop share links

`v.douyin.com/X` → `iesdouyin.com/share/video/123?…` → `douyin.com/video/123?previous_page=…` is the ordinary shape, and the last two normalize to the same canonical URL. That is treated as *arrived*, not as a loop — two URLs with the same canonical form are the same resource. If you have an older build that reported "redirect loop" for the most common share link there is, this is what was fixed.

---

## NOT_FOUND on a post that exists

**Symptom.** `NOT_FOUND` (404) for something you can open in a browser.

**What it means.** For content endpoints, `NOT_FOUND` is what a *business error* becomes: the platform answered, and what it answered was that the content is not there for this caller. It is not a claim that the id is malformed, and it is deliberately not `UPSTREAM_RISK_CONTROL` — the identity is not blamed for it and is not cooled.

**Confirm it.** Fetch the same link from the command line and look at the classification:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw \
  "https://www.douyin.com/video/7123456789012345678"
```

`dtk fetch` uses no login, no API key, no rate limit and no cache: the URL is resolved, one signed request goes out through a real identity, and you see the HTTP status, the outcome, the matched rule and — with `--raw` — the platform's own body. That last part is the whole point: it tells you what the platform actually said.

**The usual causes.**

| What you see in the raw body | Cause |
|---|---|
| `filter_detail` with `filter_reason: core_dep` and an empty message | Douyin's answer for an aweme id that does not exist. Check the id |
| `filter_detail` with `filter_reason: status_self_see` | The owner restricted the post to themselves. It is genuinely not fetchable by a guest |
| `statusCode: 10204`, no `itemInfo` | TikTok: the post was deleted, or never existed. TikTok does not distinguish the two, so neither does this |
| `status_code: 2053` | Douyin: aweme unavailable |
| `status_code: 2` / `error_code: 10201` / `100002` | An author id or handle nobody has |
| Region-locked content | The platform answers about the post differently depending on where the request came out. Try an identity on a proxy in a different country |

**When the content needs a login.** A guest identity cannot see what only an account can see. Import a cookie jar from your own signed-in browser on `/identities`, then pin the request to it with `?identity=<id>` (needs `identity:manage` and an operator role). See [Identities and proxies](./06-identities-and-proxies.md).

**When you suspect the answer is wrong.** A response the classifier reads as a business error but that really was a refusal would show as `NOT_FOUND` with a healthy identity. `dtk fetch --raw` is how you tell: a captcha page, an empty body, or a bare `{"status_code":0}` is a refusal, not an answer. Report it with the raw body attached.

---

## UPSTREAM_RISK_CONTROL

**Symptom.** `UPSTREAM_RISK_CONTROL` (502), with a `retry_after` equal to `sched.cooldown_base_seconds`. The identity that made the request is now cooling.

**What it actually means.** The response was *classified* as a refusal. Classification is a table of ordered rules, not a guess, and this is what fires it:

- An HTTP status the platform uses to refuse a caller: **401, 403, 405, 412, 429, 444** (444 is nginx closing the connection with no response, which is what Douyin's edge does to a request it does not like).
- A **challenge marker** in the first 4 KB of the body: `verify_center`, `verify_page`, `verifycenter`, `captcha`, `secsdk`, `byted_acrawler`, `slide_verify`, `tiktok-verify-page`. Scanned only when the body is *not* a clean success envelope — a video description containing the word "captcha" is not a captcha.
- A body-level status of **10000** — TikTok's verification envelope.
- **HTTP 200 with an empty body**, on an endpoint that has not declared its silence as meaningful.
- **A payload key present and empty** (`aweme_detail`, `aweme_info`, `itemInfo`, `userInfo`, `user_info`, `user`) with nothing explaining it. An empty *list* is excluded on purpose: an empty `aweme_list` is the normal end of pagination, and treating it as risk control would cool an identity every time somebody reached the last page of a profile.
- **A bare envelope**: HTTP 200 whose body is a status field, a message and tracing crumbs, and no payload at all. Douyin answers `author_posts` with a non-zero `max_cursor` exactly this way — 17 bytes — while the same request with `max_cursor=0` returns hundreds of kilobytes.
- **A signature the platform did not accept** — see the next section. Classified as risk control on purpose, because the request really was refused, but the rule name says the cause is the signer.

**What does *not* trigger it.** A deleted post, a private profile, an author id nobody has, an HTTP 400/404/410/451, an endpoint that has measured its own silence (Douyin's `author_likes` answers a private likes list with zero bytes), and an empty payload the platform *explained* with a `filter_detail`. All of those are business errors and cost the identity nothing.

**Fix.** Read the rule in the Logs page's `error_code` column first, then:

- Signature rules → [Signature errors](#signature-errors-and-the-signing-self-check). Nothing about the identities will fix it.
- Real challenge markers on one identity → let it cool; it returns on its own. Repeatedly, on one identity → retire it.
- Real challenge markers across the pool → you are asking too fast for the exits you have, or the exits are shared/flagged. Add proxies, add identities, or lower the endpoint pressure.
- All of one proxy's identities → [Proxies fail their probe](#proxies-fail-their-probe).

---

## Signature errors and the signing self-check

Every platform request is signed with a pure-Python reimplementation of the platform's own algorithm. When that drifts, the symptom is rarely a clean error.

### The three shapes this takes

| Symptom | What it is |
|---|---|
| `SIGNING_FAILED` (502) | No usable signer at all, or the signer raised. The clearest case, and the rarest |
| HTTP 200, empty or hollow body, header `tt_orcas_res` present | TikTok refusing a request whose signature did not verify. Classified `signature.rejected` |
| HTTP 403 with `Blocked by ArgusSecurityPlugin …` in the body | Douyin refusing a signature that is missing or does not cover the request. Classified `signature.refused`. The body carries one of `uifid not found`, `signature not found`, `sign invalid`, `sign expired` |

The second one is the expensive one: nothing about a 200 with an empty body says "signature", so it reads as a dead endpoint. It is called out by name in the classifier precisely so it stops looking like a platform block.

### Confirm it with step 5 of the self-check

Step 5 signs the same request twice — once with the native algorithm, once through browser-rpc — and compares.

| Step 5 result | Meaning | What to do |
|---|---|---|
| `signing_ok` | Native and browser signatures agree | Nothing |
| `signing_mismatch` | The native algorithm has drifted from the platform's, for the named platforms | Those platforms now sign through browser-rpc. Open an issue with the report attached so the algorithm can be updated |
| `signing_not_comparable` | The browser reference implements a *different version* of the algorithm, so a structural comparison says nothing either way | **Not a fault.** The native path is unaffected and still serves requests |
| `signing_no_browser_rpc` | browser-rpc is not configured, so there is no second signer | Set `DTK_BROWSER_RPC_URL` if you want this check. It is what detects a stale algorithm *before* the risk rate does |

`signing_not_comparable` is the one most likely to be misread: "cannot compare" reads as a failure and is not one.

### Reproduce a signature by hand

`POST /api/v1/tools/sign` computes the signature for a URL. It is pure arithmetic — nothing is fetched, no identity is spent — and the result depends only on what you send. `stages` breaks it down layer by layer, saying which layer contributed which parameter and whether it ran at all.

Three things must line up between signing and sending or the platform refuses the request whatever the signature says:

- **The User-Agent.** Both platforms hash it into the signature. The response echoes the one used; send that exact string.
- **The TLS fingerprint.** TikTok checks it agrees with the User-Agent, so a Chrome UA must travel over a Chrome TLS profile. Plain `curl` is refused whatever the signature says.
- **A cookie jar.** A correct signature with no cookies still gets nothing: the platform answers a *session*. `POST /api/v1/tools/identity` mints one (needs browser-rpc). On Douyin the jar is also what `x-secsdk-web-signature` is computed over — without a visitor id, `headers` comes back empty and the sign-protected endpoints refuse the request naming `uifid`.

`POST /api/v1/tools/decode` reads a signature parameter back, which is how you check what a signature you were sent actually encodes. Both are on the console's **Tools** page (`/tools`); see [Playground and tools](./07-playground-and-tools.md).

### Which signer is actually carrying traffic

`signing.mode` chooses: `native` (default, in-process, no browser in the request path), `rpc` (drive a real browser), `auto` (prefer rpc for endpoints the platform's own SDK signs, otherwise native). `signing.fallback_enabled` (default on) lets the other signer be tried when the preferred one fails — **turn it off to find out which signer is really carrying your traffic**, because a silent crossing is how a broken signer goes on looking healthy: its traffic moves to the other one and the success rate never dips.

The `signer` column on each `request_log` row records `native` or `browser` per request.

---

## The browser container is unavailable

**Symptom.** Step 1 of the self-check reports a `warn`: *postgres and redis are reachable; browser-rpc is not*. Or minting fails with `NOT_CONFIGURED`.

**This is a supported deployment, not a degraded one.** browser-rpc is behind a compose profile and is the heaviest container in the stack — measured warm at 2.57 GiB resident and six cores' worth of Chromium. An instance that imports its cookies by hand does not need it.

### What still works without it

| Works | Does not work |
|---|---|
| Every read endpoint, signed natively | Automatic identity minting |
| Manually imported cookie jars | `POST /api/v1/tools/identity` (answers `NOT_CONFIGURED`) |
| The whole console, the API, MCP, the CLI | Step 5 of the self-check (skipped: nothing to compare against) |
| Readiness — an unreachable RPC never takes the instance out of rotation | The browser signing fallback, when `signing.mode` is `rpc` or `auto` |

### Confirm which of the two it is

`NOT_CONFIGURED` means `DTK_BROWSER_RPC_URL` is unset — this install never had a browser. A component check that reports a timeout or a connection error means the service is configured and down.

```bash
docker compose -p dtk -f docker/compose.yml --profile browser ps
docker compose -p dtk -f docker/compose.yml logs browser-rpc | tail -40
```

### Start it

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

### It reports healthy but mints nothing

The service answers `/rpc/health` even when the browser backend failed to start — on purpose, so you get a reason instead of a restart loop. The compose healthcheck therefore reads the `status` field in the body rather than settling for a 200. An image built without `CLOAKBROWSER_COMMIT` is the usual cause.

### Chromium crashes with "Target crashed", and it reads like a platform block

The driver passes `--disable-dev-shm-usage`, so every renderer's shared memory lands in the container's `/tmp` tmpfs, not `/dev/shm`. Each resident context holds roughly 290 MB of deleted-but-open files — invisible to `du`, visible to `df`. The tmpfs is sized 3 GB for that reason. The rough ceiling is `300M × DTK_BROWSER_WARM_CONTEXTS × 2 platforms`, plus a mint in flight. If you raised `DTK_BROWSER_WARM_CONTEXTS`, raise the tmpfs with it — and remember it is RAM, not free disk.

### Chromium and emulation version drift

The System page reports the Chromium major browser-rpc runs next to the `wreq` emulation profile major, side by side. They are shown together so drift stays visible instead of being discovered from a rising risk-control rate weeks later.

---

## Downloads fail or files are missing

Media downloads are an opt-in sidecar. An instance that only wants metadata never needs to store a byte of video.

### `NOT_CONFIGURED` (501) on `POST /api/v1/downloads`

Either `DTK_DOWNLOADER_URL` is unset, or `media.enabled` is off in the settings. The message says which.

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

### `DOWNLOADER_UNAVAILABLE` (503)

The sidecar is configured and did not answer. Nothing was changed by the failed call.

```bash
docker compose -p dtk -f docker/compose.yml --profile downloader ps
docker compose -p dtk -f docker/compose.yml logs downloader | tail -40
```

The container is a scratch image with one static Go binary — no shell, no package manager — so its healthcheck is the binary probing itself.

### `QUEUE_FULL` on a download, with a 300-second `Retry-After`

New downloads are paused because the disk crossed `capacity.hard_stop_percent`. See [The disk is full](#the-disk-is-full).

### A download finished `partial`

`partial` is its own state because a post with several files can land some and lose others — that is neither `done` nor `failed`. Look at the download's row on `/downloads` for which file is missing and why. Common causes: a single file exceeded `media.max_file_bytes` (512 MiB by default, counted on bytes actually written rather than on what the server claims), or a mirror expired mid-transfer.

### The files were there and now they are not

Two mechanisms delete media, and both announce themselves:

- **The size ceiling.** `media.max_bytes` (2 GiB by default). Past it the oldest *unpinned* downloads are removed until the volume is back under, and a `MEDIA_EVICTED` alert says exactly what went — that alert is the only notice you get. Pin a download in the console to exempt it permanently. `media.max_bytes = 0` disables eviction entirely.
- **Nothing else.** Retention does not delete media, and the capacity guard does not delete anything at all.

### A download is stuck `queued` or `running` forever

The maintenance sweep runs every 300 seconds and settles downloads older than 7200 seconds that are still in a live state, marking them `failed` with *the worker never reported an outcome for this download*. Files that did land stay where they are. If it never settles, the worker is not running.

### The links expired before the transfer started

Signed CDN links expire within hours. `media.mirror_max_age_seconds` (600) is how old an archived media link may be before a download re-parses the post to get fresh ones; `0` always re-parses.

### The API will not serve me the file

By design for the downloader: it is a sink, never a relay. The API mounts the media volume read-only and serves stored files to the console behind the `media:read` scope; the downloader itself never relays media to callers. See [Downloads, library and watchlist](./08-downloads-and-library.md).

---

## The disk is full

**Symptom.** Background collection stopped on its own. New downloads answer `QUEUE_FULL`. A `CAPACITY_WARNING` or `CAPACITY_PAUSED` alert fired.

**What it means.** The capacity guard measures free space with `shutil.disk_usage` on the database and media paths — the only number that accounts for WAL, indexes, chunk overhead, other containers and whatever else shares the volume, none of which a sum over table sizes can see. It reads the *fullest* volume, not the average: the disk that fills first is the one that breaks things, and averaging it against an empty second volume is how a guard reports healthy while Postgres is failing to write.

| State | Threshold | Behaviour |
|---|---|---|
| `ok` | below `capacity.warn_percent` (80) | Nothing |
| `warn` | at or above 80% | `CAPACITY_WARNING` alert. Nothing changes |
| `full` | at or above `capacity.hard_stop_percent` (92) | Background collection and **new** download jobs stand down. `CAPACITY_PAUSED` alert |

**Interactive reads are never paused, and nothing is ever deleted.** Turning a full disk into "my API is down" would be a worse outage than the one being prevented, and deleting a user's archive to free space would be worse than either — the archive exists precisely to outlive the platform.

**Confirm it.** The **System** page shows the total database size and the row count per table. `GET /api/v1/system/status` returns the same figures — `storage.db_size_bytes`, `storage.rows` per table, and `storage.identities`. Bytes on disk per table are not exposed today.

**Fix, in order of how much you get back.**

1. **Stored media.** Usually the biggest thing. Lower `media.max_bytes` and let eviction reclaim, or delete downloads you do not need from `/downloads`.
2. **The raw archive.** `archive.store_raw` (off by default) keeps each platform's untouched payload on every object. It is the largest single thing this instance can *choose* to store. If it is on and you do not need it, turn it off.
3. **Request log retention.** `retention.request_log_days` (14). Lowering it reclaims chunks on the next maintenance pass.
4. **Archived posts.** Nothing prunes the archive on its own, and `retention.content_days` will not change that: the key is declared, but **no code reads it**, so setting it has no effect (see the [configuration reference](./03-configuration.md)). Delete the posts you do not want from the [Library](./08-downloads-and-library.md) instead.
5. **More disk.** The honest answer when the archive is the thing you wanted.

---

## Proxies fail their probe

**Symptom.** A proxy is marked unhealthy on `/proxies`, or step 3 of the self-check reports `proxies_partial` / `proxies_all_failed`.

**What it means.** The prober sweeps every 300 seconds (and never re-probes the same proxy more often than every 60 seconds). Three network errors on one proxy inside five minutes marks it unhealthy and puts every identity behind it on a 900-second cooldown. Identities are never re-bound to a different proxy.

**Confirm one proxy directly.**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk proxy test '<proxy-id>'
```

It reports the exit IP, country, timezone, latency and a detail on failure, and writes the health and geo result back onto the row (pass `--no-write` to probe without recording). The id may be the shortened prefix the tables print. The console has the same button per row on `/proxies`.

**Read the detail.**

| Detail | Cause |
|---|---|
| `ConnectTimeout`, `ConnectionError`, `ProxyConnectionError` | The exit is down, or the credentials are wrong, or your address is not allowed by the provider |
| `ImportError` | A SOCKS proxy without the optional httpx extra installed |
| `ValueError` | The proxy URL is not usable as written |
| `probe returned no JSON` / `no JSON object` | The probe URL is not the service you thought it was, or a captive portal answered |
| HTTP 407 in the request logs | The proxy itself is refusing: misconfigured exit, or a lapsed subscription. Counted as a network error so proxy health is probed, never as "the content is missing" |

**Fix.** Correct the credentials, or replace the proxy. Identities bound to a permanently dead proxy cannot recover — retire them and mint or import replacements bound to a working exit.

**A changed country is worth seeing even when the proxy still works.** The exit address decides the locale a minted identity should claim, and a German exit reporting `Asia/Shanghai` is a tell given away for free.

**Running with no proxies at all** is allowed and the self-check reports it as a warning, not a failure: every identity shares this machine's egress IP. Fine for a first look, a correlation risk for a busy pool.

---

## MCP client cannot connect

The MCP server is mounted on the API process at `/mcp` (streamable-http), and can also be run over stdio.

| Symptom | Cause | Fix |
|---|---|---|
| 404 or a redirect surprise on `GET /mcp` | A Mount matches only the paths *beneath* it | The endpoint is `/mcp/`. The bare path answers a `307` to it, preserving method and body, so most clients follow it — but configure `/mcp/` |
| `401 UNAUTHENTICATED` | No API key, or one that was rejected | Send `Authorization: Bearer <key>` or `X-API-Key: <key>`. **The console session cookie is never accepted here** — a cookie exists for a browser, and accepting it would make the MCP endpoint reachable by anything that can make a request from inside that browser |
| `401` with *this endpoint accepts API keys only* | You authenticated as a console session | Mint an API key on `/api-keys` |
| `403 FORBIDDEN_SCOPE` at connect time | The key has no platform read scope at all | Give it `douyin:read` or `tiktok:read` |
| `403 FORBIDDEN_SCOPE` on one tool call | The key reads one platform and the call named the other | The transport can only check that the key reads *something*; the platform arrives inside the JSON-RPC body, so it is re-checked per call. Same rule as REST, deliberately |
| `429 RATE_LIMITED` | The same abuse limiter as REST applies | See [Rate limited](#rate-limited) |
| The session drops when you scale the api | Sessions are stateless by default so the API can scale horizontally | Nothing to fix; a pinned session would break the moment a second worker started |

MCP failures use the same `{"success": false, "error": {"code": …}}` envelope as REST, and a 401 carries `WWW-Authenticate: Bearer realm="dtk"`.

**Over stdio**, for a local agent, run the server directly:

```bash
python -m dtk.mcp
```

It owns its own database, Redis and settings lifecycle, so it needs the same `DTK_*` environment the containers get. Note that on stdio, stdout *is* the JSON-RPC channel — logging is moved to stderr before anything logs, and a single stray line on stdout corrupts the stream.

**The tool set is eight tools and stays eight.** Each additional tool measurably lowers an agent's selection accuracy. There is no identity, cookie or proxy tool, structurally — see [MCP and AI agents](./12-mcp.md).

---

## Tasks stay queued

**Symptom.** `GET /api/v1/tasks/{id}` reports `queued` and never moves. The Diagnose page never finishes.

**What it means.** Nothing is consuming the queue. Console-triggered jobs — diagnose, mint, identity test, proxy test, backup, restore, notification test — all run in the worker.

```bash
docker compose -p dtk -f docker/compose.yml ps worker
docker compose -p dtk -f docker/compose.yml logs worker | tail -40
```

The worker has no HTTP port, so its healthcheck asserts the interpreter runs and that Redis — where leases, budgets and the queue live — is reachable. A worker that cannot reach Redis does no work at all.

**Stale tasks recover on their own.** A task still `running` 900 seconds after it started is presumed orphaned and re-queued by the maintenance sweep, up to 200 per pass, so a pathological backlog is not pushed back all at once.

### `QUEUE_FULL` (503)

The queue is at `sched.queue_max` (500). The error names the current depth and the ceiling, and `Retry-After` is `sched.max_wait_seconds`. Shedding load beats accepting work nobody will get to: a caller left hanging is worse off than one told to come back.

Operator-triggered maintenance jobs are exempt from the ceiling — you can always run a diagnosis on a saturated instance. Media downloads are deliberately *not* exempt.

**Fix.** Scale workers, or reduce submissions, or raise `sched.queue_max` if the queue really is draining and just needs more headroom. Throughput is bounded by the identity pool, not by the worker count.

### The result expired

`TASK_NOT_FOUND` on a task that definitely succeeded means the result was evicted: `retention.task_result_hours` is 24. The task row itself survives `retention.task_days` (90). Submit the work again — no retry brings an expired result back.

---

## UPSTREAM_CHANGED

**Symptom.** `UPSTREAM_CHANGED` (502), with `details.path` naming a field, and a message asking you to report it.

**What it means.** The platform answered, the response was classified as usable, and the parser found the response no longer has the shape it expects. It is non-retryable on purpose: no amount of retrying changes the platform's response format, and the fix is a code change.

**Confirm it and make the report useful.**

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk fetch --raw "<url>"
```

The command prints the parse error naming the missing path, and the platform's own payload beside it. That pair is exactly what tells a maintainer whether the platform renamed a field or stopped returning it at all — which is why `--raw` exists.

**One case that looks like this and is not.** A systematic refusal that reaches the parser as an empty-but-well-formed body used to surface as `UPSTREAM_CHANGED`, sending operators to report a parser bug that no parser change could fix. The `payload.bare_envelope` classification rule now catches that shape and reports it as risk control instead. If you are seeing `UPSTREAM_CHANGED` alongside `http_status: 200` and a tiny response, check the raw body before assuming a parser bug.

---

## What to include when you report a problem

1. **The diagnostics report.** `dtk diagnose`, or the Copy button on `/diagnose`. It is redacted at the source, which is what makes "paste this into the issue" safe advice.
2. **The `error.code` and the `meta.request_id`** from the failing response.
3. **The `dtk fetch --raw` output** for the exact link, if the problem is one link or one endpoint.
4. **The version and commit.** Shown on `/system` and on the **About** page (`/about`), and printed by `dtk --version`.
5. **Which compose profiles you run** — plain, `--profile browser`, `--profile downloader`.

Do not paste `.env`, a cookie jar, a proxy URL with credentials, or an API key. The report and the CLI already mask those; anything you copy from elsewhere is not masked.

---

## See also

- [Operations](./10-operations.md) — the console pages, alerts, backups and retention this page keeps pointing at
- [CLI reference](./13-cli.md) — every flag of the commands this page runs: `dtk diagnose`, `dtk user passwd`, `dtk migrate`, `dtk fetch`, `dtk identity`, `dtk proxy`
- [Configuration reference](./03-configuration.md) — every setting named here, with its default and scope
- [Concepts](./04-concepts.md) — identities, outcomes, the scheduler and the circuit breaker, explained rather than debugged
- [Identities and proxies](./06-identities-and-proxies.md) — minting, importing, pinning, retiring
- [REST API guide](./11-api.md) — the envelope, `?wait=`, `?refresh=`, `?explain=`, scopes
- [Security](./15-security.md) — the master key, the allowlists, what leaves this instance
- [FAQ and glossary](./17-faq.md) — the vocabulary used throughout
