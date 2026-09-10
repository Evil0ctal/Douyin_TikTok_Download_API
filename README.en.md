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
[![爱发电](https://img.shields.io/badge/爱发电-evil0ctal-blue.svg?style=flat-square&color=ea4aaa&logo=github-sponsors)](https://afdian.net/@evil0ctal)
[![Kofi](https://img.shields.io/badge/Kofi-evil0ctal-orange.svg?style=flat-square&logo=kofi)](https://ko-fi.com/evil0ctal)
[![Patreon](https://img.shields.io/badge/Patreon-evil0ctal-red.svg?style=flat-square&logo=patreon)](https://www.patreon.com/evil0ctal)

</div>

## Sponsors

These sponsors paid to be here, and **Douyin_TikTok_Download_API** stays free and open because of it. To sponsor the project, see my [GitHub Sponsors page](https://github.com/sponsors/evil0ctal).

<div align="center">
    <a href="https://www.tikhub.io/?utm_source=douyin_tiktok_download_api&amp;utm_medium=referral&amp;utm_campaign=sponsor&amp;utm_content=readme" target="_blank" rel="sponsored noopener">
        <img src="https://tikhub.io/logo.jpeg" width="100" alt="TikHub.io - Global Social Data & API Marketplace">
    </a>
    <div>
        <h2><b>TikHub.io</b></h2>
        <p>Your Ultimate Social Media Data & API Marketplace</p>
        <p>
            Professional data solutions for Douyin, Xiaohongshu, TikTok, Instagram, YouTube, 
            Twitter, and more.<br>
            Real-time Data | Flexible APIs | Seamless Integration | Competitive Pricing with Discounts
        </p>
        <p>
            <b>Discover TikHub.io Marketplace</b><br>
            Buy and sell custom APIs, services, and social media solutions.<br>
            Join a thriving ecosystem of developers, businesses, and content creators.
        </p>
        <p><em>Trusted by leading global influencer marketing and social media intelligence platforms</em></p>
    </div>
</div>


## What v5 is

v5 is a rewrite. It started from an empty branch and inherits no V4 code.

V4's real problem was never a shortage of features - it was that **the API would
die quietly and nobody would know**. A cookie expires, a signature algorithm
changes, an endpoint gets rate-limited, and you find out when someone files an
issue. v5 puts observability and self-healing ahead of features.

It does three things:

1. **Maintains its own identities.** A headless browser mints guest identities
   (cookies, fingerprint, proxy) and the pool tops itself up when usable ones run
   low. No more copying cookies out of a browser into a config file.
2. **Spreads the load.** The scheduler ranks by health, rotates by quantised LRU,
   holds one in-flight lock per identity, meters a token bucket per
   (identity, endpoint), and trips a circuit breaker per endpoint. No single
   identity carries the traffic.
3. **Writes down what happened.** One structured record per request, live health
   for every identity and every endpoint, automatic cooldown and breaker trips on
   risk-control signals - all of it visible in the console.

### Explicitly out of scope

- ❌ AI content analysis, generation or vector search
- ❌ **Anything commercial**: billing, plans, quota sales, subscriptions,
  multi-tenancy. `rate_limit` and `quota` in these docs always mean abuse
  protection, never money
- ❌ Kafka, Elasticsearch, MinIO, k8s. Nothing new gets added that Postgres and
  Redis can already do
- ❌ Proxying video bytes through the server

## Quick start

You need Docker and Docker Compose. Nothing in the repository ships a default
password or key, so write `.env` first:

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

For the browser container, the downloader sidecar, reverse proxies and backups,
see [docker/README.md](./docker/README.md).

## What you get

| Entry point | Where | What it is |
|---|---|---|
| Web console | `/` | Identity pool, scheduler, library, downloads, logs, diagnostics |
| API reference | `/docs` | Swagger UI inside the console, English and Chinese |
| Bare reference | `/swagger`, `/redoc` | No login required |
| REST API | `/api/v1/...` | 88 operations |
| MCP | `/mcp` | Shares the service layer with REST; client setup at `/mcp-guide` in the console |
| CLI | `dtk --help` | Same |

The main capabilities:

- **Parse** a link, share text, short link, or a bare post id
- **Archive** everything parsed, so a post deleted upstream is still here
- **Download** media to your own disk, in bulk by author, skipping what you have,
  with duplicate cleanup
- **Watch** an author or a post and re-collect it on a timer
- **iOS Shortcut** support at `/api/v1/ios/shortcut`

## Documentation

The full documentation lives in [`documents/`](./documents/README.md) — 17 pages,
in English and Chinese.

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

The endpoint reference is not in there: it is generated from the code that serves
the requests, so your own instance is the copy that is never out of date. Find it
at `/docs` inside the console, or at `/swagger`, `/redoc` and `/openapi.json`
without a login. Both languages.

中文文档：[`documents/README.zh-CN.md`](./documents/README.zh-CN.md)
## Licence

[Apache License 2.0](./LICENSE).

You may use, modify and distribute this project, **including commercially and
inside closed-source products**. The grant is irrevocable. In return the licence
asks you to:

- Keep the copyright notice and the licence text with any copy you distribute
- State what you changed, in files you modified
- Accept that it comes with no warranty

### A request from the author

This project is given away, and it stays free because sponsors pay for it rather
than users. **If you are making money from it, please consider sponsoring instead
of only taking.**

This is a request, not a licence condition - Apache 2.0 permits commercial use,
and nothing above takes that back.

## Support the author

The sponsors above pay for the **project**. This section is for the **person who
maintains it**, and is entirely optional.

| Network | Address |
|---|---|
| Solana | `HvtkxmDERbNXfCoojpdFAYN5mSWowjpXgedsG9eF7y9z` |
| Tron (TRC20) | `TQwSM2vjcnrdRU7gY7KNp2tCgMnK33azkT` |
| Ethereum (ERC20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| BNB Smart Chain (BEP20) | `0x2f210FdfD981B59eC130370E5b1Aa8A6a06fb5Ad` |
| Bitcoin | `bc1q785j55cxlnjqe8lkwy8cq57t8t9vn3ak9tlsfy` |

These networks carry the usual major tokens. **USDT on Tron (TRC20) or Solana**
is the easiest to receive, and the cheapest to send.

> **Send only on the network an address is listed under.** A transfer on the
> wrong chain cannot be recovered by anybody.
> Ethereum and BNB Smart Chain share one address on purpose: both are EVM
> chains, and the same key controls it.

[GitHub Sponsors](https://github.com/sponsors/evil0ctal) and
[Afdian](https://afdian.net/@evil0ctal) work too.

### What you are responsible for

This fetches data from platforms that have their own terms, and it runs on your
machine under your control. Respect those terms and applicable law, respect the
people whose content you collect, do not use it to harass anyone, and do not
redistribute work that is not yours. Nobody else can enforce any of that for you.
