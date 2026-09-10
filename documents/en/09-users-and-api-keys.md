# Users and API keys

This page is about who can do what on your instance. After reading it you will
know which console account to give a colleague, how to mint a key that lets a
script read Douyin and nothing else, how to take a leaked credential out of
circulation, and what happens if you decide to serve an endpoint without any
credential at all.

## Two kinds of credential

Every request to the API resolves to one *principal*: an account id, a role, a
set of scopes, and optionally the id of the key that was used. Route handlers
never ask *how* you authenticated, only what the resulting principal is allowed
to do. There are exactly two ways to produce one, plus an anonymous principal
that only exists on endpoints an operator has deliberately opened.

| | Console session | API key |
|---|---|---|
| Presented as | the `dtk_session` cookie, set by `POST /api/v1/auth/login` | `X-API-Key: dtk_...` or `Authorization: Bearer dtk_...` |
| Bounded by | the account's **role** | the **scopes** minted into the key, *and* the owner's role |
| Intended for | a human in the browser | a script, a cron job, an agent |
| Works on the REST API | yes | yes |
| Works on `/mcp` | no, refused explicitly | yes, this is the only accepted credential |
| Can change a password | yes | no, refused with `INVALID_PARAM` |
| Lives in | Redis, 7 days | Postgres, until revoked or expired |
| Revoked by | signing out, or an administrator resetting the password | `DELETE /api/v1/admin/api-keys/{key_id}` |

