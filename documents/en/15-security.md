# Security

This page is the division of labour between you and the software. After reading
it you will know what the instance already protects on its own, what it
deliberately refuses to do for you, which calls hand back a live credential, and
what to check before anything but your own laptop can reach the port.

## The threat model this was built for

Every default below follows from one assumption: **one operator, one instance, a
handful of accounts, no tenants.** You own the machine, the database, the
identity pool and the data. Nobody is renting capacity from you.

That assumption is why there are three roles instead of a permission matrix, why
there is no password expiry, and why the identity pool has no per-row owner. It
is also why the software is blunt about the things it cannot decide for you:
whether the port should be public, whose data you are collecting, and whether the
platform's terms allow what you are about to do.

### What is worth taking, and what stands in front of it

| Asset | Why someone wants it | What protects it |
| --- | --- | --- |
| Cookie jars in the identity pool | A working platform session. A logged-in one is somebody's account; a guest one cost a headless browser run to mint | AES-256-GCM at rest, keyed by `DTK_SECRET_KEY`; never returned by any ordinary endpoint; wiped on retirement |
| Proxy URLs | Paid egress, with credentials in the URL | Encrypted at rest; every rendering goes through a mask; plaintext exists only for the duration of one dial |
| The API surface | Free scraping capacity on your identities, your proxies and your IP reputation | Every endpoint requires a credential by default, bar the short list of routes that never had a lock; per-key rate limit; scopes |
| The archive | Months of collected posts, about real people | `archive:read` and `archive:export` are separate scopes from the platform reads |
| The instance's network position | The service sits inside a network its callers cannot otherwise reach — cloud metadata, a LAN gateway, a database on `10.x` | A positive host allowlist on every fetchable URL; `security.request_proxy` defaults to refusing; webhooks off by default |
| Console accounts | Everything above | argon2id, server-side sessions in Redis, per-account lockout |

### What it does not defend against, and says so

- **A shell on the host or in a container.** `DTK_SECRET_KEY` is in the process
  environment, and the CLI talks straight to the database with no role check and
  no audit row. Shell access is total access, by design — it is also the
  documented recovery path when nobody can log in.
- **The database plus the master key.** Encryption at rest protects a stolen
  dump, a stray backup, a disk that leaves the building. It protects nothing
  from someone who has both halves.
- **A malicious administrator.** Three roles in a ladder cannot contain the top
  of the ladder. An administrator can read every jar, export the pool, restore a
  backup and change any setting. The audit log records it; it does not prevent
  it.
- **A caller with a valid key doing legitimate-looking damage.** Rate limits are
  abuse protection, not a quota system — this project has no billing anywhere in
  it.
- **The platforms noticing.** Your identities are guests you minted or sessions
  you imported. Nothing here makes an account safe from the platform whose terms
  it is used under.
- **Multi-tenancy.** The identity pool is instance-wide and its rows have no
  owner. Anyone who can name an identity can send a request as it. That is why
  naming one is gated like pool management rather than like reading.

## The master key

`DTK_SECRET_KEY` is the one secret that cannot live in the database, because it
is what decrypts the database.

```bash
openssl rand -base64 48
```

| Property | Value |
| --- | --- |
| Minimum length | 32 characters, checked twice: by the container entrypoint and by `dtk.core.crypto.derive_key` |
| Key derivation | SHA-256 of the string, giving a 32-byte AES key |
| Cipher | AES-256-GCM, 12-byte random nonce prepended to each ciphertext |
| Additional authenticated data | The row's own id, so a blob encrypted for identity A cannot be moved onto identity B |
| Default shipped in the image | None. There is no fallback |

The entrypoint refuses to start any of the three roles — `api`, `worker`,
`migrate` — without it, exiting `78` (`EX_CONFIG`) with the command that
generates one. `migrate` is gated too: a schema created for a deployment that
cannot decrypt its own credentials is worse than a failed migration, and this
way the failure arrives before any table exists.

An image that shipped a default key would be an image with no encryption at all,
which is the whole reason there is no default to fall back on.

### If you lose it

Everything encrypted under it is gone. There is no recovery path and no escrow.

- Identities cannot be decrypted, so they cannot sign anything. The masked
  cookie view reports `readable: false`; the reveal endpoint answers *"this
  identity's cookies cannot be decrypted with the current secret key"*.
