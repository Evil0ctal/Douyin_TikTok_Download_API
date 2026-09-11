# MCP and AI agents

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/12-mcp.md)

This page shows you how to point an AI client — Claude Code, Claude Desktop, Codex CLI, Cherry Studio, or anything else that speaks MCP — at your own instance, what the eight tools do, and how to read what comes back. After this you should be able to configure a client, verify the connection with `curl`, and recognise the two mistakes that account for most failed connections.

## What the MCP endpoint is

The MCP server runs **inside the API process**. It is not a wrapper around the REST API: REST, MCP and the CLI are three entry points onto the same service layer, so a tool call goes straight to the scheduler, the identity pool and the response cache without a second HTTP hop and without re-authenticating a caller who is already authenticated.

The practical consequences:

- A tool call spends the identity pool exactly as the equivalent REST call would. It is metered by the same per-key rate limit, it lands in the same request log, and it is subject to the same circuit breaker.
- A cached answer is served from the same cache. If your agent asks for a post that a console user fetched two minutes ago, no upstream request happens.
- Changing a runtime setting (for example `api.mcp_tool_timeout`) reaches the MCP server without a restart, because the tools read the config through a hot-reloaded callable.

Two deliberate limits are worth knowing before you plan an agent around this:

- **Eight tools, and no more.** Every extra tool measurably lowers an agent's ability to pick the right one, so a new capability is expected to arrive as an argument to an existing tool rather than as a ninth. If you want a surface that covers everything this instance can do, use [the REST API](./11-api.md).
- **No tool touches a credential.** There is no identity tool, no cookie tool, no proxy tool, and `pool_status` reports counts and circuit states rather than rows. This is structural — the tools do not exist — not a permission check somebody has to remember to write. An agent connected to this instance cannot read or export your cookies, and cannot be tricked into it.

Everything the tools do is a read. Downloads, the watchlist, settings, identity management and user administration are console and REST territory; see [Downloads, library and watchlist](./08-downloads-and-library.md) and [Users and API keys](./09-users-and-api-keys.md).

## The endpoint address

| Property | Value |
| --- | --- |
| Transport | streamable-http |
| Path | `/mcp/` — mounted on the API process, beside `/api/v1` |
| URL with the default compose binding | `http://127.0.0.1:8000/mcp/` |
| Session mode | stateless: no `Mcp-Session-Id` to keep, no `initialize` handshake required before a call |
| Authentication | an API key, in `Authorization: Bearer …` or `X-API-Key: …` |
| Required `Accept` | `application/json, text/event-stream` |
| Server name / version | `dtk` / the installed package version |
| Tools | 8, all read-only |
| Language of tool text | always English |

**Keep the trailing slash.** `/mcp/` is the endpoint; `/mcp` answers `307 Temporary Redirect` to it. A well-behaved client re-sends the POST body on a 307 and never notices, but a client that follows the redirect with a GET, or that does not follow it at all, fails with something that reads like an authentication problem. Configure the slash and the question never comes up.

The endpoint does not appear in the OpenAPI document, so you will not find it in `/swagger`. The console has a page for it at `/mcp-guide` (nav: **MCP**), which fills in the address from the URL your browser reached it on and shows the same client snippets as this page.

**Tool text is always English.** The console and REST error messages follow `api.default_language` and the caller's `Accept-Language`; MCP does not, because an MCP client never sends a language and the model on the other end reads English. Setting `api.default_language` to `zh` does not change what a tool returns.

## Authentication

The endpoint accepts an API key and nothing else.

- **Header:** either `Authorization: Bearer dtk_…` or `X-API-Key: dtk_…`. Both are checked before the JSON-RPC body is looked at.
- **The console session cookie is refused.** A cookie is presented automatically by a browser; a key is presented deliberately by a program. Accepting the cookie here would make the MCP endpoint reachable by anything able to make a request from inside a signed-in browser.
- **A key needs read access to at least one platform.** The transport requires `douyin:read`, `tiktok:read`, or `admin`; a key with, say, only `archive:read` is refused with `FORBIDDEN_SCOPE` before any tool runs.
- **Rate limiting is the same limiter as REST**, keyed on the API key: `api.default_rate_limit_per_min` (default **120** requests per minute) unless the key carries its own limit. Note that each JSON-RPC call is one HTTP request, so `initialize`, `tools/list` and every `tools/call` each count.
- **Opening endpoints to anonymous callers does not open MCP.** The "endpoint access" feature applies to REST paths; the MCP guard requires a real key regardless.