A console session is **not** scope-limited: it carries the full scope set and is
bounded by the account's role instead, because the console is the surface the
roles were written for. An API key is bounded by both, and the scope half is
never softened because the key happens to belong to an administrator — see
[Rules a key cannot escape](#rules-a-key-cannot-escape).

The relevant code is `src/dtk/api/deps.py`, which is short and worth reading if
you want the exact resolution order.

## The four roles

Four roles, no groups and no per-object permissions. A self-hosted instance has
a handful of users, and a rule set that fits in one table is one people actually
follow. The same table is rendered on the console's **Users** page so an
administrator can see it while handing a role out.

| Capability | admin | operator | viewer | demo |
|---|---|---|---|---|
| Call the platform endpoints | yes | yes | yes | yes |
| Read the overview, library, downloads, logs and system pages | yes | yes | yes | yes |
| Read identities, proxies, API keys and settings | yes | yes | yes | no |
| Manage identities and proxies | yes | yes | no | no |
| Create and revoke API keys | yes | yes | no | no |
| Run the self check | yes | yes | no | no |
| Change runtime settings | yes | yes | no | no |
| Change sensitive settings: allowlist, CORS, download proxy | yes | no | no | no |
| Manage accounts and roles | yes | no | no | no |
| Create and restore backups | yes | no | no | no |

Roles are a ladder, not a set: `demo < viewer < operator < admin`, so an
administrator can do everything an operator can and no endpoint has to list four
roles.

`demo` sitting *below* viewer is where demo mode's safety comes from. Every gate
is written as "viewer or better", so a role numbered underneath is refused by all
of them without a call site being touched, and a route added later is closed to a
demo instance until somebody opens it deliberately. It is also bound by a
separate read-only rule: every write is refused except the handful that are
read-shaped — parsing a link, the tools that compute an answer locally. See
[Security](./15-security.md#demo-mode).

Three things follow that surprise people:

- **A role change takes effect on the next request.** The principal is rebuilt
  from the database on every call, so there is no cache to wait out and no need
  to sign the person out.
- **The console does not hide pages by role** — `demo` is the one exception.
  Every signed-in user can open every page in the navigation; the server is what
  refuses the action. The demo account's sidebar is trimmed, because a stranger
  learns nothing from a list of links that all answer 403, while an operator
  being refused the Users page learns something true about their own instance.
  The Users page hides the account list and disables its buttons for a
  non-administrator, because a page whose every control errors is worse than a
  page that says why.
- **The `demo` role cannot be assigned by hand.** It is created by the
  `demo.enabled` switch, and the users API refuses to create or move any account
  into it — otherwise you would get a second demo account with a password an
  administrator chose and no key to go with it.

## Managing console accounts

Account management is administrator-only, and all of it lives under
`/api/v1/admin/users` and on the console's **Users** page.

| Operation | Endpoint | Who |
|---|---|---|
| List accounts | `GET /api/v1/admin/users` | admin |
| Create an account | `POST /api/v1/admin/users` | admin |
| Change a role, or reset a password | `PUT /api/v1/admin/users/{user_id}` | admin |
| Delete an account | `DELETE /api/v1/admin/users/{user_id}` | admin |

### Creating an account

On the console: **Users → Add user**, which asks for a username, an initial
password and a role. Over the API:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/users \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"username": "ops-alice", "password": "correct-horse-battery", "role": "operator"}'
```

`cookies.txt` here is the jar written by the login call under
[Console sessions](#console-sessions); an API key with the `admin` scope works
just as well, if its owner is an administrator.

| Field | Rule |
|---|---|
| `username` | 3 to 64 characters matching `^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$`: it starts with a letter or a digit, and continues with letters, digits, dot, dash or underscore. Unique across the instance. |
| `password` | 8 to 256 characters. Stored as an argon2id digest and never returned. |
| `role` | `admin`, `operator` or `viewer`. Defaults to `viewer` if omitted. |

There is no rename. A username is the account's stable name in the audit trail,
so changing it would rewrite history; create a new account and delete the old
one if you need a different name.

### Changing a role

The role dropdown on each row of the Users table, or `PUT` with `{"role": ...}`.
Nothing else changes and no session is signed out.

### Resetting someone else's password

`PUT` with `{"password": ...}`. This is the administrator's reset for a person
who forgot theirs; it deliberately does *not* ask for the current password,
because that check would only stop an administrator from helping. Every session
that account had open is revoked, including one they are sitting in front of.
The response reports how many with `revoked_sessions`.

Changing **your own** password is a different endpoint and does require the
current one — see [Passwords](#passwords).

### Deleting an account

The console asks you to type the username to confirm. Deleting an account:

- revokes every session it had open;
- **deletes every API key it owns**, by a database cascade. This is the one that
  catches people out: retiring a colleague also stops every script whose key
  they minted. Mint a replacement key from another account first, or the cron
  job goes dark at the same moment the person does;
- leaves the audit trail intact. Audit rows reference the user with
  `ON DELETE SET NULL`, so what somebody did survives their account.

### The two guards you cannot argue with

A self-hosted instance has no support desk to undo a mistake, so two operations
are refused outright:

- **You cannot delete the account you are signed in with.** Sign in as another
  administrator to do it.
- **The last administrator cannot be demoted or deleted.** An instance with no
  administrator can only be repaired from a shell inside the container.

Both come back as `INVALID_PARAM` naming the field, not as a silent no-op.

### The same jobs from a shell

The CLI reaches the database directly and needs no console session. It is the
rescue path when nobody can log in.

| Command | What it does |
|---|---|
| `dtk user list` | Lists accounts with role, creation time and last sign-in. Never prints a digest. |
| `dtk user create <username> --role admin\|operator\|viewer` | Creates an account, prompting twice for the password. `--role` defaults to `admin`. |
| `dtk user passwd <username>` | Resets a password, prompting twice. |

Both write commands accept `--stdin` to read exactly one line instead of
prompting, for `docker exec` and provisioning scripts:

```bash
printf '%s\n' 'correct-horse-battery' | \
  docker compose -p dtk -f docker/compose.yml exec -T api \
  dtk user create ops-alice --role operator --stdin
```

The CLI enforces the same 8-character floor as the console. It hashes with
argon2id at the OWASP-recommended parameters (19 MiB, 2 iterations, 1 lane); the
API uses the argon2 library's own defaults. That difference does not matter,
because the parameters are encoded in each digest: a password set from the CLI
verifies in the console and the other way round, and a digest written with older
parameters is upgraded in place on the next successful login.

The CLI knows nothing about the last-administrator guard — it writes to the
database. `dtk user create` is therefore also the way out of a demotion you
regret. See [CLI reference](./13-cli.md).

## Passwords

| Rule | Value | Where |
|---|---|---|
| Minimum length | 8 characters | server, console form and CLI |
| Maximum length | 256 characters | server |
| Algorithm | argon2id | `src/dtk/api/routes/passwords.py` |
| Rehash | automatic on the next successful login when the parameters have moved on | login |
| Anything else (dictionary checks, rotation, complexity) | not enforced | by design: this is a tool running on your own machine, not a corporate directory |

Two rules are deliberately absent, and both are the kind of thing people try to
"fix": there is no password expiry, and there is no complexity requirement
beyond the length floor. Neither is enforceable usefully by a tool whose entire
user base is the person who deployed it.

### Changing your own password

`POST /api/v1/auth/password`, or the form on the console's Users page. It asks
for the current password even though you are already signed in — a stolen
session should not be enough to take the account over — and it refuses an API
key outright, so a leaked key cannot lock the owner out of their own console.

Every *other* session for the account is signed out on success; the one you are
using survives. The usual reason to change a password is that it may have
leaked, and half a revocation is not one.

### When nobody can log in

There is no password-reset email and there is no support desk. There is a shell:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk user list
docker compose -p dtk -f docker/compose.yml exec api dtk user passwd admin
```

That is the whole recovery path, and it is why the command exists — the honest
alternative would have been "drop the database", which throws away every
identity, snapshot and setting to fix a forgotten string.

### Login throttling

Failed logins are counted three ways. All three are abuse protection, never
authorization.

| Counter | Threshold | Effect | Notes |
|---|---|---|---|
| Per username | 5 failures | refuse for 15 minutes | Always enforced. It costs an attacker the account they are guessing at and nothing else. |
| Per source address | 20 failures | refuse for 15 minutes | Enforced **only** when `DTK_FORWARDED_ALLOW_IPS` is set to something other than a wildcard. Otherwise it is logged as `auth.login_spray_suspected` and not enforced. |
| Across all accounts | 30 failures in 60 seconds | every further attempt waits 2 seconds before its password is verified | A delay, not a refusal. |

The per-address counter is soft by default on purpose. Behind Docker's published
port every request in the world arrives from the bridge gateway, and behind a
TLS terminator every request arrives from the terminator, so enforcing on that
address means twenty junk attempts from a stranger lock the only administrator
out of their own console for fifteen minutes, repeatably. Setting
`DTK_FORWARDED_ALLOW_IPS` to your reverse proxy's address is what makes the
address mean one caller again and turns the counter back into a block. A
wildcard is not a declaration: it tells uvicorn to trust `X-Forwarded-For` from
whoever sends it, which makes the address forgeable rather than merely shared.

Password verification is also capped at four concurrent hashes. argon2id at
these parameters is roughly 30 ms of CPU by design, and an open endpoint that
starts one per request is a CPU-exhaustion channel.

## Console sessions

| Property | Value |
|---|---|
| Cookie name | `dtk_session` |
| Contents | an opaque random token; the mapping token → user lives in Redis |
| Lifetime | 7 days from sign-in, **not** extended by use |
| `HttpOnly` | yes |
| `SameSite` | `Lax` |
| `Secure` | set when the request is HTTPS, or `X-Forwarded-Proto: https`; dropped otherwise, with an `auth.cookie_insecure` warning in the log |
| `Path` | `/` |

Because sessions live in Redis, flushing Redis signs everybody out. That is a
feature during an incident and a surprise during a maintenance window; it costs
nothing but a re-login.

The lifetime is absolute. Using the console does not push the expiry out, so a
browser tab left open for eight days asks for a password again even if you used
it this morning.

| Operation | Endpoint |
|---|---|
| Sign in | `POST /api/v1/auth/login` |
| Sign out this session | `POST /api/v1/auth/logout` (idempotent) |
| Who am I | `GET /api/v1/auth/me` |
| List my live sessions | `GET /api/v1/auth/sessions` |
| Sign out every other device | `DELETE /api/v1/auth/sessions` |
| Revoke one session | `DELETE /api/v1/auth/sessions/{session_ref}` |

The Users page shows one row per live session with its address, client and last
seen time. A session is identified by `session_ref`, a 16-character digest of
its token — listing your sessions can never hand anyone a usable credential, and
that is why the console can show the identifier in full.

`DELETE /api/v1/auth/sessions` keeps the session making the call, so you do not
log yourself out by using it.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{"username": "admin", "password": "..."}'
```

The response carries the signed-in principal and `expires_in: 604800`.

## API keys

A key authenticates a program: a script, a cron job, an MCP client, a Shortcut
on a phone. The console itself never uses one. The page is **API keys** in the
console's Access group.

### Creating one

Console: **API keys → Create key**. Over the API:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/admin/api-keys \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{
        "name": "nightly-archiver",
        "scopes": ["douyin:read", "archive:read"],
        "rate_limit": 60,
        "expires_at": "2027-01-01T00:00:00Z"
      }'
```

| Field | Rule |
|---|---|
| `name` | 1 to 128 characters. It is what you will see in the key list and in the audit trail, so name it after the script or host that will hold it. |
| `scopes` | The list below. The console requires at least one; the API accepts an empty list, which produces a key that authenticates and is refused by every scope-gated endpoint. |
| `rate_limit` | Requests per minute, 1 to 100000, or `null` to inherit the instance default. |
| `expires_at` | An ISO timestamp in the future, or `null` for a key that never expires. A past timestamp is refused with `INVALID_PARAM`. |

The console offers expiry as never, 7, 30, 90 or 365 days; the API takes any
future instant.

Creating a key is `manage_pool`: an **operator** role or better, and — if you
are calling with a key rather than a session — the `admin` or `identity:manage`
scope. The key is always owned by whoever created it. There is no way to mint a
key on someone else's behalf.

### The shape of a key, and the one time you see it

A key looks like this:

```
dtk_9f3c1a7b2e08_Vn4kQ2xR8sT1yU6wZ0aB3cD5eF7gH9jK
```

`dtk_` then a 12-character prefix then the secret. Only the prefix is stored in
clear, for display; the key itself is kept only as a sha256 digest of the
whole string. **The full value exists
in exactly one HTTP response, the one that creates it.** The service cannot show
it again to anyone, administrators included — there is nothing left to show. The
console's create dialog will not close on a stray backdrop click and makes you
tick an acknowledgement, because the only recovery from losing the value is
revoking the key and minting another.

### Listing keys

`GET /api/v1/admin/api-keys` returns **every key on the instance**, newest
first; add `?mine_only=true` for just your own. Reading the list is `read_admin`
— any signed-in console user, viewer included, can see the names, prefixes,
owners, scopes, limits and last-used times of everyone's keys. No secret is in
that response, but the inventory is not private between accounts. The console
page adds a search box and filters by status and scope, all evaluated in the
browser over the full list.

`last_used_at` is written on every authenticated request that key makes, which
makes it the fastest way to answer "is anything still using this?" before
revoking.

## The eight scopes

A scope is a group of endpoints. A key reaches an endpoint if it holds any one
of the scopes that endpoint accepts.

| Scope | What it unlocks |
|---|---|
| `douyin:read` | Every Douyin read: `GET /api/v1/douyin/video`, `/video/comments`, `/video/comments/replies`, `/user`, `/user/posts`, `/user/likes`, `/mix/posts`; `POST /api/v1/parse` and `POST /api/v1/tasks/batch` for Douyin links; the signature tools `POST /api/v1/tools/sign`, `/decode`, `GET /api/v1/tools/parse-url`, `POST /api/v1/tools/parse-batch`; `POST /api/v1/archive/recheck` and `/archive/backfill`; and reading back the task any of those created. |
| `tiktok:read` | The same set for TikTok, plus `/user/followers` and `/user/following`, which only TikTok serves. A key holding only this is refused on `/api/v1/douyin/...`, over REST and over MCP alike. |
| `archive:read` | What this instance has already stored, answered from Postgres without touching a platform: `GET /api/v1/archive`, `/archive/stats`, `/archive/collections`, `GET /api/v1/archive/{platform}/{content_id}`. Separate from the platform scopes on purpose — opening a platform endpoint must not thereby open everything the instance has ever collected. |
| `archive:export` | `GET /api/v1/archive/export`, which walks the whole archive in one request. It has its own scope because it is the single call that turns a read key into a copy of the database. |
| `media:read` | The download records and their bytes: `GET /api/v1/downloads`, `/downloads/storage`, `/downloads/{download_id}` and `/downloads/{download_id}/files/{name}`. It also adds the stored-media block to the archive listing, `GET /api/v1/archive`. |
| `media:write` | Anything that starts or changes a download: `POST /api/v1/downloads`, `/downloads/retry`, `/downloads/deduplicate`, `POST /api/v1/downloads/{download_id}/pin`, `DELETE /api/v1/downloads/{download_id}` — plus the library's collections (`POST`, `PATCH`, `DELETE /api/v1/archive/collections...`) and `POST /api/v1/archive/delete`. Separate from `media:read` because starting a download spends an identity and fills a disk: it is the only read-shaped call in this API with a lasting side effect on the host. |
| `identity:manage` | The pool and the machinery around it: `/api/v1/admin/identities/*`, `/api/v1/admin/proxies/*`, `/api/v1/admin/watchlist/*`, `POST /api/v1/admin/diagnose`, `POST /api/v1/admin/notifications/test`, the API key routes themselves, the read-only admin boards (settings list, request log, audit trail, metrics, endpoint access), `POST /api/v1/tools/identity`, and the `identity=` and `explain=` parameters on a data call. Do not put this on an ordinary read key: `GET /api/v1/admin/identities/{id}/cookies/reveal` hands back a decrypted cookie jar. |
| `admin` | Everything. The scope check short-circuits on it, so a key holding `admin` passes every scope gate on the instance. What it does not do is raise its owner's role — see below. |

Three consequences worth internalising:

- **The platform half of a read is checked twice.** `/api/v1/parse` and the MCP
  tools accept a link before they know which platform it belongs to, so they
  check "does this key read *something*" first and the resolved platform second.
  A `tiktok:read` key that pastes a Douyin link gets `FORBIDDEN_SCOPE`, not an
  empty result.
- **Reading a task result needs the scope that creating it needed.** A task id
  is not a bearer token for its own payload; `GET /api/v1/tasks/{task_id}`
  re-checks the scopes of the endpoint the task was for.
- **`GET /api/v1/auth/me` needs no scope at all.** It works with any live
  credential and reports the role, the scopes and the rate limit, which makes it
  the canonical way for a script to check that its key still works.

## Rules a key cannot escape

**Scopes are checked even when an administrator owns the key.** Almost every key
on a self-hosted instance belongs to the admin account, so an "administrators
bypass scopes" short cut would make `archive:export` — the one call that hands
back a copy of the database — unenforceable in exactly the deployment it was
written for. It is not a short cut the code takes.

**A key can never be given a scope its creator does not hold.** When the caller
minting a key is itself a key, the requested scopes are intersected against the
ones it holds and anything extra is refused with `FORBIDDEN_SCOPE` listing what
was refused and what is held. Without this, a key holding only `identity:manage`
— refused `GET /api/v1/admin/users` and refused a write to a sensitive setting —
could have minted itself a key with `scopes: ["admin"]` and done both, one
request apart. A console session is exempt from this particular check because it
is bounded by its role rather than by scopes, and the route already requires an
operator.

**A key carries its owner's role, and the role floor still applies.** So an
operator *can* mint a key with the `admin` scope, and that key still cannot
reach `/api/v1/admin/users` or change a sensitive setting, because those check
the owning account's role and find `operator`. Demote an account and every key
it owns loses the same power on the next request; delete the account and its
keys go with it.

**A key cannot change a password**, its owner's or anyone else's. That endpoint
requires a console session.

**A key cannot be given to someone else safely.** Keys have no per-object
ownership: any operator can revoke any key, and any key holding
`identity:manage` can pin requests to any identity in the pool. If two people
need separate blast radii, give them separate accounts, not separate keys on one
account.

## Rate limits are not billing

Every key can carry a `rate_limit` in requests per minute. **It is abuse
protection and never billing.** This project has no plans, no metering, no
usage settlement and no tenancy; the limit exists so that one runaway script
cannot drain the identity pool that every other caller shares. If you are
looking for a number to charge against, there isn't one.

| | Value |
|---|---|
| Window | fixed 60 seconds, aligned to the wall clock |
| Counted per | `key:<key id>` for a key, `user:<user id>` for a console session, `anon:<address>` for an anonymous caller |
| Limit for a key | the key's `rate_limit`, else `api.default_rate_limit_per_min` |
| Limit for a session | always `api.default_rate_limit_per_min` |
| Default | `120` requests per minute |
| Disabled when | the effective limit is `0` or less |
| Response headers | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (unix seconds) |
| Over the limit | `429` with `RATE_LIMITED` and a `Retry-After` header |

Two caveats:

- A console session is metered too, at the instance default, per account. A
  browser with several console tabs open shares that bucket.
- Anonymous callers are bucketed by source address, which behind Docker's
  published port or a TLS terminator is the same address for everyone. The
  bucket then degrades to one shared counter rather than to no counter at all —
  stricter is the right direction for a counter whose job is abuse.

`api.default_rate_limit_per_min` is a runtime setting; see
[Configuration](./03-configuration.md).

## Expiry and revocation

An expired key stops working on its own, which bounds the damage of a leak
nobody noticed. Expiry is checked at authentication time against the stored
timestamp, so it needs no sweeper and takes effect to the second.

Revocation is `DELETE /api/v1/admin/api-keys/{key_id}`, the **Revoke** button on
each row, or the bulk action after selecting several rows. It requires an
operator role, and it works on anyone's key.

- It takes effect on the **next request**. Authentication reads the row every
  time; there is no cache to wait out.
- It cannot be undone. A revoked key stays revoked; mint a replacement.
- Anything still using the key starts getting `401 UNAUTHENTICATED`.

The list's status column is computed from the row: `active`, `expired` (the
`expires_at` has passed) or `revoked`. Revoking an already-revoked key is a
no-op that still writes an audit line, with `already_revoked: true` in its
detail.

