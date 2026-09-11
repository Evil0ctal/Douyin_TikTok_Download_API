# Installation and deployment

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/02-installation.md)

This page takes you from an empty host to a running instance you can leave alone, and explains
enough of the compose file that you can change it without breaking it. After reading it you should
be able to size a machine, decide which of the two optional containers you actually want, put TLS in
front of the API, upgrade without losing data, and run the whole thing without Docker if you prefer.

If you only want the shortest path to a working instance, read [Quick start](./01-quickstart.md)
first and come back here when something needs changing.

## Which path

There are two ways to install this. Pick one before reading further.

| | Docker Compose | By hand |
|---|---|---|
| Who it is for | Almost everyone, production included | Environments where Docker is not an option, or you are changing the code |
| You install on the host | Docker, and nothing else | PostgreSQL 17 + TimescaleDB, Redis 8, Python 3.12, uv — plus Node 22 if you build the console |
| Time to a running stack | However long the pull takes | Half an hour and up, half of it on TimescaleDB |
| Upgrading | Change the tag, `pull`, migrate, `up -d` | `git pull`, `uv sync`, migrate, restart the systemd units |
| Resource ceilings, read-only rootfs, dropped capabilities, non-root user | Already in the compose file | You rebuild them yourself with systemd |
| When you ask me about a problem | I can reproduce it exactly | Your box is one of a kind and I am guessing |
| Start reading at | [First install](#first-install) | [Running without Docker](#running-without-docker) for development, [A production install without Docker](#a-production-install-without-docker) for a server |

**Docker is the recommendation**, not because containers are fashionable but because everything this
stack is fussy about is already pinned in the compose file: the database has to be Postgres 17 with
the TimescaleDB extension (a plain `postgres:17` image will not do), the browser image pins
CloakBrowser to one commit, the four services are sequenced by health checks, and every container's
memory, CPU and PID ceilings and read-only root filesystem are written down. By hand you rebuild all
of that yourself — [A production install without Docker](#a-production-install-without-docker) lists
what you are putting back, and it is worth reading before you choose.

### Contents

**Before you start**

- [Installing and managing it with the script](#installing-and-managing-it-with-the-script) — the guided install, and the operations menu afterwards
- [What you need](#what-you-need) — sizing, disk, and the two things to know before pointing this at your own database
- [What the shipped defaults are written for](#what-the-shipped-defaults-are-written-for) · [Three sizes](#three-sizes) · [Changing the limits](#changing-the-limits)
- [Network preparation in mainland China](#network-preparation-in-mainland-china) — switch your mirrors first; without them the install usually dies on the image pull
- [The stack at a glance](#the-stack-at-a-glance) — how many containers, and which talks to which

**With Docker**

- [First install](#first-install) — empty directory to a console you can log in to
- [The compose file, service by service](#the-compose-file-service-by-service) — read this before editing it
- [The two optional profiles](#the-two-optional-profiles) — whether you want `browser` and `downloader`
- [Building the browser image: the CloakBrowser pin](#building-the-browser-image-the-cloakbrowser-pin) — this image is not published; you build it
- [Environment variables](#environment-variables) — including the two ways Compose reads `.env`, which catches everyone once
- [Volumes](#volumes) · [Ports and binding](#ports-and-binding) · [Behind a reverse proxy](#behind-a-reverse-proxy)
- [Verifying an install](#verifying-an-install) — run these; do not judge it by whether the page loads
- [Upgrading](#upgrading) · [Scaling: more workers](#scaling-more-workers)

**By hand**

- [Running without Docker](#running-without-docker) — how the project is developed: uv for dependencies, containers still for Postgres and Redis
- [A production install without Docker](#a-production-install-without-docker) — all of it by hand, systemd units included, verified on a clean Ubuntu 24.04
- [Running the worker on its own](#running-the-worker-on-its-own)

**When something is wrong**

- [Starting over](#starting-over) — stopping with the data intact, and stopping with it gone
- [Where to go next](#where-to-go-next)

## What you need

| Requirement | Why |
|---|---|
| Docker Engine with Compose v2 (v2.24 or newer) | v2.24 is where Compose added the long form `env_file: - path: … required: false`, which `docker/compose.yml` uses, and `COMPOSE_ENV_FILES`, which this page suggests. Nothing in the repository enforces the floor, so it is a property of the features used, not a declared minimum |
| Linux, macOS or WSL2 host | Nothing in the stack is platform-specific, but the browser container wants a lot of memory and is happiest on Linux |
| 2 GiB RAM for the core stack, 4 GiB to be safe | 2 GiB is enough in practice — measured usage at rest is about 295 MiB across the four core containers (see the table below) — but the container ceilings sum to 3.5 GiB, so 4 GiB is the safe figure |
| ~8 GiB RAM if you run the `browser` profile | The browser container is capped at 4 GiB and genuinely uses it |
| ~15 GB of free disk | Roughly 4.5 GB of images for the core stack and ~6.3 GB with the browser container, before volumes and build cache; then Postgres and, if you enable media downloads, the media volume (capped at 2 GiB by default). 15 GB free is comfortable |
| Outbound HTTPS | The worker and the browser container talk to the platforms, usually through proxies you supply |

You do **not** need to install Postgres, Redis, Python, Node or a browser on the host. The compose
file brings all of them, pinned.

Two things about the database are worth knowing before you swap it for one you already run:

- The schema requires the **TimescaleDB** extension. Request logs, identity events and content
  snapshots are hypertables, and the first migration checks `pg_available_extensions` and refuses
  with an explanation rather than creating half a schema. The compose file uses
  `timescale/timescaledb-ha:pg17`; a stock `postgres:17` image will not do.
- Nothing in the repository ships a default password or key. The entrypoint exits `78`
  (`EX_CONFIG`) if `DTK_SECRET_KEY` is missing or shorter than 32 characters, and the redis
  container exits if `REDIS_PASSWORD` is empty. That is deliberate: a key that is identical on
  every install is not encryption.

Resource ceilings and measured usage, both read off `docker/compose.yml` (the measurements in its
comments were taken on 2026-09-08):

| Service | `mem_limit` | `cpus` | `pids_limit` | Measured at rest |
|---|---|---|---|---|
| `postgres` | 2g | — | 256 | 112 MiB |
| `redis` | 512m | — | 128 | 8 MiB |
| `api` | 512m | 2.0 | 256 | 104 MiB |
| `worker` | 512m | 2.0 | 256 | 71 MiB |
| `browser-rpc` | 4g | 4.0 | 1024 | 2.57 GiB warm, 629% CPU |
| `downloader` | 512m | 2.0 | 128 | streaming buffers only |

The limits exist so one overloaded component cannot destabilise the rest. `postgres` is the
exception with no CPU cap and a loose memory cap, because a tight ceiling on Postgres degrades
correctness rather than protecting anything — its 2g is there to stop a runaway query taking the
host, not to be a budget it has to live inside.

### What the shipped defaults are written for

Add up the ceilings in the table above: **about 8 GiB of memory**, and
`browser-rpc` asks for 4 CPUs. So `docker/compose.yml` describes a
**4 vCPU / 8 GiB** machine — on one of those, `up -d` needs no changes at all.

Ceilings are not reservations, so day-to-day usage sits far below them. But on
a smaller box this is **not "slower", it is "will not start"**:

```
Error response from daemon: range of CPUs is from 0.01 to 2.00,
as there are only 2 CPUs available
```

Docker refuses to start a container asking for more CPUs than the host has. It
is a hard error, not a cap that degrades quietly.

### Three sizes

| Size | Spec | What you get | Compose changes |
|---|---|---|---|
| No browser | 1 vCPU / 2 GiB | Manual cookie import, no automatic minting. The four core containers idle at about 295 MiB | None — just leave `--profile browser` off |
| Full, minimum | 2 vCPU / 4 GiB **plus swap** | Everything | **Yes**, see the override below |
| Full, recommended | 4 vCPU / 8 GiB | Everything | None |

The minimum row is measured rather than estimated. A 2 vCPU / 3.8 GiB VPS
running the whole stack with the browser profile on:

| | Idle | Peak during a mint |
|---|---|---|
| `browser-rpc` | 234 MiB | **845 MiB** |
| `api` | 115 MiB | 128 MiB |
| `postgres` | 80 MiB | 88 MiB |
| `worker` / `redis` / `downloader` | 82 / 4 / 4 MiB | barely move |
| **Host total** | **1.0 GiB** | **1.1 GiB** |

What sets the floor is the mint peak, not the idle figure: Chromium spikes while
it opens a context, and that is the same moment Postgres is holding its buffers.
**Give a box this size swap** — 2 GiB with `vm.swappiness=10` is enough. It is
not there to be used; it is there so that peak becomes a slow second instead of
the kernel picking a process to kill, which is usually Postgres rather than the
one that actually grew.

### Changing the limits

Do not edit `docker/compose.yml`. It describes a normal machine, and the way
yours differs should be visible in a file of its own. Write `compose.host.yml`
at the repository root:

```yaml
services:
  postgres:    { mem_limit: 1g,    memswap_limit: 2g }
  redis:       { mem_limit: 320m,  memswap_limit: 512m }
  api:         { mem_limit: 448m,  memswap_limit: 768m,  cpus: 1.5 }
  worker:      { mem_limit: 448m,  memswap_limit: 768m }
  browser-rpc: { mem_limit: 1200m, memswap_limit: 2400m, cpus: 1.5 }
  downloader:  { mem_limit: 192m,  memswap_limit: 384m,  cpus: 1.0 }
```

Then pass it on every command:

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk \
  -f docker/compose.yml -f compose.host.yml --profile browser up -d
```

Three arguments, none of them optional, all of them easy to forget — which is
what a wrapper is for. (The [guided script](#installing-and-managing-it-with-the-script)
writes this file, and the `compose.host.yml` above it, with the numbers worked
out for your machine. What follows is what it does, and how to write it
yourself.)

```bash
cat > dtkctl <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_ENV_FILES=.env
exec docker compose -p dtk -f docker/compose.yml -f compose.host.yml \
  --profile browser --profile downloader "$@"
EOF
chmod +x dtkctl
./dtkctl up -d
./dtkctl logs -f api
```

Knobs worth turning, most effective first:

- **Give `browser-rpc` fewer `cpus`.** Signing and minting are both off the
  request path, so it is exactly the service that should yield when the machine
  is busy. 1.5 rather than 2.0 leaves `api` a core to answer on.
- **`DTK_BROWSER_WARM_CONTEXTS` (default 1).** Each resident signing page costs
  roughly 300 MiB. Leave it at 1 on a small host.
- **`DTK_DOWNLOADER_WORKERS` / `DTK_DOWNLOADER_ITEM_WORKERS` (4 each).** The
  concurrent-transfer ceiling is their product. Drop both to 2 on one core.
- **`pool.target_size` (a console setting).** A bigger pool means more mints and
  more rows in Postgres. 8 is a sensible number on a small box.

## Installing and managing it with the script

There is a guided script that walks the Docker route above for you, and then
manages the instance afterwards. It exists in both languages — see
[install/README.md](../../install/README.md).

```bash
curl -fsSL https://raw.githubusercontent.com/Evil0ctal/Douyin_TikTok_Download_API/main/install/install.sh -o install.sh
less install.sh    # read it before you run it
bash install.sh
```

**The first run** works out the distribution, checks for Docker (and offers to
install it), sizes the resource ceilings to this machine's cores and memory,
then asks six questions: where, which address and port, the browser container,
media downloads, published images or a build. It writes three things, and they
are exactly the three this page goes on to teach you to write by hand:

| What it writes | The section here |
|---|---|
| `.env`, secrets from `openssl rand`, mode 0600 | [First install](#first-install), step 2 |
| `compose.host.yml`, ceilings scaled to this host | [Changing the limits](#changing-the-limits) |
| `dtkctl`, carrying the project name, both compose files and `COMPOSE_ENV_FILES` | the same section |

**Run it again** and it finds the install — by asking Docker, so it does not
matter where you put it — and opens a menu instead of installing:

- **Status** — versions, containers, disk, and a comparison against the latest
  GitHub release
- **Upgrade** — pins `DTK_IMAGE_TAG` to the target, pulls, migrates, restarts.
  Migrations run before the swap, so a failure leaves the old containers up
  with nothing changed
- **Manage** — passwords, an extra administrator, account list, backup,
  restore, self check, logs, restart, any runtime setting, disk cleanup
- **Stop or remove** — three levels: stop, stop and delete the volumes, or that
  plus the install directory. The last two require typing `delete`, not a y/n

`bash install.sh --manage` goes straight to that menu.

**The rest of this page is still worth reading.** What the script does is what
follows; it just does it for you. When you want to know why it chose something,
or to deviate from it, the answer is below.


## Network preparation in mainland China

Skip this section unless you are installing from a machine in mainland China.

The short version: **change your mirrors before you start.** Almost everything this install pulls
lives outside the Great Firewall — Docker Hub, PyPI, the npm registry, GitHub, Debian's apt
repositories. Without mirrors it is not slower, it usually times out on the image pull, and you
conclude the project is broken.

Treat the mirrors below as leads that worked when this page was written, not as guarantees. Public
mirrors have a history of disappearing — a wave of university sites dropped their Docker Hub proxies
in 2024 — so each part ends with a way to check for yourself.

### Docker images

On the recommended path, where you only pull prebuilt images, there are four to fetch from Docker Hub:

| Image | Used by |
|---|---|
| `evil0ctal/douyin_tiktok_download_api` | `api`, `worker` and `migrate`, which share one image |
| `evil0ctal/douyin_tiktok_download_api-downloader` | `downloader` (optional profile) |
| `timescale/timescaledb-ha:pg17` | `postgres` |
| `redis:8-alpine` | `redis` |

Mirrors go in `/etc/docker/daemon.json`:

```json
{
  "registry-mirrors": ["https://<your-account-id>.mirror.aliyuncs.com"]
}
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart docker
docker info | grep -A3 "Registry Mirrors"   # nothing printed means it did not take
```

Sources, most to least reliable:

- **Your own cloud provider's accelerator.** Aliyun issues a per-account address from the Container
  Registry console; Tencent Cloud is `https://mirror.ccs.tencentyun.com` but only resolves inside
  Tencent Cloud's network; Huawei Cloud and Volcengine are the same idea. These are the steady ones,
  because they are a vendor serving its own customers rather than a volunteer site.
- **Public accelerators**, such as `https://docker.m.daocloud.io`. Use one while it works.
  `registry-mirrors` takes a list and Docker tries them in order.
- **When none of them work**: `docker pull` on a machine outside, then `docker save` / `docker load`
  across. Or run a registry proxy on a host outside. It sounds primitive; it costs less time than
  cycling through accelerators that time out.

**Note that the `browser-rpc` image is not published** and has to be built locally — see
[Building the browser image: the CloakBrowser pin](#building-the-browser-image-the-cloakbrowser-pin).
Its build reaches Debian's apt repositories, PyPI, GitHub (CloakBrowser installs as
`pip install "cloakbrowser @ git+https://github.com/…"`) and then downloads a browser binary.
**This is the most network-sensitive step in the whole install**, and most of what follows exists for
it. If you do not need minted identities yet, leave the `browser` profile off — importing your own
cookies works without it.

### System package mirrors

[The bare-metal install](#a-production-install-without-docker) installs PostgreSQL 17, TimescaleDB
and Redis from their official apt repositories.

One Ubuntu 24.04 trap worth calling out: sources moved to the deb822 format at
`/etc/apt/sources.list.d/ubuntu.sources`. The `/etc/apt/sources.list` that older guides edit no
longer does anything, and editing it fails silently — you will believe you switched mirrors.

```bash
# Ubuntu 24.04, switching to the Tsinghua mirror
sudo sed -i \
  -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|http://ports.ubuntu.com/ubuntu-ports|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports|g' \
  /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update
```

The third expression is for arm64 — ARM Ubuntu uses `ports.ubuntu.com` rather than
`archive.ubuntu.com`. On x86 it matches nothing and is harmless.

Common hosts, interchangeable by domain: `mirrors.tuna.tsinghua.edu.cn` (Tsinghua),
`mirrors.ustc.edu.cn` (USTC), `mirrors.aliyun.com` (Aliyun), `repo.huaweicloud.com` (Huawei),
`mirrors.cloud.tencent.com` (Tencent). On a given cloud, prefer that cloud's own — it stays on the
internal network, which is both faster and not metered.

The PostgreSQL apt repository (`apt.postgresql.org`) is mirrored at Tsinghua and elsewhere, on a path
like `https://mirrors.tuna.tsinghua.edu.cn/postgresql/repos/apt/` — check the mirror's own help page,
these paths do move.

TimescaleDB's packages are on packagecloud.io, which as far as I know has no mirror in China. If that
step stalls, either put apt behind a proxy (`sudo -E apt-get …` with `https_proxy` set) or go the
Docker route — pulling `timescale/timescaledb-ha:pg17` through an accelerator beats installing
packages from packagecloud by a wide margin. That is one of the reasons Docker is the recommendation.

### Python, pip and uv

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
# uv itself: the official install script goes through GitHub, so install from PyPI when it drags
pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple

# The dependency index: uv reads this variable
export UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
uv sync --frozen --no-dev
```

Put it in `~/.bashrc`, or in a systemd unit's `Environment=`. Older uv reads `UV_INDEX_URL`; setting
both does no harm.

For pip itself:

```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

Common PyPI mirrors: Tsinghua `https://pypi.tuna.tsinghua.edu.cn/simple`, Aliyun
`https://mirrors.aliyun.com/pypi/simple/`, USTC `https://mirrors.ustc.edu.cn/pypi/simple`, Tencent
`https://mirrors.cloud.tencent.com/pypi/simple`, Huawei
`https://repo.huaweicloud.com/repository/pypi/simple`.

One that is easy to miss: `uv python install` downloads its interpreter from GitHub Releases. Set
`UV_PYTHON_INSTALL_MIRROR`, or just use the system Python — this project wants `>=3.12,<3.14`, and
Ubuntu 24.04 ships 3.12.

### Node and npm

Only needed if you build the console or the application image yourself; pulling prebuilt images needs
no Node at all.

```bash
npm config set registry https://registry.npmmirror.com
```

`npm ci` pulls packages with native binaries such as esbuild and rollup. In current versions those
ship as platform-specific npm packages, so switching the registry is enough — there is no separate
binary mirror to configure.

While on the subject: the Go `downloader` service needs **no** `GOPROXY`. It has no third-party
dependencies at all, only the standard library, so its build fetches no modules.

### GitHub, and proxies at build time

`git clone` and CloakBrowser's `pip install "… @ git+https://github.com/…"` both reach GitHub. The
least intrusive fix is a proxy scoped to GitHub rather than a global one:

```bash
git config --global http.https://github.com.proxy http://127.0.0.1:7890
```

Network access inside a Docker build does not read the host's git configuration; the proxy has to be
passed as a build argument (`HTTP_PROXY` and `HTTPS_PROXY` are BuildKit predefined arguments, so the
Dockerfile does not declare them):

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml build \
  --build-arg HTTP_PROXY=http://172.17.0.1:7890 \
  --build-arg HTTPS_PROXY=http://172.17.0.1:7890 \
  browser-rpc
```

`172.17.0.1` is the host as seen from the default bridge network. A common trap here: a proxy
listening only on `127.0.0.1` is unreachable from inside the container — it has to listen on
`0.0.0.0`, or at least on the docker0 address.

### Check it took

Do not go by feel:

```bash
docker info | grep -A5 "Registry Mirrors"    # did it read the accelerator
time docker pull redis:8-alpine              # fast or not, this answers it
pip config get global.index-url
npm config get registry
```

If a step still fails after switching mirrors, run that step alone and read its own error before
suspecting the project — everything listed here names the host it could not reach when it fails.

## The stack at a glance

| Service | Image | Profile | Optional | What it does |
|---|---|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg17` | default | no | Every durable row: identities, proxies, tasks, archive, request logs, settings |
| `redis` | `redis:8-alpine` | default | no | Task queue, token buckets, rate-limit counters, response cache, console sessions |
| `migrate` | `dtk-app` | default | no | One-shot: applies migrations, exits; everything else waits for it |
| `api` | `dtk-app` | default | no | REST API, MCP, the console, `/healthz` and `/readyz`; the only published port |
| `worker` | `dtk-app` | default | no | Claims tasks, drives the scheduler and identity pool, runs background jobs |
| `browser-rpc` | `dtk-browser-rpc` | `browser` | **yes** | Headless browser: mints guest identities, optional signing fallback |
| `downloader` | `dtk-downloader` | `downloader` | **yes** | Go sidecar that writes media files to a volume |

Two networks:

- **`data`** is declared `internal: true`. `postgres` and `redis` sit on it and have no route off
  the host at all — not to the internet, not to anything outside the compose project.
- **`edge`** is an ordinary bridge network. `api`, `worker`, `browser-rpc` and `downloader` are on
  it because they need egress. `browser-rpc` and `downloader` are on `edge` **only**: neither has
  any reason to reach the database, so neither can.

## First install

**1. Clone the repository.** The compose file, the Dockerfiles and the migrations all live in it,
and the default branch is v5. Everything below runs from the repository root:

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

One file tells you whether you have the right tree — if it is missing you are on the `v4` branch and
nothing on this page applies:

```bash
ls docker/compose.yml
```

**2. Write `.env`.** Hex rather than base64 for the two passwords, because they end up inside
connection URLs where `+` and `/` would need escaping:

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

`.env.example` in the repository root lists the bootstrap variables, most of them with a comment
(`DTK_BACKUP_DIR` is missing from it, and a handful of the rest carry no comment); copy from it if
you would rather start from a template. Four of its lines — `DTK_BIND_HOST`, `DTK_BIND_PORT`,
`DTK_REDIS_MAXMEMORY` and `CLOAKBROWSER_COMMIT` — are read by Compose interpolation, so they take
effect only if the file is also fed to Compose, as `docker/.env`, from your shell, or through
`COMPOSE_ENV_FILES`. The last two are read by nothing else at all; the first two are application
variables as well, but the `api` container pins its own bind address in `environment:`, so the
copies in this file move the published port through interpolation or not at all. See
[The two ways Compose reads `.env`](#the-two-ways-compose-reads-env) below.
`.env` is git-ignored and excluded by `.dockerignore`, so it reaches the containers as an env file
and never as image content.

**3. Bring the stack up.** The first run builds the `dtk-app` image, which includes an `npm ci` and
a `uv sync`, so expect a few minutes:

```bash
docker compose -p dtk -f docker/compose.yml up -d --wait
```

`--wait` returns only when every service reports healthy. The `api` healthcheck is a readiness
probe, so a successful `--wait` means the API can actually serve a request.

**4. Read the setup token out of the log:**

```bash
docker compose -p dtk -f docker/compose.yml logs api
```

The API prints a banner with a URL of the form `http://127.0.0.1:8000/setup?token=…`. Open it and
create the first administrator. The token lives in Redis for 24 hours, is reused rather than
replaced across restarts (so a link you are holding keeps working), and is burnt by the fifth
failed attempt — four wrong tries are survivable. Once an account exists, the token is deleted and
`/setup` is closed. To get the link again on an instance that still has no account, restart the api
container — it reprints the same token. A new token is only issued after the old one expires or is
burnt by five failed attempts.

No configuration file needs editing at any point. Everything except the bootstrap variables lives in
the database and is edited from the console — see [Configuration](./03-configuration.md).

## The compose file, service by service

### postgres

`timescale/timescaledb-ha:pg17`, on the `data` network only. It creates the database `dtk` owned by
the user `dtk`, with `POSTGRES_HOST_AUTH_METHOD=scram-sha-256`; the password comes from
`POSTGRES_PASSWORD` in `.env`. `DTK_SECRET_KEY` is blanked in its `environment:` block — the
database has no use for the key that encrypts its own contents.

Its data directory is `/home/postgres/pgdata/data`, not the usual `/var/lib/postgresql/data`. That
is the `timescaledb-ha` image's layout; mounting the conventional path instead would leave the real
data directory inside the container and lose the database on the first rebuild. If you change the
image, check that path first.

Healthcheck: `pg_isready -U dtk -d dtk` every 10s, 12 retries, 30s start period. `stop_grace_period`
is a full minute so a checkpoint is not cut short.

### redis

`redis:8-alpine`, on the `data` network only, running as the `redis` user (the image's own
entrypoint only drops privileges when its first argument is literally `redis-server`, and this
service passes a shell command, so the drop is done in the compose file instead).

It holds the task queue, the scheduler's token buckets, rate-limit counters, the response cache, the
console's sessions and the setup token. Persistence is `appendonly yes` plus `save 60 1`.

Two settings matter:

- `--maxmemory ${DTK_REDIS_MAXMEMORY:-320mb}`, well under the 512m container limit. The response
  cache writes one entry per distinct request and keeps it for `cache.content_ttl` (30 minutes by
  default); measured entries run 16–200 KB, so a busy instance can put hundreds of megabytes here
  inside one TTL window. Without a ceiling that ends as an OOM kill of the whole container — queue
  included — rather than as a cache miss.
- `--maxmemory-policy volatile-lru`, not `allkeys-lru`. The queue list carries no TTL and so is
  never an eviction candidate. What can be evicted is what carries a TTL: cache entries, rate-limit
  counters, token buckets, sessions — all rebuildable. A queued task is not.

The container refuses to start with an empty `REDIS_PASSWORD`, and its healthcheck authenticates
the same way the application does rather than settling for an unauthenticated `PING`.

### migrate

The same `dtk-app` image as `api` and `worker`, started with the argument `migrate`. It applies
Alembic migrations up to `head` and exits; `restart: "no"`, and no healthcheck, because a one-shot
container's exit status *is* its health — which is what `service_completed_successfully` reads on
`api` and `worker`.

It exists as its own service rather than as something `api` and `worker` each attempt, because
concurrent `CREATE TABLE` races are how a first boot corrupts itself, and because nobody should have
to remember to run migrations after a pull. Migrations are idempotent, so it runs on every start.

The `DTK_SECRET_KEY` gate applies here too: a schema created for a deployment that cannot decrypt
its own credentials is worse than a failed migration, and this way the failure arrives before any
table exists.

### api

The `dtk-app` image with the argument `api`, which starts
`uvicorn dtk.api.app:create_app --factory --no-server-header`. It serves:

| Path | What it is |
|---|---|
| `/` | The React console (a single-page app; unknown paths fall back to `index.html`) |
| `/api/v1/…` | The REST API |
| `/api/setup/…` | First-run initialization, closed once an account exists |
| `/mcp/` | The MCP endpoint. Keep the trailing slash: `/mcp` answers `307 Temporary Redirect` to it — see [MCP and AI agents](./12-mcp.md) |
| `/docs` | The console's own API reference page |
| `/swagger`, `/redoc`, `/openapi.json` | The generated API document, without logging in |
| `/healthz` | Liveness. Touches no dependency on purpose |
| `/readyz` | Readiness: probes Postgres and Redis, `503` if either is down |

It performs no upstream platform requests itself — it authenticates, validates, hands work to the
service layer and assembles the reply. That is why it is the only container that publishes a port
and the only one that needs to be reachable.

Five variables are pinned in its `environment:` block and therefore cannot be overridden from
`.env`, because `environment:` wins over `env_file:`: `DTK_BIND_HOST=0.0.0.0`, `DTK_BIND_PORT=8000`,
`DTK_CONSOLE_DIR=/app/web/dist`, `DTK_BACKUP_DIR=/var/lib/dtk/backups` and
`DTK_MEDIA_DIR=/var/lib/dtk/media`. Only the first four matter. Nothing reads `DTK_MEDIA_DIR` — the
media path is the constant `MEDIA_PATH` in `dtk.ops.capacity` — so the compose file sets it and no
code consults it, which is why it appears in none of the variable tables below. The bind address is
the container's own; what limits exposure is the published address, not this value.

Volumes: `backup-data` read-write at `/var/lib/dtk/backups` (it lists and serves the archives the
worker writes) and `media-data` **read-only** at `/var/lib/dtk/media`. The read-only mount is
deliberate: the console has to be able to hand a stored file to a browser, and the API is the only
component with authentication, scopes, rate limiting and an audit trail — so it serves the bytes,
while the downloader stays a sink and never a relay.

Healthcheck: `GET /readyz` every 15s, 5 retries, 40s start period. `browser-rpc` is deliberately not
part of readiness — minting is off the request path, so an instance without it is degraded, not
unready.

### worker

The same image with the argument `worker`, which runs `python -m dtk.worker`. It claims tasks from
the Redis queue, runs them through the scheduler and the identity pool, and writes results; it also
runs the pool filler, the proxy prober, retention, watchlist ticks and archive rechecks.

One worker runs 4 tasks at a time. Real concurrency is bounded by the identity pool, not by that
number — extra slots simply wait on the scheduler.

It has no port, so its healthcheck opens a TCP connection to the address in `DTK_REDIS_URL`: a
worker that cannot reach Redis does no work at all, and that is the failure worth detecting.

`stop_grace_period` is 45s, long enough for an in-flight upstream request to finish and release its
identity lease rather than leaving it to expire by TTL. Killing a worker mid-request wastes an
identity's quota.

### browser-rpc (profile `browser`)

A small FastAPI service wrapping a headless browser, with two jobs, both off the hot path: mint
guest identities through an identity's own proxy, and sign requests with the platform's own
JavaScript when the in-process algorithm has drifted. Its RPC surface is `POST /rpc/mint`,
`POST /rpc/sign` and `GET /rpc/health`.

It is the heaviest thing in the stack and the reason the resource limits exist. Warm, it was
measured at 2.57 GiB and 629% CPU — six cores' worth of Chromium. The cap of 4 cores is deliberately
below that: signing and minting are off the request path, so this is exactly the service that should
yield when the machine is busy.

Everything it writes is RAM:

| Mount | Size | Why |
|---|---|---|
| `/tmp` | tmpfs, 3g | The driver passes `--disable-dev-shm-usage`, so every renderer's shared memory lands here. Roughly 300 MB per resident context; 1g fitted barely three, and opening a fourth crashed Chromium in a way that reads exactly like a platform block |
| `/profiles` | tmpfs, 1g | Single-use mint profiles. A tmpfs makes "never reused, never on disk" structural rather than a promise about cleanup code |
| `shm_size` | 1gb | Insurance for a future build that stops disabling `/dev/shm`; it is not the fix for renderer crashes |

If you raise `DTK_BROWSER_WARM_CONTEXTS`, raise the `/tmp` tmpfs in `docker/compose.yml` with it:
budget roughly `300M × warm_contexts × 2 platforms`, plus a mint in flight.

It drops all capabilities and adds back only `SYS_ADMIN`, which Chromium's own sandbox needs to
create user namespaces. That is the lesser evil against `--no-sandbox`, which would remove the
renderer boundary entirely. `init: true` because Chromium leaves zombies behind and PID 1 has to
reap them.

Its healthcheck reads the `status` field of the response body, not just the status code:
`/rpc/health` answers `200` even when the browser backend failed to start — deliberately, so you get
a reason in the log instead of a restart loop — and a probe that stopped at the status code would
call an image built without a browser healthy.

### downloader (profile `downloader`)

A single static Go binary on a `scratch` base: no shell, no package manager, nothing to execute but
the binary. It is the one container that dials arbitrary CDN hosts and writes their bytes to a disk
you care about, so it has the least in it. Its healthcheck is the binary probing itself
(`downloader healthcheck`) because a scratch image has no `curl`, and adding one would undo the
reason the image is scratch.

It mounts `media-data` read-write at `/var/lib/dtk/media` and is on the `edge` network only.
`DTK_SECRET_KEY`, `DTK_DATABASE_URL` and `DTK_REDIS_URL` are all blanked in its `environment:` block
even though the shared env file carries them: this is the component whose compromise would matter
most, and it is the one that needs none of them.

Files land as `<platform>/<author>/<content id>/` with a `meta.json` beside them. Transfers are
streamed with `io.Copy`, so resident memory is a handful of 32 KiB buffers per concurrent file plus
the Go runtime — not the size of the files.

## The two optional profiles

Neither profile starts by default. Leaving one off is a supported deployment, not a broken one.

**The browser profile** gives you automatic identity minting and the browser signing fallback:

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

Two separate things have to be true: the container has to be running (the profile), and `api` and
`worker` have to be told where it is (`DTK_BROWSER_RPC_URL` in `.env`). Left unset, they run
manual-import only — you paste cookies in from a browser and the pool works from those. That is the
correct degraded mode rather than an error, and the pool falls back to it on its own when
`browser-rpc` is unreachable. See [Identities and proxies](./06-identities-and-proxies.md).

**The downloader profile** gives you media stored on your own disk:

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

Same shape: profile plus `DTK_DOWNLOADER_URL`. Without it, `POST /api/v1/downloads` answers `501`
with a message saying how to turn it on, and nothing else changes — parsing, the archive and the
watchlist are all unaffected. Stored media is capped by the `media.max_bytes` setting, 2 GiB by
default; past it the oldest unpinned downloads are removed and an alert says what went. See
[Downloads, library and watchlist](./08-downloads-and-library.md).

Both profiles at once:

```bash
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser --profile downloader up -d --build
```

`build`, `config` and `down` have to repeat the profile flags: those commands read the compose file
rather than the running stack, so without the flags Compose does not know those services exist — and
a `down` without them stops the core services and leaves the profile containers running. `ps` and
`logs <service>` find the running containers by project label and need no flags. If you get tired of
repeating them, export `COMPOSE_PROFILES=browser,downloader` in your shell.

## Building the browser image: the CloakBrowser pin

At least three GitHub organizations publish repositories called `cloakbrowser` with identical
descriptions. "The latest cloakbrowser" therefore does not identify any particular piece of
software, so the image takes a repository **and a commit**, and stamps the pair into
`DTK_BROWSER_BACKEND_PIN`, which `/rpc/health` reports. "Which browser is this?" is answerable from
the running system rather than from build history.

```bash
CLOAKBROWSER_REPO=https://github.com/CloakHQ/cloakbrowser \
CLOAKBROWSER_COMMIT="<40-char sha>" \
  docker compose -p dtk -f docker/compose.yml --profile browser build browser-rpc
```

`.env.example` carries a known-good starting point:
`CLOAKBROWSER_COMMIT=f04c23da285b3b3d3cf10c8f9d282e7adc1d52ce` (CloakBrowser 0.5.10, Chromium 146,
verified end to end on 2026-09-08). Change it deliberately.

**These two are build arguments, resolved by Compose interpolation, so the repository-root `.env` is
the wrong place for them.** Put them in your shell as shown above, in `docker/.env`, or run the
build with `COMPOSE_ENV_FILES=.env` so Compose interpolates from the root file. The next section
explains why.

Build without a commit and the image still contains the service and the `fake` backend, but no
Chromium. It then refuses to mint unless you set `DTK_BROWSER_BACKEND=fake` on purpose. Nothing
falls back to the fake backend on its own: a pool quietly filling with synthetic identities looks
healthy right up to the moment every request comes back risk-controlled.

To pin an upstream image that already carries the browser instead of building from source, pass a
digest:

```bash
docker build -f docker/Dockerfile.browser \
  --build-arg "BROWSER_BASE_IMAGE=ghcr.io/<org>/cloakbrowser@sha256:<digest>" \
  --build-arg "CLOAKBROWSER_COMMIT=<sha>" -t dtk-browser-rpc:dev .
```

Build arguments, with the defaults the Dockerfiles declare:

| Image | Argument | Default | Meaning |
|---|---|---|---|
| `Dockerfile` | `PYTHON_VERSION` | `3.12.8` (compose passes the same) | Base Python; bump together with the runtime pin in `pyproject.toml` |
| `Dockerfile` | `NODE_VERSION` | `22` | Node used for the console build stage only; it never reaches the runtime image |
| `Dockerfile` | `UV_VERSION` | `0.9.18` | Pinned, because `uv sync --frozen` against `uv.lock` is the reproducibility guarantee |
| `Dockerfile` | `CONSOLE_OUT_DIR` | `dist` | Checked with `test -d`, so a Vite config writing elsewhere fails the build instead of shipping an image that serves nothing |
| `Dockerfile`, `Dockerfile.browser` | `DTK_UID` / `DTK_GID` | `10001` | The unprivileged runtime user |
| `Dockerfile.browser` | `BROWSER_BASE_IMAGE` | `python:${PYTHON_VERSION}-slim-bookworm` | Override with a digest-pinned image that already carries CloakBrowser |
| `Dockerfile.browser` | `CLOAKBROWSER_REPO` | `https://github.com/CloakHQ/cloakbrowser` | The repository to install from |
| `Dockerfile.browser` | `CLOAKBROWSER_COMMIT` | *(empty)* | The revision. Empty means no browser in the image |
| `Dockerfile.browser` | `CLOAKBROWSER_INSTALL_CMD` | `python -m cloakbrowser install` | The command that fetches the browser binaries; it belongs to the pinned revision, so verify it when you change the commit |
| `Dockerfile.browser` | `CLOAKBROWSER_HOME` | `/opt/cloakbrowser` | Where the browser is installed. The rootfs is read-only at runtime, so the entrypoint symlinks it into the container's `HOME` |
| `Dockerfile.downloader` | `VERSION` | `dev` (compose passes `${DTK_IMAGE_TAG:-dev}`) | Stamped into the binary |

## Environment variables

### The two ways Compose reads `.env`

This trips up nearly everyone, so it is worth being explicit.

- **Service variables** come from `env_file: ../.env`, and Compose resolves that path relative to
  the compose file. The compose project directory is `docker/`, so `../.env` is the
  **repository-root** `.env`. A missing file is not an error (`required: false`) — the entrypoint
  produces the useful message instead.
- **Interpolation** — the `${…}` expressions inside `compose.yml` itself — does **not** read that
  file. It reads `docker/.env` or your shell. Six variables appear, in nine such expressions:
  `DTK_BIND_HOST`, `DTK_BIND_PORT` (the published address), `DTK_IMAGE_TAG` (image tags, in four
  places), `DTK_REDIS_MAXMEMORY` (the redis ceiling) and `CLOAKBROWSER_REPO` / `CLOAKBROWSER_COMMIT`
  (build arguments).

So this works:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

and so does pointing Compose at the root file explicitly:

```bash
COMPOSE_ENV_FILES=.env docker compose -p dtk -f docker/compose.yml up -d
```

but putting `DTK_BIND_HOST=0.0.0.0` in the root `.env` and expecting the port to move does not. You
can always check what Compose resolved without starting anything:

```bash
docker compose -p dtk -f docker/compose.yml config
```

One more rule on top: `environment:` beats `env_file:`. `api` pins `DTK_BIND_HOST` and
`DTK_BIND_PORT` to `0.0.0.0:8000` in its own `environment:` block, so those two values in the root
`.env` change nothing inside the compose stack. They still decide what `dtk serve` binds to outside
Docker.

### Bootstrap variables

These are read once at process start, from the environment, because they are needed before the
database is reachable. Changing one requires a restart. They are the complete contents of
`BootstrapSettings` in `src/dtk/core/config.py`, and they all take the `DTK_` prefix.

| Variable | Default | What it does |
|---|---|---|
| `DTK_SECRET_KEY` | *(none — required)* | Master key for every stored cookie and proxy credential. At least 32 characters. Generate with `openssl rand -base64 48`. Changing it makes already-stored credentials undecryptable |
| `DTK_DATABASE_URL` | `postgresql+asyncpg://dtk:dtk@postgres:5432/dtk` | The database. The driver must be `asyncpg` |
| `DTK_REDIS_URL` | `redis://redis:6379/0` | Queue, cache and counters |
| `DTK_BIND_HOST` | `127.0.0.1` | Listen address. Pinned to `0.0.0.0` inside the api container |
| `DTK_BIND_PORT` | `8000` | Listen port. Pinned to `8000` inside the api container |
| `DTK_LOG_LEVEL` | `info` | Log level |
| `DTK_LOG_JSON` | `true` | Structured JSON logs. Set `false` for human-readable output while debugging |
| `DTK_BROWSER_RPC_URL` | *(empty)* | Where `browser-rpc` is. Empty disables automatic minting; the pool then relies on manual imports |
| `DTK_BACKUP_DIR` | `backups` | Where backup archives are written and listed from. The image sets it to `/var/lib/dtk/backups`; the relative default suits a source checkout |
| `DTK_DOWNLOADER_URL` | *(empty)* | Where the downloader sidecar is. Empty disables media downloads entirely |
| `DTK_DOWNLOADER_TOKEN` | *(empty)* | Optional shared secret for that sidecar. Empty means the sidecar checks nothing. It must match on both sides |

### Variables read outside the bootstrap settings

| Variable | Default | Read by | What it does |
|---|---|---|---|
| `DTK_FORWARDED_ALLOW_IPS` | *(empty)* | `docker/entrypoint.sh` and `dtk.api.routes.support` | Comma-separated addresses your reverse proxy connects from. Set it and the entrypoint starts uvicorn with `--proxy-headers --forwarded-allow-ips`. The application reads it too, to decide whether the per-address login failure limit is enforced — and treats `*` as if nothing were declared, because an address anyone can forge is a worse lockout key than a shared one |
| `DTK_WORKER_COMMAND` | *(empty)* | `docker/entrypoint.sh` | Overrides the worker command line entirely. Only needed if the worker module is not importable under either of its usual names |
| `DTK_CONSOLE_DIR` | `/app/web/dist` in the image | `dtk.api.console` | Where the built console lives. Fallbacks keep a source checkout working without it |
| `DTK_COMMIT`, `DTK_GIT_COMMIT`, `GIT_COMMIT` | *(empty)* | `dtk.ops.health` | Build provenance shown on the console's system page. Nothing sets them by default |

### Compose-level variables

Read by Compose interpolation, so they belong in your shell or `docker/.env`.

| Variable | Default | What it does |
|---|---|---|
| `DTK_BIND_HOST` | `127.0.0.1` | Host address the api port is published on |
| `DTK_BIND_PORT` | `8000` | Host port the api is published on |
| `DTK_IMAGE_TAG` | `dev` | Tag for `dtk-app`, `dtk-browser-rpc` and `dtk-downloader` |
| `DTK_REDIS_MAXMEMORY` | `320mb` | Redis `maxmemory`. Keep it comfortably under the container's 512m limit |
| `CLOAKBROWSER_REPO` | `https://github.com/CloakHQ/cloakbrowser` | Build argument for the browser image |
| `CLOAKBROWSER_COMMIT` | *(empty)* | Build argument for the browser image |

Plus the two the containers require directly, which do live in the root `.env`:

| Variable | What it does |
|---|---|
| `POSTGRES_PASSWORD` | Password the postgres container creates the `dtk` user with. Must match what `DTK_DATABASE_URL` carries |
| `REDIS_PASSWORD` | Password the redis container starts with, and what its healthcheck authenticates with. Must match what `DTK_REDIS_URL` carries |

Getting these out of step with the URLs is a silent failure: the containers start, and only the
connection fails. The recipe in "First install" writes both from the same shell variable for exactly
that reason.

### browser-rpc variables

All prefixed `DTK_BROWSER_`, all read once at start, all settable in the repository-root `.env` —
the browser container gets the same `env_file` as the rest of the stack. The three the container
fixes for itself (`BIND_HOST`, `BIND_PORT`, `PROFILE_ROOT`) have to agree with `expose`, the
healthcheck and the tmpfs, so they are pinned in `environment:` and cannot be overridden.

| Variable | Default | Meaning |
|---|---|---|
| `DTK_BROWSER_BACKEND` | `cloak` | `cloak` or `fake`. There is no fallback between them |
| `DTK_BROWSER_BACKEND_PIN` | set by the image | Repository and commit, reported by `/rpc/health` |
| `DTK_BROWSER_BIND_HOST` | `127.0.0.1` (compose pins `0.0.0.0`) | Listen address |
| `DTK_BROWSER_BIND_PORT` | `9000` | Listen port. Never published to the host |
| `DTK_BROWSER_PROFILE_ROOT` | `/tmp/dtk-browser-profiles` (compose pins `/profiles`) | Parent of the single-use mint profiles |
| `DTK_BROWSER_WARM_CONTEXTS` | `1` | Warm signing contexts kept resident, per platform |
| `DTK_BROWSER_WARM_REFRESH_SECONDS` | `1800` | Age at which a warm page is rebuilt; the platform ships new JavaScript regularly |
| `DTK_BROWSER_PREWARM` | `true` | Build warm pages at startup instead of on first use |
| `DTK_BROWSER_MAX_CONCURRENT_MINTS` | `2` | Browsers minting at once (~500 MB each) |
| `DTK_BROWSER_MINT_TIMEOUT_SECONDS` | `75` | Service-side mint budget, below the client's 90s |
| `DTK_BROWSER_SIGN_TIMEOUT_SECONDS` | `8` | Service-side signing budget, below the caller's ceiling (`signing.rpc_timeout_seconds`, default 60) |
| `DTK_BROWSER_CONTEXT_OPEN_TIMEOUT_SECONDS` | `45` | Budget for opening a browser context |
| `DTK_BROWSER_SDK_READY_TIMEOUT_SECONDS` | `25` | How long a fresh page may take to become able to sign, counted after navigation. Too low and you get failures that read like an algorithm change and are really impatience |
| `DTK_BROWSER_SIGN_PROXY_URL` | *(unset)* | Optional exit for the warm signing pages. Without it they load the platform from the host's own address; minting is unaffected, it always uses the identity's proxy |
| `DTK_BROWSER_GEO_PROBE_URL` | `https://ipinfo.io/json` | Echo endpoint queried *through the proxy* to learn the exit country before a context is created. Empty disables it |
| `DTK_BROWSER_GEO_PROBE_TIMEOUT_SECONDS` | `8` | Budget for that probe. It never fails a mint |
| `DTK_BROWSER_DEFAULT_COUNTRY` | `US` | Two-letter code used when neither the caller nor the probe knows |
| `DTK_BROWSER_HEADLESS` | `true` | Off only for debugging on a machine with a display |
| `DTK_BROWSER_LOG_LEVEL` | `info` | Log level for this service |

### downloader variables

All prefixed `DTK_DOWNLOADER_`, read once at start by the Go binary, settable in the root `.env`.
`BIND` and `ROOT` are pinned by the compose file because they have to agree with `expose`, the
healthcheck and the volume mount.

| Variable | Default | Meaning |
|---|---|---|
| `DTK_DOWNLOADER_BIND` | `0.0.0.0:9100` | Listen address (pinned by compose) |
| `DTK_DOWNLOADER_ROOT` | `/var/lib/dtk/media` | Where files are written (pinned by compose) |
| `DTK_DOWNLOADER_TOKEN` | *(empty)* | Shared secret; must match `DTK_DOWNLOADER_TOKEN` on the api side |
| `DTK_DOWNLOADER_WORKERS` | `4` | Jobs in parallel |
| `DTK_DOWNLOADER_ITEM_WORKERS` | `4` | Files in parallel within one job. Concurrent transfers are the product of these two |
| `DTK_DOWNLOADER_QUEUE` | `64` | Queue depth |
| `DTK_DOWNLOADER_HISTORY` | `500` | Finished jobs kept in memory for status queries |
| `DTK_DOWNLOADER_MAX_REDIRECTS` | `5` | Redirect ceiling per transfer |
| `DTK_DOWNLOADER_TIMEOUT_SECONDS` | `900` | Ceiling for one transfer |

A value that is empty, unparseable or not positive falls back to the default rather than failing the
start.

### Runtime settings seeded from the environment

Everything else is a runtime setting: it lives in the `settings` table, is edited from the console
without a restart, and is seeded from the environment **once, at first init**. The env name is the
setting key uppercased with dots replaced by underscores and `DTK_` in front, so
`cache.content_ttl` seeds from `DTK_CACHE_CONTENT_TTL`.

After that first init the environment no longer wins. If you change one of these in `.env` and
nothing happens, that is why — change it in the console instead. The full list is in
[Configuration](./03-configuration.md).

## Volumes

Four named volumes, all managed by Docker. They are named rather than bind-mounted so a default
install cannot write into your checkout.

| Volume | Mounted at | Written by | What lives in it |
|---|---|---|---|
| `postgres-data` | `/home/postgres/pgdata/data` on `postgres` | postgres | Everything durable: identities, proxies, users, API keys, tasks, the content archive, request logs, settings |
| `redis-data` | `/data` on `redis` | redis | The append-only file and RDB snapshots: the queue survives a restart, the cache does not need to |
| `backup-data` | `/var/lib/dtk/backups` on `api` and `worker` | worker (api reads) | Backup archives. Shared because the worker writes them and the API lists and serves them |
| `media-data` | `/var/lib/dtk/media` on `downloader` (rw) and `api` (ro) | downloader | Downloaded media, laid out as `<platform>/<author>/<content id>/` with a `meta.json` |

`backup-data` and `media-data` exist because the application containers run with a read-only root
filesystem — the only other writable path they have is a 64 MB tmpfs on `/tmp`. The backup directory
is created in the image owned by the runtime user, because Docker seeds a fresh named volume from
whatever the image has at that mount point: an absent or root-owned path would yield a root-owned
volume a non-root process cannot write, and the error would arrive the moment someone asked for a
backup rather than at startup. The downloader image does the same with an empty `/seed/media`.

To see what they are called and how big they are:

```bash
docker volume ls --filter label=com.docker.compose.project=dtk
docker system df -v | grep dtk_
```

## Ports and binding

Only `api` publishes a port, and by default it publishes to loopback:

```yaml
ports:
  - "${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000"
```

`browser-rpc` and `downloader` use `expose`, which publishes nothing to the host — they are reachable
only from other containers on the `edge` network. `postgres` and `redis` publish nothing and sit on
an internal network with no route off the host at all. This is the single most important thing the
compose file does for your security posture, and it is easy to undo by accident.

Serving the outside world is meant to be an explicit act:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

Do that only with TLS in front of it. The container does not terminate TLS and will not do it for
you. If your reverse proxy runs on the same host, leave the default loopback binding and point the
proxy at `127.0.0.1:8000`; nothing needs to be published on a public interface at all.

## Behind a reverse proxy

Two problems appear the moment a proxy sits in front of the API, and both have concrete answers.

**The source address.** Everything arriving through a TLS terminator carries the terminator's
address, and everything arriving through Docker's published-port userland proxy carries the bridge
gateway's. Either way `request.client.host` is a single address shared by the whole internet.
`DTK_FORWARDED_ALLOW_IPS` is how you say otherwise:

```bash
# in the repository-root .env, alongside DTK_SECRET_KEY
DTK_FORWARDED_ALLOW_IPS=172.18.0.5
```

| Value | What the api does with the source address |
|---|---|
| unset (default) | Peer address only. It is still recorded, but no login is refused because of it: the per-address failure counter degrades to an `auth.login_spray_suspected` warning |
| your proxy's address | Real client addresses in the audit trail, the session list and the logs, and the per-address login limit refuses again |
| `*` | uvicorn believes `X-Forwarded-For` from whoever sends it, so the address is forgeable. The api treats this as undeclared and will not refuse a login on it |

The per-address limit is conditional on purpose. With one shared address it is one global bucket:
twenty failed logins from a stranger would lock every account out of the console for fifteen
minutes, repeatably — a denial of service handed to anyone who can reach the login page. The
per-account limit is not conditional, because five failures cost an attacker only the account they
are guessing at.

The address to use is the one the api container actually sees. If your proxy is a container on a
shared network, that is its container address. If it is a process on the host reaching the published
port, it is the Docker bridge gateway:

```bash
docker network inspect dtk_edge -f '{{ (index .IPAM.Config 0).Gateway }}'
```

Never set it to `*` on an instance reachable from the internet.

**Streaming.** There is no WebSocket anywhere in this project — the console polls every 5–10
seconds, deliberately. There are two streaming responses, and a proxy that buffers will break both:

| Endpoint | Content type | Note |
|---|---|---|
| `GET /api/v1/tasks/{task_id}/events` | `text/event-stream` | Server-sent events until the task settles, up to 300 seconds. A keep-alive comment goes out every 15 seconds of silence. The response already carries `Cache-Control: no-cache` and `X-Accel-Buffering: no` |
| `GET /api/v1/archive/export` | `application/x-ndjson` | One post per line, streamed a page at a time so the size of your archive is not what breaks the export |

nginx honours `X-Accel-Buffering: no`, so SSE works with an ordinary proxy block as long as the read
timeout exceeds 300 seconds:

```nginx
server {
    listen 443 ssl;
    server_name dtk.example.com;

    ssl_certificate     /etc/letsencrypt/live/dtk.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dtk.example.com/privkey.pem;

    # The API refuses bodies over 1 MiB itself, with a proper error envelope.
    # Keep nginx's limit above that so the caller gets that message.
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Longer than the 300s ceiling on the task event stream.
        proxy_read_timeout 320s;
    }
}
```

Caddy sets the `X-Forwarded-*` headers itself and needs only the buffering hint:

```caddyfile
dtk.example.com {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

The API also sets its own security headers and sends no `Server` header (`--no-server-header`).
Cross-origin access is closed by default: `security.cors_allow_origins` is empty, meaning
same-origin only. If you serve the console from a different origin than the API you have to open it
deliberately — see [Security](./15-security.md).

## Verifying an install

```bash
# Every service healthy?
docker compose -p dtk -f docker/compose.yml ps

# Liveness and readiness
curl -s http://127.0.0.1:8000/healthz
curl -s http://127.0.0.1:8000/readyz

# Which revision is on disk, and what the CLI thinks
docker compose -p dtk -f docker/compose.yml exec api dtk --version
docker compose -p dtk -f docker/compose.yml exec api dtk migrate --show

# With the browser profile: is there actually a browser in there?
docker compose -p dtk -f docker/compose.yml exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://browser-rpc:9000/rpc/health').read().decode())"
```

`/readyz` returns `503` with a per-component breakdown when Postgres or Redis is down, which is
usually faster to read than the logs. `browser-rpc` is deliberately excluded from it.

There is also an end-to-end smoke test in the repository. It brings up a **separate** compose
project (`dtk-smoke`) from the same compose file, walks the initialization flow, mints an API key,
exercises the documented contract and tears everything down including volumes:

```bash
./scripts/smoke.sh          # full run
./scripts/smoke.sh --keep   # leave the stack up for inspection
```

Because it uses the same compose file, it publishes the same `127.0.0.1:8000`. Stop your main stack
first, or the port is already taken.

## Upgrading

The least effort is the [guided script](#installing-and-managing-it-with-the-script):
run it again and pick Upgrade. It compares against the latest GitHub release,
pins `DTK_IMAGE_TAG`, pulls, migrates and restarts — with the migration before
the swap, so a failure leaves the old containers running. What follows is every
step of that, by hand.

**Back up first.** The archive is a logical export, not a volume snapshot, and restoring it needs
the same `DTK_SECRET_KEY` — credentials are exported still encrypted.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk backup create -o /var/lib/dtk/backups
docker compose -p dtk -f docker/compose.yml exec api dtk backup list --dir /var/lib/dtk/backups
```

Pass `-o` explicitly: the CLI's own default is a relative `backups/` directory, and the container's
root filesystem is read-only, so an unqualified `dtk backup create` fails inside the container.
`DTK_BACKUP_DIR` is what the console and the worker use, not the CLI. Identities are left out of an
archive unless you pass `--include-identities`, because they are bound to a proxy and an exit
address a new machine does not have.

To copy an archive off the volume:

```bash
docker compose -p dtk -f docker/compose.yml cp \
  "api:/var/lib/dtk/backups/<file>" ./
```

Then upgrade:

```bash
git pull
docker compose -p dtk -f docker/compose.yml build
docker compose -p dtk -f docker/compose.yml up -d --wait
```

`migrate` runs on every start and Alembic is idempotent, so schema changes apply themselves. Named
volumes survive rebuilds.

[Operations](./10-operations.md#upgrading-safely) writes the same upgrade out as a numbered
procedure, with the backup first and the `/readyz` and migrate-log checks afterwards; the points
below apply to it too.

Three things to know:

- **`build` only builds the services in the active profiles.** To rebuild the browser or the
  downloader image, repeat the profile flags — and supply `CLOAKBROWSER_COMMIT` again, since it is a
  build argument read from your shell or `docker/.env`, not from the root `.env`.
- **Migrations are forward-only from the CLI.** `dtk migrate` upgrades; there is no downgrade
  command. Rolling back a release that changed the schema means restoring a backup taken before it.
- **Never change `DTK_SECRET_KEY` as part of an upgrade.** Every stored cookie and proxy credential
  is encrypted with it. If it has to change, retire the affected identities and mint or import them
  again.

To time an upgrade rather than let it happen at container start, run the migration by hand:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk migrate
```

## Scaling: more workers

```bash
docker compose -p dtk -f docker/compose.yml up -d --scale worker=4
```

Each worker runs 4 tasks concurrently, so four workers is sixteen slots. That is almost certainly
not what limits you.

**Throughput is bounded by the identity pool, not by the worker count.** Every upstream request
takes an exclusive lease on an identity and passes a per-(identity, endpoint) token bucket. Workers
beyond the number of healthy identities do not add throughput; they queue. Before scaling workers,
look at the pool: `pool.target_size` defaults to 8 per platform, and raising it means minting more
identities, which means proxies. See [Identities and proxies](./06-identities-and-proxies.md).

Where extra workers *do* help: long-running background work (archive rechecks, watchlist ticks,
large download jobs) not blocking interactive parsing, and surviving the loss of one worker without
a pause. Scale the API instead if you are serving many cached reads — but that means a second
published port and a load balancer, which the compose file does not set up for you.

Postgres has no CPU cap and Redis is the smallest thing in the stack, so neither is usually the
first ceiling you meet.

## Running without Docker

Supported, and how the project is developed. You need:

| Requirement | Version |
|---|---|
| Python | `>=3.12,<3.14` |
| [uv](https://docs.astral.sh/uv/) | Any recent release |
| PostgreSQL | 17 with the **timescaledb** extension available |
| Redis | 8 |
| Node | 22, only if you want to build the console |

Clone the repository first; everything below runs from its root:

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
```

Bring up a database and a Redis if you do not already have them. The repository ships a test-fixture
compose file that publishes them on non-default ports so they cannot collide with anything:

```bash
docker compose -p dtk-test -f docker/compose.test.yml up -d --wait
# postgres on 127.0.0.1:55432 (database dtk_test), redis on 127.0.0.1:56379
```

That fixture stores Postgres data on a tmpfs and is wiped by `down -v`. It is fine for development
and wrong for anything you want to keep.

Install and configure:

```bash
uv sync --all-extras

cat > .env <<'EOF'
DTK_SECRET_KEY=replace-me-with-openssl-rand-base64-48
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test
DTK_REDIS_URL=redis://127.0.0.1:56379/0
DTK_BIND_HOST=127.0.0.1
DTK_BIND_PORT=8000
EOF
```

Apply the schema, then run the two processes in separate terminals:

```bash
uv run alembic upgrade head          # or: uv run dtk migrate
uv run dtk serve --reload            # API on 127.0.0.1:8000
uv run dtk worker                    # the task worker
```

`dtk serve` binds to `DTK_BIND_HOST`/`DTK_BIND_PORT` unless you pass `--host`/`--port`. `--reload`
cannot be combined with `--workers`, and the command tells you so before it reads the environment.

For the console you have two options. Build it once and let the API serve it:

```bash
cd web && npm ci && npm run build      # emits web/dist
```

The API finds `web/dist` from a checkout without any configuration; `DTK_CONSOLE_DIR` overrides the
search if your layout differs. Or run the Vite dev server, which proxies `/api`, `/docs`, `/redoc`,
`/openapi.json`, `/healthz` and `/readyz` to a locally running API:

```bash
cd web && npm run dev                  # http://localhost:5173
```

`DTK_API_TARGET` overrides that proxy target if your API is not on `127.0.0.1:8000`.

What you give up outside Docker: the read-only root filesystem, the dropped capabilities, the
memory and PID ceilings, and the internal network that keeps Postgres and Redis unreachable. Those
are properties of the compose file, not of the code. A bare-metal production install has to
reproduce them some other way — see [Security](./15-security.md).

The optional services can still be run by hand. `browser-rpc` runs from the repository without a
container, though only the `fake` backend works without CloakBrowser installed:

```bash
PYTHONPATH=docker DTK_BROWSER_BACKEND=fake DTK_BROWSER_BIND_PORT=19000 \
  DTK_BROWSER_PROFILE_ROOT=/tmp/dtk-browser DTK_BROWSER_GEO_PROBE_URL= \
  uv run python -m browser_rpc
```

That is useful for exercising the RPC surface, not for producing usable identities — the fake
backend produces synthetic cookies no platform accepts, which is exactly why nothing falls back to
it on its own.

### A production install without Docker

What is above is the development path — it points you at the test fixture, and
that fixture keeps its data on tmpfs. To run this on a server you install the
database and Redis yourself. The steps below were run end to end on a clean
Ubuntu 24.04 host.

**1. PostgreSQL and TimescaleDB**

TimescaleDB is not in Ubuntu's repositories, so two of them get added. Without it
the first migration refuses to run and says why — the request log, identity
events and content snapshots are all hypertables.

```bash
sudo apt-get install -y curl ca-certificates gnupg lsb-release

sudo install -d /usr/share/postgresql-common/pgdg
sudo curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list

curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
  | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg
echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/timescaledb.list

sudo apt-get update
sudo apt-get install -y postgresql-17 timescaledb-2-postgresql-17 redis-server
```

**2. Actually load TimescaleDB**

Skip this and `CREATE EXTENSION` fails with an error that does not point here:

```bash
echo "shared_preload_libraries = 'timescaledb'" \
  | sudo tee -a /etc/postgresql/17/main/postgresql.conf
sudo systemctl restart postgresql
```

**3. Database, user, extension**

```bash
PGPASS=$(openssl rand -hex 24)
sudo -u postgres psql -c "CREATE USER dtk WITH PASSWORD '${PGPASS}';"
sudo -u postgres createdb -O dtk dtk
sudo -u postgres psql -d dtk -c 'CREATE EXTENSION IF NOT EXISTS timescaledb;'

# Confirm the extension is really there before going further
sudo -u postgres psql -d dtk -tAc \
  "SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';"
```

**4. The code and its Python dependencies**

Run it as a dedicated non-root account — the first thing a bare-metal install has
to put back, because the container did it for you.

```bash
sudo useradd --system --create-home --home-dir /opt/dtk --shell /usr/sbin/nologin dtk
sudo -u dtk git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git /opt/dtk/app
cd /opt/dtk/app

curl -LsSf https://astral.sh/uv/install.sh | sudo -u dtk sh
sudo -u dtk /opt/dtk/.local/bin/uv sync --frozen --no-dev
```

`--frozen` installs exactly what `uv.lock` pins rather than re-resolving; `--no-dev`
skips the test and lint toolchain.

**5. Build the console**

The API serves the console, so skipping this leaves you with a working API and a
404 where the front page should be.

```bash
sudo apt-get install -y nodejs npm     # Node 22
cd /opt/dtk/app/web && sudo -u dtk npm ci && sudo -u dtk npm run build
```

From a source checkout the API finds `web/dist` with no configuration. Point
`DTK_CONSOLE_DIR` at it if your layout differs.

**6. Configuration**

```bash
sudo -u dtk tee /opt/dtk/app/.env >/dev/null <<EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
DTK_DATABASE_URL=postgresql+asyncpg://dtk:${PGPASS}@127.0.0.1:5432/dtk
DTK_REDIS_URL=redis://127.0.0.1:6379/0
DTK_BIND_HOST=127.0.0.1
DTK_BIND_PORT=8000
DTK_BACKUP_DIR=/opt/dtk/backups
EOF
sudo chmod 600 /opt/dtk/app/.env
sudo -u dtk mkdir -p /opt/dtk/backups
```

Redis has no password by default. That is defensible while it only listens on
loopback, but you are already writing credentials, so set one anyway: add
`requirepass` to `/etc/redis/redis.conf` and make `DTK_REDIS_URL`
`redis://:<password>@127.0.0.1:6379/0`.

**7. Migrate, and create the first administrator**

```bash
cd /opt/dtk/app
sudo -u dtk /opt/dtk/.local/bin/uv run dtk migrate

# Password over stdin rather than in argv: argv is visible to every process on the box
printf '%s' 'your-password-here' \
  | sudo -u dtk /opt/dtk/.local/bin/uv run dtk user create admin --role admin --stdin
```

**8. Two systemd services**

`api` and `worker` are two processes and each needs a unit.

```ini
# /etc/systemd/system/dtk-api.service
[Unit]
Description=DTK API
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
User=dtk
WorkingDirectory=/opt/dtk/app
ExecStart=/opt/dtk/.local/bin/uv run dtk serve
Restart=always
RestartSec=5
# Some of what the container gave you, put back by hand
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/dtk/backups

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/dtk-worker.service
[Unit]
Description=DTK worker
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
User=dtk
WorkingDirectory=/opt/dtk/app
ExecStart=/opt/dtk/.local/bin/uv run dtk worker
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dtk-api dtk-worker
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/healthz    # expect 200
```

**What you gave up, and how to get it back**

The container gave you a read-only root filesystem, dropped capabilities, memory
and PID ceilings, and an internal network on which Postgres and Redis are simply
not reachable from outside. Those are properties of the compose file, not of the
code. The units above restore part of it through `ProtectSystem` and
`NoNewPrivileges`; the rest comes from leaving Postgres and Redis on `127.0.0.1`
(the default — do not change it) and leaving `DTK_BIND_HOST` alone. The full list
is in [Security](./15-security.md).

**The two optional components**

`browser-rpc` (automatic identity minting) and `downloader` (media on disk) are
**two more processes** on bare metal, each with its own dependencies: a separate
Python environment plus CloakBrowser and Chromium for the first, a Go toolchain
for the second. Both are optional — without the browser you import cookies by
hand, and without the downloader `POST /api/v1/downloads` answers `501` and says
why.

Mixing is entirely fine if you want one of them without installing it by hand:
run the application on bare metal and start those two sidecars with
`docker compose --profile browser --profile downloader`, then point
`DTK_BROWSER_RPC_URL` and `DTK_DOWNLOADER_URL` at them. They speak HTTP and do
not care where the other side runs.


## Running the worker on its own

The worker is the same image as the API with a different first argument, and the same code as
`uv run dtk worker`. It needs three things and nothing else: `DTK_SECRET_KEY`, `DTK_DATABASE_URL`
and `DTK_REDIS_URL`. It needs no inbound port and no console build.

Inside the compose stack, run one by hand during an incident:

```bash
docker compose -p dtk -f docker/compose.yml run --rm worker
```

On a second machine, the constraint is not the worker — it is that `postgres` and `redis` are on an
`internal: true` network precisely so nothing off the host can reach them. Moving a worker elsewhere
means exposing both, with TLS or a private network of your own, and the compose file will not do it
for you. Before doing that, re-read the previous section: extra workers rarely help, because the
identity pool is the ceiling.

If the image cannot find the worker module it exits with `no worker entry point in this image` and
tells you to set `DTK_WORKER_COMMAND` to the command line your deployment uses. That escape hatch
exists for forks that moved the module; a stock build does not need it.

## Starting over

```bash
# Stop everything, keep the data
docker compose -p dtk -f docker/compose.yml down --remove-orphans

# Stop everything and delete the volumes: database, queue, backups, media
docker compose -p dtk -f docker/compose.yml down -v --remove-orphans
```

`down -v` is irreversible and takes the backup volume with it. Copy any archive you want to keep off
the volume first (see "Upgrading"). Add the profile flags if you started the optional services, or
their containers are left behind as orphans.

The repository's `Makefile` wraps the common ones — `make up`, `make down`, `make logs`,
`make clean` — always with the `dtk` project name, so nothing stray is left behind.

## Where to go next

- [Configuration](./03-configuration.md) — every runtime setting, what it costs, and when to change it
- [Concepts](./04-concepts.md) — identities, the scheduler, signing, and why the pool is the ceiling
- [Identities and proxies](./06-identities-and-proxies.md) — filling the pool, with or without the browser container
- [Users and API keys](./09-users-and-api-keys.md) — after the first administrator
- [Operations](./10-operations.md) — backups, retention, logs, alerts and capacity
- [CLI reference](./13-cli.md) — every `dtk` command this page runs, with its full option list
- [Security](./15-security.md) — what the compose file protects you from, and what it does not
- [Troubleshooting](./14-troubleshooting.md) — when the stack is up and something still refuses to work