- Proxy rows cannot be dialled. They have to be re-entered.
- Backups written under the old key refuse to restore: the manifest carries an
  HMAC fingerprint of the derived key, and a mismatch is reported as a mismatch
  rather than quietly producing rows nothing can read.

The way back is to retire the affected identities and mint or import them again,
and to re-enter the proxies. The archive, the settings, the users and the API
keys are unaffected — none of them is encrypted with this key.

### If you rotate it

There is no rotation tool, and rotating is exactly the same event as losing it:
old ciphertext stays encrypted under the old key and nothing re-encrypts it.
Plan a rotation as *retire the pool, change the key, rebuild the pool*, and take
a backup with the **old** key first if you might want to go back.

Keep the key with the same care as a private key: in `.env` at the repository
root, which is never committed, and out of screenshots, issues and chat.

## What is encrypted, and what is only masked

The distinction matters, because "masked" protects a screenshot and nothing else.

| Data | At rest | In responses |
| --- | --- | --- |
| `identities.cookies_encrypted` | AES-256-GCM, AAD = identity id | Never, except the three audited calls below |
| `proxies.url_encrypted` | AES-256-GCM, AAD = proxy id | Masked: scheme, host, port, and `user:***@` when there are credentials |
| `identities.fingerprint` | Plain JSONB | User-Agent, language and timezone are returned; it identifies a browser profile, not a session |
| `users.password_hash` | argon2id digest | Never returned by any endpoint, never logged |
| `api_keys.key_hash` | SHA-256 of the full key | Never. Only the prefix is stored for display |
| Runtime settings that hold credentials (`notify.channels`, `security.webhook_secret`) | **Plain JSON in the `settings` table** | Masked on read; a mask sent back means "keep the stored value" |
| `audit_log.detail` | Plain JSONB, masked at the point of writing | Settings rows are masked again on the way out |

Two consequences are worth stating plainly:

- **A bot token in `notify.channels` is not encrypted at rest.** It is masked
  everywhere a human or an API reader can see it, and it is encrypted inside a
  backup archive, but in the table it is JSON. Anyone with the database has it.
- **Retirement wipes a jar immediately.** `cookies_encrypted` is set to empty
  bytes the moment an identity is retired, and the row survives only for its
  statistics; `retention.retired_identity_days` (90 by default) removes the row
  itself later.

## Every other secret this deployment holds

| Secret | Where it lives | Notes |
| --- | --- | --- |
| `DTK_SECRET_KEY` | `.env`, process environment of `api`, `worker` and `migrate` | Blanked out in the `environment:` block of postgres, redis, browser-rpc and downloader — none of them has any use for it |
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | `.env` | Both stores sit on an `internal: true` network with no route off the host |
| `DTK_DOWNLOADER_TOKEN` | `.env`, optional | Shared secret for the downloader sidecar. Defence in depth; empty means the sidecar checks nothing |
| The setup token | Redis only, 24 hour TTL | Printed once in the container log. Never in an HTTP response, never in the database, never on disk. Compared in constant time; five wrong attempts invalidate it; the whole mechanism is permanently closed once an account exists |
| API keys | `dtk_<prefix>_<secret>`, stored as prefix + SHA-256 | Shown exactly once, in the response that creates them. The service cannot show one again, to anybody |
| Console session tokens | Redis, 7 days | Opaque 32-byte tokens in an httpOnly cookie. The console identifies a session by a 16-character digest of the token, so listing sessions cannot hand anyone a usable credential |
| Console passwords | Postgres, argon2id | 8–256 characters. Parameters are encoded in the digest, so an old hash is upgraded in place on the next successful login |

Nothing in the repository ships a default password or key. That is checked by
the entrypoint rather than by documentation.

## Exposing the instance

The compose file publishes exactly one port, on loopback:

```yaml
ports:
  - "${DTK_BIND_HOST:-127.0.0.1}:${DTK_BIND_PORT:-8000}:8000"
```

Inside the container uvicorn listens on `0.0.0.0`. **What limits exposure is the
published address, not the listen address.** Publishing elsewhere is an explicit
act:

```bash
DTK_BIND_HOST=0.0.0.0 docker compose -p dtk -f docker/compose.yml up -d
```

