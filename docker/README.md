# Container images and orchestration

Everything needed to run dtk on one host. The design this implements is
`docs/design/09-deployment.md`, with the container rules from
`docs/design/08-security.md` and the browser-rpc contract from
`docs/design/04-transport-signing.md`.

```
docker/
├── Dockerfile           api + worker + migrate (one image, three entrypoints)
├── Dockerfile.browser   browser-rpc (optional, --profile browser)
├── Dockerfile.downloader  media downloader (optional, --profile downloader)
├── entrypoint.sh        role dispatch and the DTK_SECRET_KEY gate
├── compose.yml          the stack
├── compose.test.yml     test fixtures only (owned elsewhere; never used here)
├── browser_rpc/         the RPC service source, built into Dockerfile.browser
└── downloader/          the Go downloader source, built into Dockerfile.downloader
```

## Quick start

Write `.env` at the repository root first - nothing in the stack ships a default
password or key. Hex rather than base64 for the two passwords because they end
up inside URLs, where `+` and `/` would need escaping:

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

```bash
docker compose -p dtk -f docker/compose.yml up -d
docker compose -p dtk -f docker/compose.yml logs api    # prints the setup token
```

`.env` is git-ignored and excluded by `.dockerignore`, so it reaches the
containers as an env file and never as image content.

Then open <http://127.0.0.1:8000/setup?token=...> from the log line and create
the first administrator. No configuration file needs editing at any point.

With the headless browser (automatic identity minting and the signing fallback):

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=<sha> \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

Leaving the profile off is a supported deployment, not a degraded one: a user who
imports cookies by hand does not need the heaviest container in the stack, and
the pool falls back to manual import on its own when browser-rpc is unreachable.