Failures use the REST envelope, so one error handler covers both surfaces:

```json
{ "success": false, "error": { "code": "UNAUTHENTICATED", "message": "…" } }
```

A 401 also carries `WWW-Authenticate: Bearer realm="dtk"`.

To create a key: console → **Access → API keys**, or `POST /api/v1/admin/api-keys`. Give it `douyin:read`, `tiktok:read`, or both — nothing more. The full key is shown once, at creation, and is never recoverable; if you lose it, revoke it and make another. See [Users and API keys](./09-users-and-api-keys.md).

## The tools

| Tool | What it does | Task endpoint it queues |
| --- | --- | --- |
| `parse_url` | Fetch whatever a link points at | `parse` |
| `get_video` | One post — video or image album — by id | `{platform}.content_detail` |
| `get_user` | One author profile | `{platform}.author_profile` |
| `list_user_posts` | An author's posts, one page at a time | `{platform}.author_posts` |
| `list_comments` | Top-level comments on a post, one page | `{platform}.comments` |
| `get_content_history` | Recorded metric history for a post or author | none — reads the local database |
| `pool_status` | Identity-pool and endpoint health | none — reads Redis and the local database |
| `get_task_result` | Collect a result that did not finish in time | none |

The endpoint names in the third column are the same keys the scheduler, the circuit breaker, the health board and the request log use, so a tool call an agent made is straightforward to find in [Operations](./10-operations.md).

`platform` is `douyin` or `tiktok`. **All ids are strings**, never numbers: Douyin and TikTok ids exceed the JavaScript safe integer range and a client that parses them as numbers will silently corrupt them.

### `parse_url`

| Argument | Required | Meaning |
| --- | --- | --- |
| `url` | yes | A Douyin or TikTok link, or the share text containing one. Short links (`v.douyin.com`, `vm.tiktok.com`) are followed server-side. |

The entry point to use when you have a link rather than an id: it works out the platform and the resource type itself. Share text with a link buried in it is accepted — the first URL in the string is used.

Returns `status`, `task_id`, `platform`, `resource`, `url` (the link after classification) and `data`.

Refused without spending any quota: a link on a host that is not Douyin or TikTok (`INVALID_URL`); a link on a supported host that points at no post or profile — a feed, a search page, a settings screen (`UNSUPPORTED_CONTENT`); and a link to a live room, a mix, a music page, a challenge or a search result, which this tool set has no endpoint for (`UNSUPPORTED_CONTENT`).

### `get_video`

| Argument | Required | Meaning |
| --- | --- | --- |
| `platform` | yes | `douyin` or `tiktok` |
| `content_id` | yes | The post id as a string: Douyin `aweme_id`, TikTok item id |

Returns the normalized post — author, stats, media URLs, tags — under `data`.

### `get_user`

| Argument | Required | Meaning |
| --- | --- | --- |
| `platform` | yes | `douyin` or `tiktok` |
| `uid` | yes | Douyin: the `sec_user_id` (starts with `MS4wLjAB`). TikTok: the `secUid`, or the `@handle` if that is all you have. |

The `uid` in the result is the stable key for that author. A TikTok `@handle` can change, so an agent should carry the `uid` from this result into follow-up calls rather than the handle.

Only TikTok's profile endpoint accepts a handle. On Douyin a handle is refused with a sentence telling the agent to pass a profile link to `parse_url` instead, which resolves it — that is the only path from a Douyin handle to an id.

### `list_user_posts`

| Argument | Required | Meaning |
| --- | --- | --- |
| `platform` | yes | `douyin` or `tiktok` |
| `uid` | yes | The author's stable id. A TikTok `@handle` is **not** accepted here — call `get_user` first. |
| `cursor` | no | The opaque `cursor` from the previous page. Omit for the first page; pass it back unchanged and never parse it. |
| `count` | no | Items per page, **1–50**. Omit to use the platform's own default. |