Mint one key per script or host. A single leak is then one revocation, and the
name in the audit trail tells you which machine to go and clean.

## Authenticating a program

Three ways in, in the order the server tries them:

1. `Authorization: Bearer dtk_...`
2. `X-API-Key: dtk_...`
3. the `dtk_session` cookie

```bash
# Either header works; pick one.
curl -H 'X-API-Key: dtk_9f3c1a7b2e08_...' \
  'http://127.0.0.1:8000/api/v1/douyin/video?url=https://v.douyin.com/xxxxxxx/&wait=20'

curl -H 'Authorization: Bearer dtk_9f3c1a7b2e08_...' \
  http://127.0.0.1:8000/api/v1/auth/me
```

Two behaviours to know about:

- **A key beats a cookie.** If a request carries both and the key is valid, the
  key's principal wins — including its narrower scopes. If the key is unknown,
  expired or revoked, the request falls back to the cookie.
- **A bad credential is never downgraded to anonymous.** A request that presents
  a key the server rejects is told so with `401`, even on an endpoint that has
  been opened to unauthenticated callers. Silently downgrading it would turn
  "your key expired" into "your key works but sees less", which is much harder
  to diagnose.

The MCP endpoint at `/mcp` accepts an API key only, in either header, and says
so explicitly if you send a session cookie. The key must hold `douyin:read` or
`tiktok:read` to complete the handshake; each tool then re-checks the platform
it was actually asked for. See [MCP and AI agents](./12-mcp.md).