Do not do that without a TLS terminator in front. The container does not
terminate TLS, does not redirect HTTP to HTTPS, and does not send HSTS. Those
are the reverse proxy's job, and there is no configuration flag here that
substitutes for one.

Two things break in a plain-HTTP deployment, and both are silent unless you know
to look:

- **The session cookie loses its `Secure` flag.** It is dropped deliberately —
  a browser would discard a `Secure` cookie on an HTTP origin and the console
  would simply not work — and the downgrade is logged as `auth.cookie_insecure`
  rather than being silent. `HttpOnly` and `SameSite=Lax` are always set.
- **Every request looks like it came from the same address.** Behind Docker's
  published-port userland proxy the peer address is the bridge gateway; behind a
  TLS terminator it is the terminator.

### Telling the instance about your reverse proxy

`X-Forwarded-For` is ignored by default, because without a trusted proxy the
caller writes it. Declaring the proxy is what changes that:

```bash
# in the repository-root .env
DTK_FORWARDED_ALLOW_IPS=172.18.0.5    # the nginx/caddy container or host
```

The entrypoint then starts uvicorn with `--proxy-headers
--forwarded-allow-ips`, and uvicorn rewrites the peer address for those hops and
for nobody else.

| Value | What the source address means |
| --- | --- |
| unset (default) | Peer address only. Recorded, but no login is refused because of it — the per-address failure counter degrades to a warning |
| your proxy's address | Real client addresses in the audit trail, the session list and the logs; the per-address login limit refuses again |
| `*` | uvicorn believes `X-Forwarded-For` from whoever sends it, so the address becomes forgeable. The instance treats this as *undeclared* and still will not refuse a login on it |

A wildcard is not a declaration. A forgeable address is a worse lockout key than
a shared one: it hands any stranger a lever on everyone else's access.

### Headers the application sets, and the ones it does not

| Header | Value | Set by |
| --- | --- | --- |
| `X-Content-Type-Options` | `nosniff` | the app |
| `X-Frame-Options` | `DENY` | the app |
| `Referrer-Policy` | `no-referrer` | the app |
| `Strict-Transport-Security` | — | your reverse proxy |
| `Content-Security-Policy` | — | your reverse proxy |

Check what a deployment actually returns:

```bash
curl -s -D - -o /dev/null http://127.0.0.1:8000/healthz
```

### Other limits on the edge

| Limit | Value | Why |
| --- | --- | --- |
| Request body | 1 MiB | A declared `Content-Length` over it is refused before the request enters the application; a chunked body is counted as it arrives and cut off |
| Pasted cookie and proxy blobs | 200,000 characters | Generous for real input, bounded enough that a paste cannot become a memory problem |
| CORS | Empty by default: same-origin only, no CORS headers at all | `security.cors_allow_origins`. A `'*'` origin drops credentials automatically — a browser would refuse the pair anyway — and is logged as `api.cors.wildcard_origin` |
| Failed logins | 5 per username / 15 min; 20 per address (only when a proxy is declared); 30 across all accounts in 60 s adds a 2 s delay | Abuse protection, never authorization. See [Users and API keys](./09-users-and-api-keys.md) |

## The SSRF chokepoint

This service exists to fetch URLs somebody else supplied, from inside a network
those people cannot otherwise reach. That is the shape of a request-forgery
problem, so URL validation is one function, in one module, applied everywhere.

Every URL the service may be asked to fetch passes `is_allowed_host` in
`src/dtk/urls/parse.py`, both before and after short-link expansion.

| Rule | Consequence |
| --- | --- |
| Only `http` and `https` | `javascript:`, `data:`, `file:`, `gopher:` are rejected before any host check runs |
| No userinfo in the authority | `https://evil.com@www.douyin.com/` is refused: it is a phishing shape, and parsers disagree about which half is the host |
| No non-default port | Only 80 for `http` and 443 for `https` |
| The host must be an ASCII DNS name | Rejects Unicode homographs and `0x7f.0.0.1`-style literals in one step |
| Loopback, private, link-local, reserved and single-label names are refused **first** | So the rejection reason is honest even though the allowlist alone would also refuse them |
| The host must be an allowlisted platform domain, or a subdomain of one, matched on a label boundary | `douyin.com.evil.com` fails; `evildouyin.com` fails; `www.douyin.com` passes |