Returns a page under `data` carrying `cursor` and `has_more`. Stop when `has_more` is false.

`count` is capped at 50 because both platforms silently cap larger values, which would hand the agent fewer items than it asked for with no indication that anything was trimmed. Asking for more per page is not a way to fetch a large account faster — page with the cursor.

### `list_comments`

| Argument | Required | Meaning |
| --- | --- | --- |
| `platform` | yes | `douyin` or `tiktok` |
| `content_id` | yes | The post id whose top-level comments you want |
| `cursor` | no | As above |
| `count` | no | As above, 1–50 |

Top-level comments only. Replies under a comment are a separate capability that this tool set does not expose — that is one of the eight-tool trade-offs. If an agent needs them, fetch them over REST from `/api/v1/{platform}/video/comments/replies`.

### `get_content_history`

| Argument | Required | Meaning |
| --- | --- | --- |
| `platform` | yes | `douyin` or `tiktok` |
| `content_id` | yes | A post id, or an author id for follower history |
| `since` | no | ISO 8601 date or timestamp, e.g. `2026-01-15` or `2026-01-15T00:00:00Z`. Defaults to the last **30 days**. |

Answers "how did this grow" from snapshots already stored locally. It makes **no upstream request** and costs no identity quota, which makes it the cheapest tool in the set — and the only one that returns nothing useful for an id this instance has never fetched. A snapshot is written as a side effect of every successful parse, deduplicated to one row per `snapshot.min_interval_seconds` (default **300 s**); a [watchlist](./08-downloads-and-library.md) entry is how you get a regular curve rather than an accidental one.

Returns:

| Field | Meaning |
| --- | --- |
| `since` | The window start actually used, as ISO 8601 |
| `point_count` | Number of points returned |
| `truncated` | True when the ceiling of **500** points was hit; the most recent points are kept, so widen `since` only if you need the earlier part of the curve |
| `points` | Oldest first. Each has `ts`, `play_count`, `digg_count`, `comment_count`, `share_count`, `collect_count`, `follower_count` |

A metric the platform did not report is `null`, never `0`. A null is missing data; a zero is a real measurement — do not let an agent average them together.

### `pool_status`

No arguments. Call it when a request failed or timed out and you need to decide whether to wait, to try the other platform, or to stop.

Returns:

| Field | Meaning |
| --- | --- |
| `observed_at` | When the snapshot was taken |
| `identities` | Per platform, a count per state: `minting`, `active`, `cooling`, `degraded`, `retired` |
| `usable_identities` | Total `active` identities across platforms |
| `endpoints` | One entry per known endpoint (see below) |
| `summary` | One English sentence describing the pool and any tripped endpoints |

Each endpoint entry carries `endpoint`, `state` (`closed` or `tripped`), `retry_after_seconds`, `reason`, `requests_recent`, `ok_recent`, `risk_control_recent` and `last_success_at`. The three `_recent` counters are over the circuit breaker's rolling **300-second** window; `last_success_at` is looked up over the last **30 days** — bounded in practice by `retention.request_log_days` (**14** by default), because the lookup reads `request_log` — and is `null` if nothing succeeded inside that window.

No credential, identity id, cookie, fingerprint or proxy address appears anywhere in this result.

### `get_task_result`

| Argument | Required | Meaning |
| --- | --- | --- |
| `task_id` | yes | The task id from a tool call that reported `status: "pending"` |

There is no other reason to call it. Every other tool already waits for its own answer, so an agent that starts here is polling for work nobody queued.

## Reading a result

Each tool returns a JSON object. On success:

```json
{
  "status": "ok",
  "task_id": "3f6c…",
  "platform": "douyin",
  "content_id": "7100000000000000000",
  "cached": false,
  "data": { }
}
```

- `data` is the model itself — the same normalized shape REST returns, unwrapped from the task envelope so the agent does not have to dig a level deeper than the schema describes.
- `cached` appears when the task recorded it, and says whether this answer cost a real upstream request. Two calls a minute apart can legitimately return identical data with `cached: true` on the second. Default cache TTLs are 1800 s for a post, 900 s for a profile and 300 s for a list; see [Configuration](./03-configuration.md).
- `task_id` is there so the call can be correlated with the request log, and so a `pending` answer can be collected later.