### When a call is refused

| Status | `error.code` | Meaning |
|---|---|---|
| `401` | `UNAUTHENTICATED` | No credential, or one the server does not recognise, or one that is expired or revoked. On `/mcp` the response also carries `WWW-Authenticate: Bearer realm="dtk"`. |
| `403` | `FORBIDDEN_SCOPE` | The credential is valid but lacks the scope (`error.details.required` names the scopes that would have passed), or the account lacks the role (`error.details.required_role` names the role it needed). |
| `429` | `RATE_LIMITED` | Over the per-minute limit. `Retry-After` says how long to wait. |

Every error is the same envelope as every success: `success`, `data`, `error`,
`meta`. Branch on `error.code`, never on `error.message` — the message is
localized.

## Public endpoints

By default **every** endpoint requires a credential, and that is the right
default: an instance on a public server is a machine strangers can reach, and
its whole purpose is to spend someone else's identity pool.

Some deployments still want a subset open — a personal instance behind a
firewall, a read-only mirror, a link-parsing helper embedded in a page. The
setting is `api.public_endpoints`: a list of `"<METHOD> <path template>"`
strings, written exactly as the API document spells them.

```json
["GET /api/v1/{platform}/video", "POST /api/v1/parse"]
```

Note the path template, not a concrete path: `{platform}`, not `douyin`. The
entry is matched against the route as the OpenAPI document names it.