The built-in list is four registrable domains, subdomains included:

| Platform | Domains |
| --- | --- |
| Douyin | `douyin.com`, `iesdouyin.com`, `amemv.com` |
| TikTok | `tiktok.com` |

**DNS is deliberately not consulted.** A name that resolves to a private address
today can resolve elsewhere between the check and the connection, so a lookup
here would buy a false sense of safety at the cost of a network call on the
request path. The defence is the positive allowlist plus the fact that every
outbound platform call leaves through the identity's own proxy. The literal
forms that exist only to evade string matching — decimal, hex, IPv4-mapped IPv6,
trailing dots — are caught by refusing anything that is not globally routable,
rather than by listing what is bad.

### Short links get checked again on every hop

`v.douyin.com`, `v.amemv.com`, `vm.tiktok.com` and `vt.tiktok.com` are opaque
until followed, so expansion is a loop that never lets the HTTP client chase the
chain itself:

1. At most 5 hops, with loop detection.
2. Each `Location` is handed back and re-validated against the allowlist before
   anything else is requested.
3. The expansion request carries no cookie and no signature, and leaves
   through a healthy proxy picked at random from the pool — deliberately not a
   leased identity, because the hop happens before the endpoint — and therefore
   the platform and the identity — is known. An instance with no proxies
   configured expands directly from the host; one whose proxies all exist but
   are unhealthy refuses the expansion rather than falling back to the host
   address.

If the client followed redirects itself, the first off-allowlist host would
already have been contacted by the time anyone looked.

### What `security.url_allowlist` widens, and what it does not

This is the only setting that can widen the SSRF boundary, so it is pinned down
at the point of writing rather than re-interpreted by each reader.

| It does | It does not |
| --- | --- |
| Admit an extra **exact hostname** as a redirect hop during expansion | Admit its subdomains. `cdn.example.com` does not admit `a.cdn.example.com` |
| Let a chain that detours through an unanticipated host survive | Let a caller submit a URL on that host: the API's own submission check runs without the operator list, so an added host widens expansion, never routing |
| Carry no platform, so the host reads as *allowed but unrecognized* | Give it a route table. Nothing can be built from it; it can only ever be a hop on the way to a platform |
| Refuse an entry that names this machine or a private network, by name, at write time | Silently ignore a bad entry later |