Paging tools put `cursor` and `has_more` inside `data`.

## When a call does not finish in time

REST is asynchronous-first: you submit, you get a task id, you collect. That model is wrong for an agent — handing back a task id and asking it to poll burns its context, and many agents simply give up — so **every MCP tool blocks and waits for its own answer**. The work still travels the same asynchronous path; the waiting just happens server-side.

The wait is bounded by `api.mcp_tool_timeout`, default **60 seconds** (`dtk config set api.mcp_tool_timeout 90`, or the console Settings page; it is a runtime setting and takes effect without a restart). Only when that bound is hit does the agent hear about a task at all:

```json
{
  "status": "pending",
  "task_id": "3f6c…",
  "message": "The request for douyin.author_posts is still running after 60 seconds, so this tool call returned before it finished. Nothing was lost: the work continues in the background as task 3f6c…. Call get_task_result with task_id=3f6c… in a little while to collect it. Identity pool - douyin: 2 active, 1 cooling; tiktok: 3 active. No endpoint is tripped."
}
```

Three things follow from this, and they are worth putting in your agent's system prompt if it is not already reading the server instructions:

1. **Do not repeat the original call after a `pending`.** That queues the work a second time and spends the pool twice. Call `get_task_result` with the id.
2. **Set the client's HTTP timeout above `api.mcp_tool_timeout`.** A client that gives up at 30 seconds will report a network failure for a request the server would have answered.
3. **A result does not live forever.** Task rows outlive their payloads by `retention.task_result_hours` (default **24 hours**). Collect a pending result within that window; afterwards `get_task_result` says, in as many words, that the payload is past retention and that the fix is to call the original tool again rather than to wait.

`get_task_result` on a task that is still running returns `status: "pending"` again with the task's state and the pool summary — it never blocks.

The server also ships a short instruction block that every client shows the model before its first call. It says where to start, that ids are strings, what a `pending` means, and that there is no tool for cookies, proxies or identities.

## Errors an agent will see

A failed tool call comes back as an MCP tool error (`isError: true`) whose text is prose, never a traceback. Every message says the same three things in the same order: what happened, what state the system is in, and whether retrying can help — then the error code in parentheses, so a prompt can match on it.

```
That post does not exist. This was the douyin.content_detail endpoint.
Retrying will not help; the request has to change. (error code NOT_FOUND)
```

When the failure is one the endpoint's health explains, the message carries that too:

```
… Endpoint douyin.author_posts is currently tripped; last success was 2 hours ago.
Reason: Risk control hit 60% of the last 25 requests, across 3 identities. It reopens
in about 240 seconds. This is retryable: back off for about 240 seconds, then try
again. (error code ENDPOINT_CIRCUIT_OPEN)
```

The retryability sentence is generated from the same table the REST layer uses, so the two can never disagree.

| Code | What it means for an agent | Retry? |
| --- | --- | --- |
| `INVALID_URL` | Not a supported Douyin or TikTok link | No — change the request |
| `UNSUPPORTED_CONTENT` | Recognised, but not a post or profile | No |
| `INVALID_PARAM` | Bad platform, blank id, `count` out of range, unparsable `since`, or a handle where a stable id is required | No |
| `FORBIDDEN_SCOPE` | This key was not granted that platform | No |
| `NOT_FOUND` | The post or author does not exist | No |
| `CONTENT_PRIVATE` | It exists but is not visible to a guest identity | No |
| `UPSTREAM_CHANGED` | A parser bug in dtk, not something a different request works around | No — report it |
| `ENDPOINT_CIRCUIT_OPEN` | The breaker tripped on this endpoint | Yes, after the stated delay |
| `IDENTITY_POOL_EXHAUSTED` | No usable identity right now | Yes — the pool refills itself |
| `UPSTREAM_RISK_CONTROL` | The platform pushed back on this request | Yes, after a backoff |
| `RATE_LIMITED` | The key's own limit | Yes, after the stated delay |
| `TASK_NOT_FOUND` | Unknown task id, or a result past retention | See the message — a past-retention result says plainly that waiting will not help |
| `INTERNAL` | The request failed inside dtk | Yes, but check the logs |

