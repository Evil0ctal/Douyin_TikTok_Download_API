# Documentation

Everything you need to run, use, operate and contribute to a self-hosted
**Douyin_TikTok_Download_API v5** instance.

**中文文档：[README.zh-CN.md](./README.zh-CN.md)** — every page here exists in both
languages and they say the same thing.

---

## Start here

If you have never run this before, read these three in order. They take about
half an hour and leave you with a working instance you understand.

| | Page | What you get |
|---|---|---|
| 1 | [Quick start](./en/01-quickstart.md) | The stack running on one host, an administrator account, and one link parsed — from the console and from `curl` |
| 2 | [Concepts](./en/04-concepts.md) | The mental model: identities, signing, scheduling, tasks. Enough to predict what a request will do before you send it |
| 3 | [Console overview](./en/05-console-overview.md) | Finding your way around, and the four pages that tell you whether the instance is healthy |

## By what you are trying to do

**Deploy and configure it**

- [Installation and deployment](./en/02-installation.md) — the compose file service by service, every `DTK_*` variable, profiles, reverse proxies, upgrading, running without Docker
- [Configuration reference](./en/03-configuration.md) — the two configuration layers, and all 54 runtime settings with defaults and when to change them
- [Security](./en/15-security.md) — what the software protects, what you are responsible for, and which operations hand out a credential

**Use the console**

- [Console overview](./en/05-console-overview.md) — the shell, Overview, System, Logs, Diagnose
- [Identities and proxies](./en/06-identities-and-proxies.md) — the pool, its state machine, minting, importing cookies, proxies, the Scheduler and Endpoint access pages, and the circuit breakers
- [Playground and tools](./en/07-playground-and-tools.md) — driving any endpoint from the browser, signing, decoding a signature, parsing links, minting on demand
- [Downloads, library and watchlist](./en/08-downloads-and-library.md) — putting media on your own disk, finding it again, and collecting a target on a timer
- [Users and API keys](./en/09-users-and-api-keys.md) — roles, scopes, keys, and who can do what

Every entry in the sidebar is covered by one of these pages, and
[Console overview](./en/05-console-overview.md#which-document-covers-which-page)
carries the table that says which: Scheduler (`/scheduler`) and Endpoint
access (`/endpoint-access`) are in Identities and proxies, and Backup
(`/backup`), Notifications (`/notifications`) and Settings (`/settings`) are
in Operations.

**Build against it**

- [REST API guide](./en/11-api.md) — authentication, the uniform envelope, the asynchronous model and `?wait=`, paging, errors, worked clients in three languages
- [MCP and AI agents](./en/12-mcp.md) — pointing Claude Code, Claude Desktop, Codex or Cherry Studio at your instance
- [CLI reference](./en/13-cli.md) — every `dtk` command and option

**Keep it running**

- [Operations](./en/10-operations.md) — the Settings, Backup and Notifications pages: backups and restore drills, alert channels, retention, monitoring, capacity, safe upgrades
- [Troubleshooting](./en/14-troubleshooting.md) — symptom, cause, fix, plus the complete error-code table
- [FAQ and glossary](./en/17-faq.md) — the questions people ask before deploying, and every term this documentation uses

**Change the code**

- [Contributing](./en/16-contributing.md) — development environment, repository layout, the test layers, the quality gates a change has to pass

## The full set

| | Page | |
|---|---|---|
| 01 | [Quick start](./en/01-quickstart.md) | From nothing to a first successful request |
| 02 | [Installation and deployment](./en/02-installation.md) | Compose, environment, profiles, upgrades |
| 03 | [Configuration reference](./en/03-configuration.md) | All 54 runtime settings |
| 04 | [Concepts](./en/04-concepts.md) | The mental model |
| 05 | [Console overview](./en/05-console-overview.md) | Shell, Overview, System, Logs, Diagnose |
| 06 | [Identities and proxies](./en/06-identities-and-proxies.md) | The pool and the scheduler |
| 07 | [Playground and tools](./en/07-playground-and-tools.md) | Playground, Tools, API docs |
| 08 | [Downloads, library and watchlist](./en/08-downloads-and-library.md) | Keeping content |
| 09 | [Users and API keys](./en/09-users-and-api-keys.md) | Access control |
| 10 | [Operations](./en/10-operations.md) | Day two and after |
| 11 | [REST API guide](./en/11-api.md) | Calling the HTTP API |
| 12 | [MCP and AI agents](./en/12-mcp.md) | AI clients |
| 13 | [CLI reference](./en/13-cli.md) | The `dtk` command |
| 14 | [Troubleshooting](./en/14-troubleshooting.md) | When something is wrong |
| 15 | [Security](./en/15-security.md) | Threat model and responsibilities |
| 16 | [Contributing](./en/16-contributing.md) | Working on the code |
| 17 | [FAQ and glossary](./en/17-faq.md) | Questions and terms |

## What is not here

**The endpoint reference.** Every endpoint, parameter, constraint and response
shape is generated from the code that serves the requests, so it is never out of
date and there is no copy of it to go stale. Your own instance serves it at
`/docs` inside the console, and at `/swagger`, `/redoc` and `/openapi.json` —
none of them needs a login. Both languages: append `?lang=zh`.

The [REST API guide](./en/11-api.md) is the companion to that reference — it
explains the things a list of endpoints cannot, like what a `202` means and when
retrying can help.

## A note on accuracy

Every command, setting name, default value, endpoint path and error code on
these pages was checked against the source it describes. If you find one that is
wrong, that is a bug worth reporting — please
[open an issue](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)
and say which page and which line.

## Getting help

- [Issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues) — public, keeps its history, and anyone who has hit the same thing can answer
- Email `Evil0ctal1985@gmail.com` — reaches one person; best for anything that does not belong in public

Read [Troubleshooting](./en/14-troubleshooting.md) first, and include the output
of the Diagnose page or `dtk diagnose` with whatever you report. It answers most
of the questions a maintainer would otherwise have to ask you. If the stack will
not start there is no api container to run the self-check in and neither surface
can answer — say so, and send
`docker compose -p dtk -f docker/compose.yml logs` instead.
