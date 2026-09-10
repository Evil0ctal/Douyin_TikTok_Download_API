<div align="center">
<a href="https://douyin.wtf/" alt="logo"><img src="./logo/logo.svg" width="120" alt="Douyin_TikTok_Download_API"/></a>
</div>
<h1 align="center">Douyin_TikTok_Download_API</h1>

<div align="center">

[English](./README.en.md) | [简体中文](./README.md)

🚀 A self-hosted data API for [Douyin](https://www.douyin.com) and [TikTok](https://www.tiktok.com). One `docker compose up`, an identity pool that maintains itself, and a REST API, MCP server and web console on top.

[![GitHub license](https://img.shields.io/github/license/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](LICENSE)
[![Release Version](https://img.shields.io/github/v/release/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/latest)
[![GitHub Star](https://img.shields.io/github/stars/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/stargazers)
[![GitHub issues](https://img.shields.io/github/issues/Evil0ctal/Douyin_TikTok_Download_API?style=flat-square)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)
<br>
[![CI](https://img.shields.io/github/actions/workflow/status/Evil0ctal/Douyin_TikTok_Download_API/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/actions/workflows/ci.yml)
[![Docker Pulls](https://img.shields.io/docker/pulls/evil0ctal/douyin_tiktok_download_api?style=flat-square&logo=docker&color=2496ed)](https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api)
[![Docker Image Size](https://img.shields.io/docker/image-size/evil0ctal/douyin_tiktok_download_api/latest?style=flat-square&logo=docker&color=2496ed)](https://hub.docker.com/r/evil0ctal/douyin_tiktok_download_api/tags)

</div>

## 💖 Sponsors

These sponsors paid to be here, and **Douyin_TikTok_Download_API** stays free and open because of it. To sponsor the project, see my [GitHub Sponsors page](https://github.com/sponsors/evil0ctal).

<div align="center">
    <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_logo" target="_blank" rel="sponsored noopener">
        <img src="https://tikhub.io/logo.jpeg" width="100" alt="TikHub.io - Global Social Data & API Marketplace">
    </a>
    <h2>
        <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_name" target="_blank" rel="sponsored noopener"><b>TikHub.io</b></a>
    </h2>
    <p>Your Ultimate Social Media Data &amp; API Marketplace</p>
    <p>
        Professional data solutions for Douyin, Xiaohongshu, TikTok, Instagram, YouTube,
        Twitter, and more.<br>
        Real-time Data | Flexible APIs | Seamless Integration | Competitive Pricing with Discounts
    </p>
    <p>
        Buy and sell custom APIs, services, and social media solutions on the<br>
        TikHub.io Marketplace, alongside developers, businesses and content creators.
    </p>
    <p><em>Trusted by leading global influencer marketing and social media intelligence platforms</em></p>
    <p>
        <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_cta" target="_blank" rel="sponsored noopener"><b>→ Visit TikHub.io</b></a>
        &nbsp;·&nbsp;
        <a href="https://api.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme_docs" target="_blank" rel="sponsored noopener">API docs</a>
    </p>
</div>

## 🎬 What it looks like

<div align="center">
    <img src="./screenshots/console-en.gif" width="900" alt="The DTK console: overview, identities, scheduler, playground, library, downloads, API docs and MCP"/>
</div>

One real call: paste a link, send it, get the normalised result back. The identity pool, the
scheduler and the API reference it passed through on the way are all in the same console.
The interface follows the browser's language, and both are written by hand rather than
machine-translated. [中文界面](./screenshots/console-zh.gif)

## 🚀 v4 vs v5

v5 is a rewrite. It started from an empty branch and inherits no v4 code.

v4's real problem was never a shortage of features — it was that **the API would die
quietly and nobody would know**. A cookie expires, a signature algorithm changes, an
endpoint gets rate-limited, and you find out when someone files an issue. v5 puts
"you can see it" and "it heals itself" ahead of features.

| | v4 | v5 |
|---|---|---|
| Where identities come from | You copy cookies out of a browser into `config.yaml` | A headless browser mints guest identities, and the pool tops itself up when usable ones run low |
| How requests go out | Straight out, as they arrive | Health tiers, quantised LRU rotation, one in-flight lock per identity, a token bucket per (identity, endpoint), a circuit breaker per endpoint |
| When something breaks | You wait for a bug report | One structured record per request, live health for every identity and endpoint, visible in the console |
| Call style | Synchronous — send and wait | Asynchronous by default (`202` + `task_id`); add `?wait=` to go back to synchronous |
| What is kept | Nothing; parsed and discarded | PostgreSQL + Redis. Everything parsed is archived, so a post deleted upstream is still here |
| Access control | None; anyone can call it | API keys with scopes and roles, managed in the console |
| Interface | A single PyWebIO page | A React console: identity pool, scheduler, library, downloads, logs, diagnostics |
| Ways in | REST | REST, MCP and a CLI, all over the same service layer |
| Signing | X-Bogus, A_Bogus | a_bogus, X-Bogus, X-Gnarly, X-Dynosaur in pure Python, with a browser fallback |
| Deployment | `pip install -r requirements.txt` + `python start.py` | `docker compose up`, three images |
| Platforms | Douyin, TikTok, Bilibili | Douyin, TikTok |

Bilibili is the one thing that went backwards: v5 does not have it yet. It shares
neither the signing nor the identity machinery with Douyin and TikTok, so the rewrite
left it out for now.

### Still on v4?

v4's code stays on the [`v4` branch](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/tree/v4),
and the image is still there — pull it by version:

```bash
docker pull evil0ctal/douyin_tiktok_download_api:V4.1.2
```

`main` is v5 now, and `latest` follows `main`. To stay on v4, pin the version tag
rather than using `latest`.

### Building this together

There are a few group chats around my open-source projects. If you want to work
on this one, or just talk shop, add me on WeChat at **`Evil0ctal`** with the note
**github 交流** and I will add you.

The groups are for learning from each other. **No advertising and nothing
illegal** — they are for making friends and talking about the work.


## 📦 What it can fetch

| Capability | Douyin | TikTok |
|---|:---:|:---:|
| One post (video or image album) | ✅ | ✅ |
| Author profile | ✅ | ✅ |
| An author's posts | ✅ | ✅ |
| An author's liked posts | ✅ | ✅ |
| Mixes / playlists | ✅ | ✅ |
| Comments | ✅ | ✅ |
| Comment replies | ✅ | ✅ |
| Followers | ❌ | ✅ |
| Following | ❌ | ✅ |

Douyin serves its follower and following lists only to a signed-in session, so those
two endpoints are not registered at all: an endpoint that always returns an empty page
is worth nothing. Importing your own logged-in cookies widens what the rest can see, too.

Media downloads, the content archive, counter snapshots, collections and a watchlist
are built in; none of them needs another service.

## 🔗 What links it accepts

Paste whatever you have — a short link, a post URL, or the whole caption a platform app
puts on your clipboard:

```
https://v.douyin.com/L4NpDJ6/
https://www.douyin.com/video/7126745726494821640
https://www.douyin.com/jingxuan?modal_id=7660875690212492466
https://www.tiktok.com/@evil0ctal/video/7156033831819037994
https://www.tiktok.com/t/ZTR9nkkmL/
2.84 nqe:/ <caption> https://v.douyin.com/L4FJNR3/ <sentence telling you to open the app>
```

Short links are followed and a link buried in a caption is extracted. A post id is also
checked against the platform's own encoding first, so an id that cannot exist is refused
here rather than costing an upstream request.

## ⚗️ Built with

| | |
|---|---|
| Service | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · Typer · structlog |
| Transport | wreq (browser TLS fingerprint emulation) · httpx |
| Data | PostgreSQL + TimescaleDB · Redis |
| Console | React 19 · TypeScript · Vite · TanStack Query · wouter · i18next |
| Signing | a_bogus, X-Bogus, X-Gnarly and X-Dynosaur in pure Python |
| Identity minting | CloakBrowser, headless, in a container of its own, called over HTTP |
| Downloader | Go 1.23, standard library only, statically linked into a scratch image |
| Auth | argon2id password hashing · API keys with scopes |
| Protocols | REST (OpenAPI) · MCP (streamable-http) · CLI |
| Tooling | uv · ruff · mypy · pytest · Docker Compose |

Nothing beyond Postgres and Redis is required. No Kafka, no Elasticsearch, no object
store, no Kubernetes.

CloakBrowser is pinned to a specific commit. That pin is a security control — see
[docker/Dockerfile.browser](./docker/Dockerfile.browser).

## 🗂 Project layout

```
Douyin_TikTok_Download_API/
├── src/dtk/                the service; all of it lives here
│   ├── api/                FastAPI routes, auth, OpenAPI localisation
│   ├── platforms/          Douyin and TikTok adapters: endpoints, params, parsers
│   ├── signing/            a_bogus / X-Bogus / X-Gnarly / X-Dynosaur
│   ├── transport/          outbound requests, response classification
│   ├── identity/           identity minting and health
│   ├── scheduler/          identity selection, token buckets, circuit breakers
│   ├── services/           the business layer, shared by REST, MCP and the CLI
│   ├── worker/             async tasks, callbacks, scheduled collection
│   ├── db/                 SQLAlchemy models and Alembic migrations
│   ├── ops/                diagnostics, backups, health checks, alerting
│   ├── media/              downloader client
│   ├── models/             one content model across both platforms
│   ├── urls/               link recognition, short-link expansion, id validation
│   ├── mcp/                MCP server
│   ├── cli/                the dtk command line
│   ├── i18n/               server-side English and Chinese strings
│   └── core/               settings, logging, error types
├── web/                    the React console, built into the app image
│   └── src/
│       ├── pages/          one file per console page
│       ├── components/     design system and shared components
│       └── locales/        console English and Chinese strings
├── docker/                 three Dockerfiles, compose, and two sidecars
│   ├── browser_rpc/        Python, wrapping CloakBrowser
│   ├── downloader/         Go, the media download sidecar
│   └── compose.yml
├── documents/              user documentation, 17 pages in each language
├── tests/                  unit / integration / contract / replay
├── scripts/                smoke.sh
├── .github/workflows/      CI and Docker image publishing
├── alembic.ini
├── pyproject.toml
└── Makefile
```

## ⚡️ Quick start

You need Docker and Docker Compose. Nothing in the repository ships a default password
or key, so write `.env` first:

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
docker compose -p dtk -f docker/compose.yml logs api   # prints the setup token
```

Open <http://127.0.0.1:8000> and use the token from the log to create the first
administrator.

### Where the image comes from

By default it is built locally: the first `up` compiles the application image from the
Dockerfile in this repository, which takes a few minutes. To skip that, point compose at
the published image instead:

```bash
export DTK_IMAGE=evil0ctal/douyin_tiktok_download_api
export DTK_IMAGE_TAG=latest
docker compose -p dtk -f docker/compose.yml pull
docker compose -p dtk -f docker/compose.yml up -d
```

The `pull` is not optional: these services declare both `image` and `build`, so compose
builds from the local Dockerfile whenever the image is not already on the machine rather
than reaching for a registry.

Images are published for `linux/amd64` and `linux/arm64`, so Apple Silicon and a
Raspberry Pi both run natively. The browser container is not published: it installs
CloakBrowser from a pinned commit, and that pin is a security control that should be
yours to choose, so it stays a local build.

For the browser container, the downloader sidecar, reverse proxies and backups, see
[docker/README.md](./docker/README.md).

## 🖥 What you get

| Entry point | Where | What it is |
|---|---|---|
| Web console | `/` | Identity pool, scheduler, library, downloads, logs, diagnostics |
| API reference | `/docs` | Swagger UI inside the console, English and Chinese |
| Bare reference | `/swagger`, `/redoc` | No login required |
| REST API | `/api/v1/...` | 93 operations |
| MCP | `/mcp` | Shares the service layer with REST; client setup at `/mcp-guide` in the console |
| CLI | `dtk --help` | Same |

The main capabilities:

- **Parse** a link, share text, short link, or a bare post id
- **Archive** everything parsed, so a post deleted upstream is still here
- **Download** media to your own disk, in bulk by author, skipping what you have,
  with duplicate cleanup
- **Watch** an author or a post and re-collect it on a timer
- **iOS Shortcut** support at `/api/v1/ios/shortcut`

## 🔄 Updating

The console keeps an eye out for you: `system.check_updates` is on by default,
and if a newer release exists you get one notice after signing in, at most once
a day. That request goes from **your browser** to GitHub — the server never
sends anything outward, so it does not tell anyone this instance exists. Turn it
off in Settings if you would rather it did not.

Updating is a pull and a restart:

```bash
cd /opt/dtk && git pull

# Running the published images (recommended): point DTK_IMAGE_TAG at the new one
docker compose -p dtk -f docker/compose.yml pull api worker downloader
docker compose -p dtk -f docker/compose.yml run --rm migrate
docker compose -p dtk -f docker/compose.yml up -d
```

Building locally instead? Swap `pull` for `build`. `migrate` runs on every start
and Alembic is idempotent, so running it separately is only about getting the
schema up before the containers switch over.

**Your data stays put.** The named volumes (`postgres-data`, `redis-data`,
`media-data`) survive a rebuild, so the identity pool, the archive, the settings
and the API keys are all where you left them.

The browser image only needs rebuilding when `docker/Dockerfile.browser` or the
CloakBrowser pin changes, which a normal version bump does not touch.

To go back, set `DTK_IMAGE_TAG` to the previous `sha-` or version and `up -d`
again. Migrations have no automatic downgrade — back the database up before
upgrading, which is the only rollback that always works.


## 📖 Documentation

The full documentation lives in [`documents/`](./documents/README.md) — 17 pages, in
English and Chinese.

**New here:**
[Quick start](./documents/en/01-quickstart.md) ·
[Concepts](./documents/en/04-concepts.md) ·
[Console overview](./documents/en/05-console-overview.md)

**Running it:**
[Installation and deployment](./documents/en/02-installation.md) ·
[Configuration reference](./documents/en/03-configuration.md) ·
[Operations](./documents/en/10-operations.md) ·
[Troubleshooting](./documents/en/14-troubleshooting.md) ·
[Security](./documents/en/15-security.md)

**Building against it:**
[REST API guide](./documents/en/11-api.md) ·
[MCP and AI agents](./documents/en/12-mcp.md) ·
[CLI reference](./documents/en/13-cli.md) ·
[Contributing](./documents/en/16-contributing.md)

The endpoint reference is not in there: it is generated from the code that serves the
requests, so your own instance is the copy that is never out of date. Find it at `/docs`
inside the console, or at `/swagger`, `/redoc` and `/openapi.json` without a login. Both
languages.

中文文档：[`documents/README.zh-CN.md`](./documents/README.zh-CN.md)

## 📮 Contact

| | |
|---|---|
| Issues | [GitHub Issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues) — public, keeps its history, and anyone who has hit the same thing can answer |
| Email | `Evil0ctal1985@gmail.com` — reaches one person; best for anything that does not belong in public |
| Author | [@Evil0ctal](https://github.com/Evil0ctal) |

Before asking, read [Troubleshooting](./documents/en/14-troubleshooting.md) and include
the output of the Diagnose page or `dtk diagnose`. It answers most of what a maintainer
would otherwise have to ask you.

## ⭐️ Star history

[![Star History Chart](https://api.star-history.com/svg?repos=Evil0ctal/Douyin_TikTok_Download_API&type=Timeline)](https://star-history.com/#Evil0ctal/Douyin_TikTok_Download_API&Timeline)

> Started 2021/11/06 · GitHub [@Evil0ctal](https://github.com/Evil0ctal)

## 📄 Licence

[Apache License 2.0](./LICENSE).

You may use, modify and distribute this project, **including commercially and inside
closed-source products**. The grant is irrevocable. In return the licence asks you to:

- Keep the copyright notice and the licence text with any copy you distribute
- State what you changed, in files you modified
- Accept that it comes with no warranty

### A request from the author

This project is given away, and it stays free because sponsors pay for it rather than
users. **If you are making money from it, please consider sponsoring instead of only
taking.**

This is a request, not a licence condition — Apache 2.0 permits commercial use, and
nothing above takes that back.

## ☕️ Support the author

The sponsors above pay for the **project**. This section is for the **person who
maintains it**, and is entirely optional.

| Network | Address |
|---|---|
| Solana | `HvtkxmDERbNXfCoojpdFAYN5mSWowjpXgedsG9eF7y9z` |
| Tron (TRC20) | `TQwSM2vjcnrdRU7gY7KNp2tCgMnK33azkT` |
| Ethereum (ERC20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| BNB Smart Chain (BEP20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| Bitcoin | `bc1q785j55cxlnjqe8lkwy8cq57t8t9vn3ak9tlsfy` |

These networks carry the usual major tokens. **USDT on Tron (TRC20) or Solana** is the
easiest to receive, and the cheapest to send.

> **Send only on the network an address is listed under.** A transfer on the wrong chain
> cannot be recovered by anybody.
> Ethereum and BNB Smart Chain share one address on purpose: both are EVM chains, and
> the same key controls it.

[GitHub Sponsors](https://github.com/sponsors/evil0ctal) works too.

### What you are responsible for

This fetches data from platforms that have their own terms, and it runs on your machine
under your control. Respect those terms and applicable law, respect the people whose
content you collect, do not use it to harass anyone, and do not redistribute work that
is not yours. Nobody else can enforce any of that for you.