For the five codes where health is part of the explanation — circuit open, pool exhausted, risk control, signing failure and internal — the message also carries the endpoint's circuit state and its last success time. Exception text is logged, never sent: it can name a host, a query or a header, and none of that belongs in a model's context.

## Scopes as they apply to an agent

The transport can only check that the key reads *something*, because the platform is inside the JSON-RPC body rather than in the headers. So the check happens twice: once at the door, and again inside each tool for the platform actually asked for.

| Scope | Effect on MCP |
| --- | --- |
| `douyin:read` | Opens the endpoint; permits Douyin tool calls |
| `tiktok:read` | Opens the endpoint; permits TikTok tool calls |
| `admin` | Permits everything, including both platforms |
| anything else alone | Endpoint refused with `FORBIDDEN_SCOPE` |

Two rules that surprise people:

- **A key is bounded by the scopes it was minted with even when an administrator owns it.** On a self-hosted instance almost every key belongs to the admin user; a key with only `douyin:read` still cannot read TikTok through MCP, exactly as it cannot through `GET /api/v1/tiktok/video`.
- **A task id is not a way around the check.** `get_task_result` re-checks the platform of the task it is collecting, so a key scoped to one platform cannot read the other's data by quoting an id it saw somewhere. (A task queued by `parse_url` has no platform in its endpoint name; there the check happened when the link was classified, before anything was queued.)

Over stdio there is no transport identity to restrict: the agent spawned the process and already has everything the process has, including the database. That is a reason to keep stdio for local use, not a gap in the HTTP transport.

## Client configuration

Every snippet below uses `http://127.0.0.1:8000/mcp/`, the address the default compose deployment publishes. Replace it with the address the client can actually reach — see **Common mistakes** below — and replace `dtk_YOUR_API_KEY` with a real key.

### Claude Code

Run once; it writes the server into your Claude Code config. Add `--scope project` to commit it to the repository instead.

```bash
claude mcp add --transport http dtk http://127.0.0.1:8000/mcp/ \
  --header "Authorization: Bearer dtk_YOUR_API_KEY"
```

### Claude Desktop

`claude_desktop_config.json` validates stdio servers only, and Claude Desktop's remote-connector screen authenticates with OAuth, which this server does not speak. So the connection goes through the `mcp-remote` bridge — a stdio process that forwards to a streamable-http endpoint and can carry a static header. It needs Node installed. Open **Settings → Developer → Edit Config**, merge this in, and restart the app.

```json
{
  "mcpServers": {
    "dtk": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp/", "--header", "X-API-Key:${DTK_API_KEY}"],
      "env": { "DTK_API_KEY": "dtk_YOUR_API_KEY" }
    }
  }
}
```

The key travels in `env` and the header value contains no space, both on purpose: `mcp-remote` documents that Claude Desktop on Windows (and Cursor, and Codex CLI) fail to escape spaces inside `args`, which mangles `"Authorization: Bearer …"`. `X-API-Key:${DTK_API_KEY}` has nowhere for that bug to bite, and this server accepts that header as readily as the bearer form.

### Codex CLI

Append to `~/.codex/config.toml`, and `export DTK_API_KEY=dtk_YOUR_API_KEY` in the shell you start Codex from. The key is read from the environment, so the config file itself stays safe to commit.

```toml
[mcp_servers.dtk]
url = "http://127.0.0.1:8000/mcp/"
bearer_token_env_var = "DTK_API_KEY"
```

### Cherry Studio

**Settings → MCP Servers → Add Server → Import from JSON**. Cherry Studio calls this transport `streamableHttp`.

```json
{
  "mcpServers": {
    "dtk": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer dtk_YOUR_API_KEY" }
    }
  }
}
```

### Any other streamable-http client

Anything that speaks streamable-http works. The request shape it has to make:

```http
POST http://127.0.0.1:8000/mcp/
Authorization: Bearer dtk_YOUR_API_KEY
Content-Type: application/json
Accept: application/json, text/event-stream
```

Both media types must be in `Accept`. The transport runs stateless, so there is no session id to carry between requests.

### stdio, for a process on the same machine

For an agent that spawns the server itself and talks JSON-RPC over the pipe. There is no HTTP request, so there is no API key and no scope restriction — the caller is the process owner.