### Use the console page, not the text field

**Endpoint access** in the console lists every documented operation, grouped by
tag, with a switch per row. The rows are built from the running application's
own OpenAPI document, so the switch and the setting cannot disagree about what a
path is called — which matters, because a typo in a hand-written entry fails
silently: it simply never matches, and the endpoint you meant to open stays
closed until somebody notices.

The switch means "require an API key for this endpoint", so ON is the protected
state and all-on means all-safe: a row reads **Key required** while its switch is
on and **No key needed** once it is off. Turning one off is what removes a
credential check, and that is the direction the page asks you to confirm.

`api.public_endpoints` is flagged SENSITIVE, so writing it needs an
administrator, the `admin` scope, and `confirm: true` in the request body. The
console supplies the confirmation from its own dialog. Every change is written
to the audit trail as `settings.updated_sensitive`.

### What can never be opened

Three prefixes are refused whatever the setting says. There is no override.

| Prefix | Why |
|---|---|
| `/api/v1/admin` | Identities, proxies, users, API keys, settings, backups. Opening any of these hands the instance over. |
| `/api/v1/auth` | Login, sessions, password changes. |
| `/api/setup` | First-run initialization, which creates the first administrator. |

An entry naming one of those paths is dropped when the setting is parsed and
logged as `api.public_endpoints.refused`; the console renders those rows locked,
with no switch at all, rather than offering one that would do nothing. The ban
is in the code rather than left to your judgement because the failure mode is
unrecoverable and silent: a mistyped entry that happened to match an admin route
would not look like anything until someone found it.