Entries are lowercased, de-duplicated and sorted on write. A URL pasted instead
of a bare hostname is refused with that reason (*"must be a bare hostname, not a
URL"*), because pasting the URL that failed is by far the likeliest mistake.

Add one only to rescue a redirect chain that dies on an unlisted hop. It is a
SENSITIVE setting: administrator role, `admin` scope, `confirm: true`, and an
audit row.

### Two allowlists that are deliberately separate

Media bytes are not pages, and the CDN hosts they come from should never be
somewhere a share link may point. So `src/dtk/media/domains.py` holds its own
per-platform list — `douyinvod.com`, `zjcdn.com`, `douyinpic.com`,
`tiktokcdn.com` and their siblings — and `security.url_allowlist` does not widen
it. A Douyin post is checked against the Douyin media list only.

The downloader sidecar closes the rebinding window that a name check cannot: it
refuses at dial time any address that is not publicly routable, so a CDN name
that resolves to `127.0.0.1` a moment after validation still fails. It also
never receives a cookie, the master key, a database address or a caller's
`?proxy=` — it gets mirrors, an allowlist and a byte ceiling.

### Task callbacks

`callback_url` is an outbound request to an address the caller chose, so it is
off unless an administrator turns it on.

| Gate | Enforced |
| --- | --- |
| `security.enable_task_webhook` | At submission, and again at delivery — the setting can be turned off while a task is queued |
| `https` only | At submission and at delivery |
| Not a private, loopback or reserved host | At submission and at delivery |
| Resolved addresses re-checked | At delivery, with the event loop's resolver |
| Certificates verified, redirects not followed | Always. Certificate verification is the real answer to DNS rebinding: an address answering on loopback cannot produce a valid certificate for the caller's hostname |
| Body | `event`, `task_id`, `endpoint`, `state`, `sent_at`, and a short `error` — never the result. Posting megabytes of collected data to a third party is not a decision anyone made by writing a URL in a query string |
| `X-Dtk-Signature` | `sha256=<HMAC of the exact bytes sent>` when `security.webhook_secret` is set. Without it a receiver cannot tell a real notification from anyone who guessed the URL |
| Failure | Logged and swallowed, 10 s timeout, 3 attempts with 2 s and 8 s backoff. A hostile or dead receiver must not be able to turn a successful fetch into a failed task |

> The API document's own front page says a callback host must be on
> `security.url_allowlist`. The code does not consult that list for callbacks;
> what is enforced is the table above. Trust the table.

## Caller-supplied egress: `?proxy=`

An operator-configured proxy and a caller-supplied one carry opposite trust, and
the codebase treats them as different features.

A row in the `proxies` table was typed by somebody with admin access, and it is
allowed to point at loopback or a LAN gateway — a local SOCKS listener is an
ordinary egress for a self-hosted install. A `?proxy=` on a request is a string
from whoever holds an API key, and *"dial this address for me"* is request
forgery in its plainest form.

| `security.request_proxy` | Behaviour |
| --- | --- |
| `deny` **(default)** | The parameter is refused with `INVALID_PARAM` and a message naming the setting |
| `public` | Accepted, but only for a publicly routable destination — loopback, private ranges, link-local and intranet names are refused |
| `any` | Accepted as given, loopback and private ranges included |

`any` is only coherent when every API key on the instance is held by someone you
would already trust with the network the instance runs in. It is the largest
single widening available in the settings table.

Three details of the implementation are deliberate:

- **A disabled feature refuses rather than ignores.** Silently dropping the
  parameter would send the request from the instance's own address while the
  caller believed it went through theirs — worse than an error, because they
  only find out from the other end.
- **An unrecognised setting value is treated as `deny`.** A typo in an
  operator's configuration must not be the thing that opens their network.
- **The value is never echoed back or logged.** A proxy URL carries
  credentials, and a rejected request is exactly when somebody is most likely to
  have pasted a real one. Only the scheme and the mode are logged.

Accepted values are at most 512 characters and must use `http`, `https`,
`socks5` or `socks5h`.

## Public endpoints, and the ones that can never be public

Every endpoint requires an API key or a console session, with two different
kinds of exception. `api.public_endpoints` lists the ones an operator *opened*,
written as `"<METHOD> <path template>"` exactly as the API document shows them.
The others were never closed in the first place, and no switch closes them:

| Route | Why it has no lock |
| --- | --- |
| `GET /healthz`, `GET /readyz` | Liveness and readiness, for whatever supervises the container |
| `GET /api/setup/status` | Answers before there is an account to authenticate as |
| `POST /api/setup/init` | Creates that account; the setup token guards it instead |
| `GET /api/v1/ios/shortcut` | The Shortcut asks for this before it has anywhere to put an API key |
| `POST /api/v1/auth/login`, `POST /api/v1/auth/logout` | A credential cannot be a precondition for obtaining one |

The console's **Endpoint access** page marks the `/api` rows above
`always_public`, which is a different state from "open"; `/healthz` and
`/readyz` are not in the API document at all.

Three prefixes are refused whatever the setting says, with no override. The ban
means those paths **cannot be added to `api.public_endpoints`** — not that every
route beneath them demands a credential, as the login and setup rows above show:

| Prefix | Why |
| --- | --- |
| `/api/v1/admin` | Identities, proxies, users, API keys, settings, backups. Opening any of these hands the instance over |
| `/api/v1/auth` | Login, sessions, password changes |
| `/api/setup` | First-run initialization, which creates the first administrator |

An entry naming one of those is dropped when the setting is parsed and logged as
`api.public_endpoints.refused`. The ban lives in the code rather than in your
judgement because the failure would be unrecoverable and silent: a mistyped
entry that happened to match an admin route would not look like anything until
somebody found it.

Opening a route is necessary but not sufficient. An anonymous caller runs as a
principal holding **exactly two scopes** — `douyin:read` and `tiktok:read` — and
the `viewer` role. Opening an endpoint that needs `media:read` achieves nothing
but turning a `401` into a `403`.

Anonymous callers are metered by source address rather than by key, so one open
endpoint cannot become an unmetered drain on the pool. Read that together with
the address caveat above: without `DTK_FORWARDED_ALLOW_IPS` the whole internet
shares one bucket, which is strict rather than lax, but means one busy stranger
can lock out the rest.

Two surfaces sit outside the switch entirely:

- **`/mcp` always requires an API key.** The console session cookie is refused
  explicitly, and the key must carry a platform read scope. It cannot be opened.
- **`/swagger`, `/redoc` and `/openapi.json` need no session at all**, by
  design: most consumers of an instance have no console account. Anyone who can
  reach the port can therefore read the full API surface. That is intentional,
  and it is a fact to weigh before publishing the port.

The console's **Endpoint access** page (`/endpoint-access`) builds its switches
from the running application's own OpenAPI document, so a switch and the setting
cannot disagree about what a path is called, and it renders permanently
protected rows as locked rather than offering a switch that would do nothing.
The same list is available at `GET /api/v1/admin/endpoints/access`.

## Roles, scopes, and the calls that hand back a credential

Roles are a ladder — `viewer < operator < admin` — and scopes bound a key.
A console session is bounded by the role only; an API key is bounded by both,
**and the scope half never softens because the key belongs to an
administrator**. Almost every key on a self-hosted box does belong to the admin
user, so an admin short-circuit there would have made `archive:export`
unenforceable in exactly the deployment it was written for.

Four guards cover the administrative surface:

| Guard | Scopes | Minimum role | Used for |
| --- | --- | --- | --- |
| `authenticated` | any | viewer | Any signed-in caller |
| `read_admin` | `admin` or `identity:manage` | viewer | Reading the admin surface: listings, settings, audit, endpoint access |
| `manage_pool` | `admin` or `identity:manage` | operator | Identity, proxy and API key maintenance |
| `admin_only` | `admin` | admin | Users, SENSITIVE settings, backup and restore |

A SENSITIVE setting write is bounded twice — `admin` scope **and** the admin
role **and** `confirm: true` — because the endpoint that writes settings is
reachable with `identity:manage`, and the role alone would not stop an
`identity:manage` key owned by the administrator from widening the URL
allowlist.

A key can also never mint a key with scopes it does not hold itself. Without
that check an `identity:manage` key could create an `admin` key and use it one
request later.

### The three calls that return a live credential

Everything else in the admin API masks. These do not, on purpose, and all three
are audited.

| Operation | Endpoint | Requires | Audit action |
| --- | --- | --- | --- |
| Reveal one cookie jar | `GET /api/v1/admin/identities/{identity_id}/cookies/reveal` | `manage_pool` | `identity.cookies_revealed` |
| Export identities as a file | `POST /api/v1/admin/identities/export` (1–200 ids) | `manage_pool` | `identity.exported` |
| Explain a request | `?explain=true` on a content endpoint | `admin` or `identity:manage`, **and** the operator role | `request.explained` |

- **Reveal** is a route of its own rather than a flag on the masked view, so the
  audit line records the deliberate act and is not drowned by the drawer that
  loads a masked jar on every open. It exists because an operator comparing a
  jar against a browser's cannot compare a mask, and the alternative on offer
  was a shell and a hand-rolled decrypt — the same disclosure with no audit line
  and no scope check in front of it.
- **Export** hands back whole identities, jars and fingerprints included. The
  document says so in a `warning` field the importer ignores, so somebody who
  finds the file later knows what they are holding. Every jar in it works until
  the platform expires it, and a logged-in one is somebody's account.
- **Explain** describes the request as it went out — signed URL, headers and the
  identity's jar — so it is a credential sitting inside an answer a `douyin:read`
  key would otherwise be entitled to. It is gated like pool management, not like
  reading, and the stored explanation is **stripped from the task result** for
  any later reader without `identity:manage`. The audit line names who asked and
  for which endpoint, never the jar.

Pinning an identity with `?identity=` is gated the same way (`admin` or
`identity:manage`, plus the operator role) for the same reason: the pool is
instance-wide and has no owners, so naming a row is asking to send a request as
whoever imported that jar. It is validated at submission — unknown id, retired,
wrong platform — so a mistyped uuid is a `400` naming the field rather than a
task that queues, runs and dies.

## The audit log

`audit_log` is a small relational table kept deliberately apart from the request
log, so the record of who changed what is never buried under ordinary traffic.

Read it at `GET /api/v1/admin/audit`, or on the console's **Logs** page under
its audit tab:

```bash
curl -s -H "X-API-Key: $DTK_API_KEY" \
  'http://127.0.0.1:8000/api/v1/admin/audit?limit=50&action=identity.cookies_revealed'
```

Filters are `limit` (up to 500), `action`, `before` for paging, and `user_id`.

| Group | Actions |
| --- | --- |
| Identities | `identity.mint_requested`, `identity.imported`, `identity.bundle_imported`, `identity.exported`, `identity.cookies_revealed`, `identity.reset`, `identity.retired` |
| Proxies | `proxy.created`, `proxy.imported`, `proxy.updated`, `proxy.deleted` |
| Credentials | `setup.completed`, `api_key.created`, `api_key.revoked`, `user.created`, `user.updated`, `user.deleted`, `user.password_changed`, `user.sessions_revoked` |
| Settings | `settings.updated`, `settings.updated_sensitive`, `settings.reset` |
| Operations | `backup.requested`, `backup.restore_requested`, `diagnose.requested`, `notification.test_requested`, `request.explained`, `watchlist.added`, `watchlist.updated`, `watchlist.paused`, `watchlist.removed` |

Each row carries the timestamp, the account, the API key id when one was used,
the target type and id, a redacted detail payload, the source address and the
user agent.

Four properties are worth knowing:

- **`detail` describes what changed, never a credential.** The table is read by
  humans and copied into bug reports.
- **Settings rows are masked again on the way out.** A settings change records
  the old and the new value, and some settings hold credentials; rows written
  before masking existed at the source are still in this table, because nothing
  trims it.
- **Nothing trims it.** There is no retention policy on `audit_log` — the
  request log is kept 14 days and identity events 90, but the audit trail grows
  for the life of the instance. It is also deliberately left out of backups: it
  records actions taken against the instance being replaced, so it does not
  travel with a restore.
- **Rows outlive their actors.** Both foreign keys are `ON DELETE SET NULL`, so
  deleting a user cannot erase what that user did.

The CLI is the gap. `dtk config set`, `dtk identity retire`, `dtk proxy add` and
the rest write to the database directly: no role check, no audit row. That is
the price of a rescue path that works when the console does not, and it is a
reason to treat shell access as the most privileged access there is.

## What never reaches a log

Redaction happens in the structured-logging processor chain rather than at call
sites, because a call site that forgets is exactly how a live session cookie
leaks.

| Rule | Effect |
| --- | --- |
| Key-based redaction | Any field named `cookie`, `cookies`, `set-cookie`, `authorization`, `x-api-key`, `api_key`, `apikey`, `password`, `secret`, `token`, `setup_token`, `proxy_url` or `dtk_secret_key` is replaced with `[REDACTED]` |
| Value-based truncation | `msToken`, `a_bogus`, `X-Bogus`, `X-Dynosaur`, `X-Gnarly`, `_signature`, `sessionid`, `sid_guard`, `odin_tt`, `uid_tt` and `ttwid` are cut to their first 6 characters wherever they appear inside a string |
| Third-party loggers | `httpx`, `httpcore` and `hpack` are pinned to WARNING. httpx logs every request line at INFO, URL and all — a callback URL routinely carries a token, and a signed CDN link carries a signature |
| Transport errors | Only the exception *type* is logged, never its message: an httpx error quotes the URL it failed on, and a proxy URL carries a password |
| Validation errors | pydantic's `input`, `ctx` and `url` keys are stripped before an error leaves the process — `input` is the offending value, and for an unparseable body it is the raw request bytes |

The per-request row in `request_log` is worth reading as a list of what is
*not* there: timestamp, request id, task id, platform, **logical endpoint name**,
identity id, proxy id, API key id, outcome, HTTP status, duration, cache hit,
signer, error code and reject reason. No URL, no query string, no headers, no
cookies, no caller address.

The diagnostic report is redacted by the renderer rather than by whoever pastes
it, because its entire purpose is to be pasted into an issue. `dtk config list`
and `dtk config get` mask credential-bearing values for the same reason.

## Backups are credential files

Two rules shape the backup format, and they are what make an archive safe to
copy off the machine — and unsafe to hand to somebody else.

- **Credentials are exported as ciphertext and the archive never holds the
  master key.** Cookie jars and proxy URLs are copied byte for byte out of their
  `bytea` columns; nothing is decrypted on the way out. The manifest carries an
  HMAC of the derived key — not the key, and not reversible into it — so a
  restore under the wrong key is reported as a mismatch instead of quietly
  producing rows that will never decrypt. The one exception is `settings`, whose
  credential fields are plain JSON in the database and are therefore encrypted
  on the way into the archive.
- **Identities are excluded unless you ask.** An identity is a jar bound to one
  proxy and one egress IP; after a move the egress has changed, so those
  identities should not be reused anyway.

| Table | In an archive |
| --- | --- |
| `users`, `api_keys`, `proxies`, `settings`, `content_snapshots` | Always |
| `identities` | Only with `include_identities` |
| `request_log`, `identity_events`, `tasks`, `audit_log`, `settings_version` | Never |

An archive therefore still contains accounts, API key hashes and settings, so
restoring one can hand an account its access back. Restore is administrator-only, needs
`confirm: true`, verifies the key fingerprint before anything is queued, and
leaves a `backup.restore_requested` audit row. Archive members are read by exact
name and never extracted to disk, so a hand-edited archive cannot write outside
its own bytes.

Backups are read at the same level as the rest of the admin surface — listing
one is not a credential — but creating and restoring are `admin_only`.

## A checklist before you expose anything

Work down this list before the port is reachable by anyone but you.

1. `DTK_SECRET_KEY` is at least 32 characters, randomly generated, and backed up
   somewhere that is not the same disk.
2. `POSTGRES_PASSWORD` and `REDIS_PASSWORD` are random, and `.env` is not
   committed.
3. A TLS terminator is in front of the published port, and it sets HSTS.
4. `DTK_FORWARDED_ALLOW_IPS` names that terminator — not `*`.
5. The first administrator account exists, so the setup path is permanently
   closed. Confirm with `GET /api/setup/status`.
6. Every script has its own API key with the narrowest scope that works, and a
   rate limit. Nothing shares the administrator's key.
7. `api.public_endpoints` is empty unless you deliberately opened something, and
   you know what an anonymous caller can reach with two read scopes.
8. `security.request_proxy` is `deny` unless you have a specific reason, and
   `security.enable_task_webhook` is off unless you use callbacks — with
   `security.webhook_secret` set if you do.
9. `security.cors_allow_origins` is empty or names your own front end, and is not
   `'*'`.
10. A backup has been taken, and the listing
    (`GET /api/v1/admin/backup`, or the console's Backup page) shows
    `manifest.key_matches: true` for it.

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk config list --scope sensitive
```

## Responsible use

The software runs on your machine, under your control, against platforms that
have their own terms. That makes you — not the project, not its author — the
person accountable for what it collects and what happens to it.

- **Respect the platforms' terms and the law where you are.** Apache-2.0 grants
  you the code; it grants you nothing about the services it talks to.
- **Respect the people behind the content.** Everything this instance archives
  is somebody's post, and often their face and voice. A self-hosted archive is
  still a database of real people.
- **Do not use it to harass anyone**, and do not redistribute work that is not
  yours.
- **Watchlists are the part that runs while you are not watching.** An entry
  collecting one author every six hours is a standing decision to keep a record
  of that person. Review the list occasionally and remove what you no longer
  need; nothing prunes the archive on a timer. `retention.content_days` reads
  like the knob for that, but **no code currently reads the key** — it is
  declared and inert — so archived posts are kept forever whatever it says.
  Removing them is a deliberate delete in the Library, and only you can decide
  when that is due.
- **A revealed jar or an export is a credential**, and a logged-in one is an
  account. It does not belong in a bug report, a shared Postman workspace or a
  chat message.

Nobody else can enforce any of that for you.

## Where to go next

- [Configuration](./03-configuration.md) — every setting named here, with its
  default and what it widens.
- [Users and API keys](./09-users-and-api-keys.md) — roles, scopes, sessions and
  login throttling in full.
- [Identities and proxies](./06-identities-and-proxies.md) — what a jar is, and
  what importing one means.
- [Operations](./10-operations.md) — backups, alerts and the audit tab in daily
  use.
- [Troubleshooting](./14-troubleshooting.md) — including what a changed master
  key looks like from the outside.
- [Installation and deployment](./02-installation.md) — the compose posture and
  putting a reverse proxy in front.