```bash
# Runs on the same machine as the database, and reads the same
# environment the API process does. No API key: there is no HTTP
# request to authenticate.
DTK_DATABASE_URL=... DTK_REDIS_URL=... DTK_SECRET_KEY=... \
  python -m dtk.mcp
```

Configuration comes from the environment exactly as it does for the API process, so the two never disagree about which database they are looking at. Logging is redirected to stderr before anything logs, because on stdio stdout *is* the JSON-RPC channel and one stray log line disconnects the client.

The catch is reachability: in the default Docker deployment Postgres and Redis are on an internal network and publish no ports, so a `python -m dtk.mcp` started on the host cannot reach them. stdio is for a source checkout, or for a process that genuinely has access to both stores. If you are running the compose stack, use the HTTP transport — it is the same tools with the same behaviour.

## Checking it works

From the machine the client will run on:

```bash
curl -sS -X POST http://127.0.0.1:8000/mcp/ \
  -H "Authorization: Bearer dtk_YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

The transport streams its answer, so what comes back is a server-sent-events frame — a `event: message` line followed by `data:` carrying the JSON-RPC payload — rather than a bare JSON body. A successful `tools/list` names all eight tools. Because the server is stateless, `tools/list` and `tools/call` work without an `initialize` handshake first, which makes this a one-line health check.

What the failures look like:

- `401` with `"code": "UNAUTHENTICATED"` — no key header, or the key is wrong, revoked or expired.
- `403` with `"code": "FORBIDDEN_SCOPE"` — the key is valid but has no platform read scope.
- `307` — you left the trailing slash off.
- `200` with the console's HTML — you are talking to something that is not this API, or through a proxy that is not passing `/mcp/` through.

A `tools/call` that reaches a platform is a real request against the pool. To exercise the path end to end without spending an identity, call `pool_status`:

```bash
curl -sS -X POST http://127.0.0.1:8000/mcp/ \
  -H "Authorization: Bearer dtk_YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pool_status","arguments":{}}}'
```

## Common mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Client reports a connection or auth error, server logs show nothing | The trailing slash is missing; the client did not re-POST on the 307 | Configure `/mcp/`, with the slash |
| Works on the server, fails from a laptop | `127.0.0.1` in the client config. The default compose binding publishes the API on loopback only | Give the client an address it can reach, and put TLS in front of the instance before you do — see [Security](./15-security.md) |
| `401 UNAUTHENTICATED` although you are signed in to the console | The session cookie is not accepted here | Create an API key and use a header |
| `403 FORBIDDEN_SCOPE` at connection time | The key has no `douyin:read`, `tiktok:read` or `admin` | Mint a key with a platform read scope |
| `FORBIDDEN_SCOPE` on some calls only | The key holds one platform's scope and the agent asked for the other | Grant the second scope, or constrain the agent |
| Client times out around 30 s while the server keeps working | The client's HTTP timeout is below `api.mcp_tool_timeout` | Raise the client timeout, or lower `api.mcp_tool_timeout` |
| Agent loops on the same failing call | It is ignoring the "retrying will not help" sentence | Tell it in the system prompt to read the error text before retrying |
| Agent re-runs a call that returned `pending` | It treated `pending` as a failure | Point it at `get_task_result` with the task id |
| Ids come back truncated or wrong | The client parsed an id as a number | Keep ids as strings everywhere |
| `get_content_history` returns nothing | This instance has never fetched that id | Fetch it once, or add it to the [watchlist](./08-downloads-and-library.md) |

If a tool is failing rather than misconfigured, `pool_status` is the first thing to look at, and [Troubleshooting](./14-troubleshooting.md) covers the rest.

## Where to go next

- [REST API guide](./11-api.md) — the full surface, including everything the eight tools deliberately leave out.
- [Users and API keys](./09-users-and-api-keys.md) — creating, scoping, rate-limiting and revoking the key an agent uses.
- [Concepts](./04-concepts.md) — the identity pool, the scheduler and the circuit breaker that `pool_status` reports on.
- [Operations](./10-operations.md) — finding an agent's calls in the request log and reading the endpoint health board.
- [Security](./15-security.md) — what to do before you expose this instance to anything beyond localhost.