### Opening a route is necessary, not sufficient

An unauthenticated caller runs as an anonymous principal that holds **exactly
two scopes**: `douyin:read` and `tiktok:read`. Never admin, never a write.

So opening `GET /api/v1/downloads` in the setting achieves nothing: the route
still demands `media:read`, the anonymous principal does not have it, and the
caller gets `403` instead of `401`. In practice the endpoints that can usefully
be opened are the platform reads, `/api/v1/parse`, `/api/v1/tasks/batch`, the
`/api/v1/tools` link and signature helpers, `/api/v1/tasks/{task_id}` for the
tasks those create, and `/api/v1/system/status`.

Anonymous callers are rate limited by source address rather than by key, so one
open endpoint cannot become an unmetered drain on the pool — with the caveat
about shared addresses in [Rate limits are not billing](#rate-limits-are-not-billing).

### Routes that were never closed

A handful of routes have no credential check in the code at all, so they answer
openly whatever the setting says and no switch can close them. The console lists
them separately, and it is right to: none of them fetches anything from a
platform.

| Route | Why |
|---|---|
| `POST /api/v1/auth/login` | A login endpoint cannot require you to be logged in. |
| `POST /api/v1/auth/logout` | Idempotent; logging out twice is not an error. |
| `GET /api/setup/status` | The setup wizard asks it before an account exists. |
| `POST /api/setup/init` | Creates the first administrator, gated by the one-time setup token printed in the container log instead — [Quick start](./01-quickstart.md) shows where to find it. |
| `GET /api/v1/ios/shortcut` | The iOS Shortcut asks for its release notes before it has anywhere to put a key. Public release metadata only. |

`/healthz` and `/readyz` are also unauthenticated and are not part of the API
document at all, so they do not appear on the page.

### When this is the wrong choice

Opening a platform read endpoint on an instance anyone can reach means strangers
spend your identity pool, your proxy bandwidth and your rate budget, and the
request log will attribute all of it to one anonymous bucket. If what you want
is "my other server can call this without a password", an API key with one scope
and a low rate limit is better in every respect: it is attributable, it is
revocable on its own, and it does not depend on the source address meaning
anything. See [Security](./15-security.md) for the wider exposure checklist.

## What gets recorded

Sensitive operations are written to the audit trail, readable at
`GET /api/v1/admin/audit` and on the console's **Logs** page under its audit tab.

| Action | Written when |
|---|---|
| `user.created` | an account is created |
| `user.updated` | a role is changed or a password is reset by an administrator |
| `user.deleted` | an account is deleted |
| `user.password_changed` | someone changes their own password |
| `user.sessions_revoked` | someone signs their other devices out |
| `api_key.created` | a key is minted, with its name, prefix, scopes and rate limit |
| `api_key.revoked` | a key is revoked |
| `settings.updated_sensitive` | a SENSITIVE setting changes, `api.public_endpoints` included |

Each row records who did it (account and, if applicable, key id), what was
touched, the source address and the user agent. It never records a credential:
the detail describes *what changed*, and a key's value or a cookie jar is not in
it. Audit rows outlive the accounts and keys they name, because both foreign
keys are `ON DELETE SET NULL`.

Separately, every upstream request writes one row to the request log with the
`api_key_id` that caused it, which is how you attribute pool consumption to a
particular script. That is covered in [Operations](./10-operations.md).

## Where to go next

- [Configuration](./03-configuration.md) — `api.default_rate_limit_per_min`,
  `api.public_endpoints` and the other runtime settings.
- [Console overview](./05-console-overview.md) — the rest of the pages.
- [REST API guide](./11-api.md) — the endpoints these scopes gate.
- [MCP and AI agents](./12-mcp.md) — how an agent presents a key.
- [CLI reference](./13-cli.md) — `dtk user` and the rest.
- [Security](./15-security.md) — exposure, secrets and what is encrypted at rest.