With the media downloader (storing video and images on this host's own disk):

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

Also optional, and also not degraded without it: `POST /api/v1/downloads` then
answers 501 with a message saying how to turn it on, and nothing else changes.
The files land on the `media-data` volume, laid out as
`<platform>/<author>/<content id>/` with a `meta.json` beside them. They are
never served back through the API - this container fills the operator's disk,
it does not relay media to callers (`docs/design/18-storage-media-and-collection.md`).

Stored media is capped by the `media.max_bytes` setting, 2 GiB by default.
Past it the oldest unpinned downloads are removed and an alert says what went;
pinning one in the console exempts it for good.

## What the images are

**`Dockerfile`** builds in three stages:

| Stage | Base | Produces |
|---|---|---|
| `console` | `node:22-alpine` | `npm ci && npm run build` in `web/`, output `dist/` |
| `deps` | `python:3.12-slim` + pinned `uv` | `/app/.venv` from `uv.lock`, `--frozen --no-dev` |
| `runtime` | `python:3.12-slim` | the venv, the installed package, the built console |

Node never reaches the runtime image, and neither does a compiler or a package
manager. `uv sync --frozen` is what makes the build reproducible; the V4
`requirements.txt` froze at 2024 precisely because nothing pinned it.

api and worker share this image and differ only in the first argument to the
entrypoint. Two images built from one source tree drift apart the first time
somebody rebuilds one of them.

**`Dockerfile.browser`** builds browser-rpc. It is separate because it is the
only container that carries a browser, and it is optional.

### Runtime posture

| Rule | Where |
|---|---|
| Non-root (`uid 10001`), application files owned by root | `Dockerfile`, `Dockerfile.browser` |
| Read-only root filesystem, writable tmpfs on `/tmp` | `compose.yml` |
| All capabilities dropped (browser-rpc keeps only `SYS_ADMIN`) | `compose.yml` |
| `no-new-privileges` | `compose.yml` |
| No default password or key anywhere in either image | `entrypoint.sh` refuses to start without one |
| Only `api` publishes a port, bound to `127.0.0.1` | `compose.yml` |
| postgres and redis on an `internal: true` network with no route off the host | `compose.yml` |

`HOME` and `XDG_CACHE_HOME` point at `/tmp` because the root filesystem is
read-only; that tmpfs is the only writable path in the container. The browser
binaries are the exception and live at `PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`:
installing them under `/tmp` would put them exactly where compose mounts a 256MB
tmpfs at runtime, which would both hide them and be too small to hold them.

## The entrypoint

```
dtk-entrypoint api      [uvicorn options...]
dtk-entrypoint worker   [worker options...]
dtk-entrypoint migrate  [revision]            # default: head
```

Every role first checks `DTK_SECRET_KEY` and refuses to start when it is missing
or shorter than 32 characters, exiting `78` (`EX_CONFIG`) with the command that
generates one. That key encrypts every stored cookie and proxy credential, so
the image ships no default: a key that is the same on every install is not
encryption.

`migrate` is gated too. A schema created for a deployment that cannot decrypt
its own credentials is worse than a failed migration, and this way the failure
arrives before any table exists.

The worker is started as `python -m dtk.worker`, falling back to
`python -m dtk.ops.worker`; `DTK_WORKER_COMMAND` overrides the command line
entirely for a deployment that starts it some other way.

## Behind a reverse proxy

Everything that reaches the api through a TLS terminator arrives with the
terminator's address, and everything that reaches it through Docker's
published-port userland proxy arrives with the bridge gateway's. Either way
`request.client.host` is a single address shared by the whole internet.

`DTK_FORWARDED_ALLOW_IPS` is how you say otherwise. Set it to the address, or
comma-separated addresses, your proxy connects from; the entrypoint then starts
uvicorn with `--proxy-headers --forwarded-allow-ips`, and uvicorn rewrites the
peer address from `X-Forwarded-For` for those hops and for nobody else.

```bash
# in the repository-root .env, alongside DTK_SECRET_KEY
DTK_FORWARDED_ALLOW_IPS=172.18.0.5    # the nginx/caddy container or host
```

| Value | What the api does with the source address |
|---|---|
| unset (default) | Peer address only. It is recorded, but no login is refused because of it: the per-address failure counter degrades to a `auth.login_spray_suspected` warning. |
| your proxy's address | Real client addresses in the audit trail, the session list and the logs, and the per-address login limit refuses again. |
| `*` | uvicorn believes `X-Forwarded-For` from whoever sends it, so the address is forgeable. The api treats this as undeclared and will not refuse a login on it. |

The per-address limit is conditional because with one shared address it is one
global bucket: twenty failed logins from a stranger would otherwise lock every
account out of the console for fifteen minutes, repeatably, which is a denial of
service handed to anyone who can reach the login page. The per-account limit is
not conditional — five failures cost the attacker only the account they are
guessing at.

That per-account limit is still a lockout, so an operator whose own account is
being guessed at can meet `RATE_LIMITED` on a correct password. The counter is a
Redis key with a fifteen minute TTL, and waiting is not the only option:

```bash
docker compose -p dtk -f docker/compose.yml exec redis \
    sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli DEL login:fail:user:admin'
```

The username in that key is lowercased. Reading the password from the
container's own environment keeps it out of the host's shell history and process
list, which is why the healthcheck is written the same way.

## Configuration and the two ways compose reads `.env`

This trips people up, so it is worth being explicit.

* **Service variables** come from `env_file: ../.env`, whose path compose
  resolves relative to the compose file. The repository-root `.env` is therefore
  picked up even though the compose project directory is `docker/`. Missing file
  is not an error (`required: false`); the entrypoint produces the useful
  message instead.
* **Interpolation** in `compose.yml` — only the published address,
  `${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000` — does *not* read
  that file. It reads `docker/.env` or the shell. It therefore defaults to
  loopback, and publishing elsewhere is an explicit act:

  ```bash
  DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
  # or
  COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
  ```

  Serving the outside world also means putting TLS in front of it; the container
  does not do that for you.

Inside the container the api listens on `0.0.0.0`. What limits exposure is the
published address, not the listen address.

The postgres, redis and browser-rpc containers get the same env file, with
`DTK_SECRET_KEY` blanked out in their `environment:` block — none of them has any
use for the key that encrypts the application's credentials.

browser-rpc reads that env file too, which is what makes every `DTK_BROWSER_*`
knob in the table below settable in one place. Only the three values fixed by the
container itself — `BIND_HOST`, `BIND_PORT` and `PROFILE_ROOT`, which have to
agree with `expose`, the healthcheck and the tmpfs — are pinned in
`environment:`, because `environment:` wins over `env_file:`.

Everything else is a runtime setting: seeded from `.env` at first init, then
database-authoritative and editable from the console
(`docs/design/10-configuration.md`).

## Scaling, upgrading, health

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=4
git pull && docker compose -p dtk -f docker/compose.yml build && \
  docker compose -p dtk -f docker/compose.yml up -d
```

`migrate` runs on every start and Alembic is idempotent. Named volumes
(`postgres-data`, `redis-data`) survive rebuilds. Throughput is bounded by the
identity pool, not by the worker count: workers beyond the number of identities
only queue.

Healthchecks: `pg_isready` for postgres, an authenticated `redis-cli ping` for
redis, `GET /readyz` for api (readiness, so `up --wait` returns when the API can
actually serve), a Redis TCP probe for the worker, which has no port of its own,
and `GET /rpc/health` for browser-rpc — that one reads the `status` field rather
than settling for a 200, because the service answers the probe even when the
browser backend failed to start. `migrate` has none - a one-shot task's exit
status is its health, which is what `service_completed_successfully` reads.

## browser-rpc

A small FastAPI service wrapping a headless browser. Two jobs, both off the hot
path: mint guest identities through an identity's own proxy, and sign requests
with the platform's own JavaScript when the native algorithm has drifted.

```
POST /rpc/mint    {platform, proxy_url, geo_hint}
                → {cookies, browser_family, browser_major, user_agent,
                   platform_hint, screen, language, timezone, exit_ip}
POST /rpc/sign    {platform, url, query, params, user_agent}
                → {a_bogus | x_bogus | signature, ms_token}
GET  /rpc/health  → {status, backend, backend_version, chromium_major,
                     warm_contexts, uptime}
```

The clients are `dtk.identity.minting.client.BrowserRpcClient` and
`dtk.signing.rpc.RpcSigner`; `docker/browser_rpc/tests/test_rpc_api.py` drives
both of them against this service, so a field rename breaks a test rather than a
deployment.

No authentication: the service is reachable only on the internal compose
network, and a second credential between two containers that already share a
private network adds rotation without adding safety. The `url` and `proxy_url`
arguments are still validated - against this project's own mistakes.

### Context lifecycles

* **Mint contexts are single use.** A fresh profile directory per session, on a
  tmpfs, removed in a `finally` block. A reused profile hands the next identity
  the previous one's traces.
* **Signing contexts stay warm**, one or two per platform, rebuilt every 30
  minutes because the platform ships new JavaScript regularly. A cold browser
  start costs seconds and the fallback path cannot absorb that. Each one still
  gets its own profile directory, removed when it closes: Chromium locks a
  persistent profile, so two warm pages sharing a directory would mean the
  second one never launches.

### Geo alignment

Timezone, locale and `Accept-Language` come from where the traffic leaves, not
from the host. The exit country is measured by querying an echo endpoint
*through the proxy* before the context is created, because those values have to
be set at creation time. A German exit reporting `Asia/Shanghai` is a tell given
away for free. The probe never fails a mint: an unreachable echo endpoint just
means the caller's `geo_hint` and the default decide.

### Settings

All read once at start, all prefixed `DTK_BROWSER_`, all settable in the
repository-root `.env` except the three the container fixes for itself.

| Variable | Default | Meaning |
|---|---|---|
| `BACKEND` | `cloak` | `cloak` or `fake`. No fallback between them |
| `BIND_HOST` / `BIND_PORT` | `0.0.0.0` / `9000` | Fixed by compose; internal network only, no port is published |
| `PROFILE_ROOT` | `/profiles` | Fixed by compose; parent of the single-use mint profiles, a tmpfs |
| `WARM_CONTEXTS` | `1` | Warm signing contexts per platform |
| `WARM_REFRESH_SECONDS` | `1800` | Age at which a warm page is rebuilt |
| `PREWARM` | `true` | Build warm pages at startup instead of on first use |
| `MAX_CONCURRENT_MINTS` | `2` | Browsers minting at once (~500MB each) |
| `MINT_TIMEOUT_SECONDS` | `75` | Below the client's 90s |
| `SIGN_TIMEOUT_SECONDS` | `8` | Below `RpcSigner`'s 10s |
| `CONTEXT_OPEN_TIMEOUT_SECONDS` | `45` | Budget for opening a context |
| `SIGN_PROXY_URL` | unset | Optional exit for the warm signing pages |
| `GEO_PROBE_URL` | `https://ipinfo.io/json` | Echo endpoint, queried through the proxy; empty disables it |
| `DEFAULT_COUNTRY` | `US` | Used when neither the caller nor the probe knows |
| `HEADLESS` | `true` | Off only for debugging with a display |
| `BACKEND_PIN` | set by the image | Reported by `/rpc/health` |

`SIGN_PROXY_URL` is worth a note: a warm page harvests no cookies, but it does
load the platform's site, and without an exit it does so from the host's own
address. Minting is unaffected - it always uses the identity's own proxy.

### The backend is pluggable, and pinned

`docker/browser_rpc/backends/cloak.py` is the only module that knows a browser
library exists, and inside it the CloakBrowser-specific surface is four marked
places: the import, the context launch, the fingerprint read, and the
`SIGN_SNIPPETS` table. Everything else - pooling, budgets, validation, geo,
HTTP - is backend-neutral and tested without a browser.

Replacing the backend means writing a module with `start`, `close`, `info`,
`mint` and `open_signing_context` (see `backends/base.py`) and adding it to the
registry in `backends/__init__.py`.

At least three GitHub organizations publish repositories called `cloakbrowser`
with identical descriptions (`docs/design/04-transport-signing.md`), so the image
takes a repository *and a commit*:

```bash
CLOAKBROWSER_REPO=https://github.com/CloakHQ/cloakbrowser \
CLOAKBROWSER_COMMIT=<40-char sha> \
  docker compose -p dtk -f docker/compose.yml --profile browser build browser-rpc
```

The pair is baked into `DTK_BROWSER_BACKEND_PIN` and reported by `/rpc/health`,
so "which browser is this" is answerable from the running system. To pin an
upstream image instead of building from source, pass a digest:

```bash
docker build -f docker/Dockerfile.browser \
  --build-arg BROWSER_BASE_IMAGE=ghcr.io/<org>/cloakbrowser@sha256:<digest> \
  --build-arg CLOAKBROWSER_COMMIT=<sha> -t dtk-browser-rpc:dev .
```

Built with no commit, the image contains the service and the fake backend but no
browser, and refuses to mint unless `DTK_BROWSER_BACKEND=fake` is set on purpose.
Nothing falls back to the fake backend on its own: a pool quietly filling with
synthetic identities looks healthy right up to the moment every request comes
back risk-controlled.

## Verifying

### What is verified offline, in CI

```bash
docker compose -f docker/compose.yml config -q          # compose parses
docker compose -f docker/compose.yml --profile browser config -q
sh -n docker/entrypoint.sh                              # shell syntax
uv run ruff format --check docker/browser_rpc
uv run ruff check docker/browser_rpc
uv run pytest docker/browser_rpc/tests -q               # 100 tests, no browser
```

The browser-rpc suite is not under `tests/`, so the repository's default
`pytest` run does not collect it; name the path explicitly, as above.

Run the service by hand against the fake backend - this is the full RPC surface,
no container required:

```bash
PYTHONPATH=docker DTK_BROWSER_BACKEND=fake DTK_BROWSER_BIND_PORT=19000 \
  DTK_BROWSER_PROFILE_ROOT=/tmp/dtk-browser DTK_BROWSER_GEO_PROBE_URL= \
  uv run python -m browser_rpc

curl -s localhost:19000/rpc/health
curl -s -X POST localhost:19000/rpc/mint -H 'content-type: application/json' \
  -d '{"platform":"douyin","proxy_url":"http://user:pass@gate.example:8080","geo_hint":{"country":"DE"}}'
curl -s -X POST localhost:19000/rpc/sign -H 'content-type: application/json' \
  -d '{"platform":"douyin","url":"https://www.douyin.com/aweme/v1/web/aweme/detail/","query":"aweme_id=7","params":{"aweme_id":"7"}}'
```

The mint reply should carry a `Europe/Berlin` timezone for a `DE` hint, and the
log line for the mint must show the proxy with its credentials masked.

### What can only be verified against a real browser

Three things cannot be checked without CloakBrowser installed, and all three are
one edit each - which is why they are isolated in `backends/cloak.py`. The
procedure follows `docs/design/16-salvage-and-debug.md`.

1. **The driver entry point.** `_load_driver()` expects an `async_playwright`
   attribute on the pinned module. Confirm with:

   ```bash
   docker compose -p dtk -f docker/compose.yml --profile browser \
     run --rm --entrypoint python browser-rpc \
     -c "import cloakbrowser; print(cloakbrowser.__version__, hasattr(cloakbrowser, 'async_playwright'))"
   ```

   If the attribute lives elsewhere, add the module path to `DRIVER_MODULES`.

2. **The launch options.** `_launch_context()` calls
   `launch_persistent_context(user_data_dir=..., proxy=..., locale=...,
   timezone_id=..., args=[...])`. Start the stack with the browser profile and
   mint once:

   ```bash
   docker compose -p dtk -f docker/compose.yml --profile browser up -d
   docker compose -p dtk -f docker/compose.yml exec api dtk identity mint --platform douyin
   ```

   A successful mint returns four or more cookies including `ttwid`, and
   `/rpc/health` then reports a real `chromium_major`. Check that number against
   the wreq emulation profile the console shows on its system page: a drift
   greater than two majors is the self-disclosure doc 04 warns about.

3. **The signing entry points.** `SIGN_SNIPPETS` lists the page globals tried in
   order. To confirm or replace them, open the platform in a real browser, find
   the function that produces `a_bogus` / `X-Bogus` in the network tab's request
   URLs, and check it from the console:

   ```js
   typeof window.byted_acrawler?.frontierSign   // douyin
   typeof window.byted_acrawler?.sign           // tiktok
   ```

   Each snippet is a self-contained function of `{url, query, userAgent}` that
   returns either `null` or an object keyed by the wire names in
   `dtk.signing.rpc.RESPONSE_FIELDS`. Add the new one to the front of the tuple;
   nothing else changes.

   End to end, once the stack is up:

   ```bash
   docker compose -p dtk -f docker/compose.yml exec api python -c "
   import asyncio, httpx
   from dtk.signing.rpc import RpcSigner
   from dtk.signing.base import RequestSpec, StaticFingerprint
   async def main():
       async with httpx.AsyncClient() as c:
           signer = RpcSigner(c, 'http://browser-rpc:9000')
           spec = RequestSpec.get('https://www.douyin.com/aweme/v1/web/aweme/detail/',
                                  params={'aweme_id': '7'})
           print(await signer.sign(spec, StaticFingerprint(user_agent='Mozilla/5.0')))
   asyncio.run(main())"
   ```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `refusing to start: DTK_SECRET_KEY is not set` | `.env` missing at the repository root, or not picked up; write it with the recipe above |
| `redis: REDIS_PASSWORD is empty` | same |
| `browser-rpc` mints nothing, `executable doesn't exist` | the pinned revision installs its browser outside `PLAYWRIGHT_BROWSERS_PATH`; see `Dockerfile.browser` |
| api healthy but the console 404s | the console stage produced no `dist/`; check `web/package.json` has a `build` script |
| `no worker entry point in this image` | the worker module is named differently; set `DTK_WORKER_COMMAND` |
| browser-rpc unhealthy, log says `not installed in this image` | built without `CLOAKBROWSER_COMMIT` |
| minting always times out | the proxy cannot reach the platform; test the proxy on its own before blaming the browser |
| renderers crash under load | `shm_size` was lowered; Chromium needs more than the 64MB default |
