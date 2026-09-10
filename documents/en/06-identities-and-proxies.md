# Identities and proxies

The pool is the part of this system that decides whether it works at all. After
reading this you will be able to read the Identities table and its state
machine, mint or import identities, bind them to proxies, understand the
scheduler that spends them, size the pool for your own traffic, and tell apart
the three different failures that all look like "nothing is answering".

## Why the pool exists at all

Neither platform issues API keys. Every request has to look like an ordinary
browser visit, which means it has to carry a cookie jar the platform issued, a
User-Agent and TLS profile that agree with each other, and an exit address the
jar has been seen from before.

The unit this system rotates is therefore not a cookie but a whole **identity**:
cookies, browser fingerprint and (optionally) one proxy, bound together for
life. Rotating cookies over one shared egress and one User-Agent is a *stronger*
anomaly than plain request frequency - to the platform it looks like a single
device cycling through visitors, which is a thing no real browser does.

Two consequences fall out of that, and both are deliberate trade-offs you should
not try to "fix":

- **An identity is never re-paired with a different exit.** When a proxy dies,
  the identities behind it are cooled, never moved. When a proxy is deleted, they
  are retired. Re-pairing a live cookie jar with a new exit IP is the single most
  correlatable thing this system could do.
- **One request at a time per identity.** Concurrency per identity is 1 and is
  meant to stay 1: a real session does not issue parallel API calls. You scale
  throughput by adding identities, not by raising that number.

## What one identity is made of

| Field | What it is |
| --- | --- |
| `cookies` | The jar, stored as AES-GCM ciphertext bound to the identity's row id. No API response ever contains it except the audited reveal route. |
| `fingerprint` | `browser_family`, `browser_major`, `user_agent`, `platform`, `screen`, `language`, `timezone`, and where the browser reported them, `hardware_concurrency` and `device_memory`. |
| `proxy_id` | The exit this identity was minted or imported behind. `null` means the server's own egress. |
| `authenticated` | True when the jar carried a session marker at import time. A statement about the paste, not about the account. |
| `source` | `minted` (a headless browser produced it) or `imported` (you pasted it). |
| `state` | `active`, `cooling`, `degraded`, `retired` - see below. |
| `consecutive_fails` | Failure streak. Cleared by one success; nothing else clears it except a manual reset. |
| `cooldown_until` | When a cooling or degraded identity becomes schedulable again. |
| `minted_at`, `last_used_at`, `retired_at`, `retire_reason` | Timeline. |

An identity whose fingerprint has no inferable browser family and major version
is **refused** rather than given a default. A TLS profile that contradicts the
User-Agent is worse than having no identity at all, so both minting and import
reject that case instead of guessing.

## The state machine

| State | Meaning | How it gets there | How it leaves |
| --- | --- | --- | --- |
| `active` | Schedulable right now. | Minted, imported, a cooldown elapsed, or a manual reset. | A risk-control hit, or retirement. |
| `cooling` | Resting off a risk-control hit. | `risk_control` outcome, with an exponential backoff. | The cooldown elapses (it is promoted on the next scheduling pass), or one successful request while cooling. |
| `degraded` | Hit the cooldown ceiling. Used only when no active identity exists. | A cooldown computed at or above `sched.cooldown_max_seconds`. | The ceiling cooldown elapses - it returns to `active` with its streak intact, so its health score keeps it last in the ranking until it succeeds. |
| `retired` | Dead. The cookie jar was wiped; the row is kept for its statistics. | You retired it, or its proxy was deleted. | Never. A retired identity cannot be reset, pinned or scheduled. |
| `minting` | The schema's column default. The mint path never leaves an identity in it - it writes `active` as soon as the browser returns a jar - so you will not normally see a row in this state. | Nothing writes it today. | — |

Outcomes are folded into the state after every request:

| Outcome | Effect on the identity |
| --- | --- |
| `ok` | Failure streak cleared. A cooling identity is promoted back to `active`; a degraded one keeps its probation and only has its streak cleared. |
| `business_error` (a deleted or private post) | **Nothing at all.** A missing video is a fact about the content, not about the identity. |
| `network_error` | Streak incremented. No cooldown - the request never reached the platform. |
| `risk_control` | Streak incremented, cooldown computed and applied, state set to `cooling` or `degraded`. |

The cooldown is `sched.cooldown_base_seconds x 2^(streak - 1) x risk_weight`,
capped at `sched.cooldown_max_seconds`, where `risk_weight` comes from the
endpoint policy (see [Circuit breakers, token buckets and what to do when a
breaker is open](#circuit-breakers-token-buckets-and-what-to-do-when-a-breaker-is-open)).
With the defaults - base 60 s, ceiling 21600 s (6 h):

| Consecutive risk-control hits | Cooldown at weight 1.0 | Cooldown at weight 1.8 |
| --- | --- | --- |
| 1 | 1 min | 1 min 48 s |
| 2 | 2 min | 3 min 36 s |
| 3 | 4 min | 7 min 12 s |
| 5 | 16 min | 28 min 48 s |
| 8 | 2 h 8 min | 3 h 50 min |
| 9 | 4 h 16 min | 6 h - **degraded** |
| 10 | 6 h - **degraded** | 6 h - **degraded** |

## Health, and the two different columns

The Identities table carries two columns that both look like a verdict on the
identity, and they measure completely different things. The page says so in one
line under the toolbar, because reading them as one number is how people
misdiagnose a pool.

**Credential** is a static check on the stored jar. No network, no history: is
the session cookie present, and is it long enough to be a real session rather
than the bootstrap value a first-time visitor gets?

| Verdict | Meaning |
| --- | --- |
| `ok` (Complete) | The session cookie is present and long enough. |
| `missing` (Missing) | The jar holds no session value at all. |
| `too_short` (Bootstrap only) | The value is the one the HTML document sets, not the one the platform's own JavaScript replaces it with. A request carrying it is refused **with a perfectly correct signature**. |
| `unknown` (Unreadable) | The jar could not be decrypted, or this platform has no session cookie registered. |

The cookie that decides it, per platform:

| Platform | Cookie | Minimum length treated as a real session |
| --- | --- | --- |
| `tiktok` | `msToken` | 144 |
| `douyin` | `UIFID_TEMP` | 32 |

**Health** is how this identity's recent requests actually went. The column is
computed from the same aggregate and the same two windows the scheduler ranks
on, so the number you read and the ordering the pool applies come out of one
formula:

```
health = success_rate x (1 - risk_rate) x 0.5^consecutive_fails
```

`success_rate` is read over the last 15 minutes, `risk_rate` over the last 60,
and `consecutive_fails` is the failure streak - the same three field names you
meet in `request_log` and in the API.

The three factors multiply rather than sum, because they are not commensurable -
in a weighted sum an unbounded failure streak either dominates or averages out a
contradictory state ("100% success rate, but just failed three times in a row").
Multiplying means any one factor collapsing drags the whole score down.

- Below 5 observations in the 15-minute window, the success rate is replaced by
  a prior, so a fresh identity is neither trusted like a proven one nor buried
  at the bottom. The displayed column always substitutes the built-in `0.8`;
  `pool.health_prior` retunes the prior the *scheduler* ranks on. Move that
  setting off its default and you change how eagerly cold identities are
  picked, not the number the table shows for them.
- The column is **empty** ("No traffic") when the aggregate has no rows for this
  identity. That is not a pass and not a zero - it is "nothing is known". The
  page falls back to showing the failure streak.
- The windows are read from the `identity_health_5m` continuous aggregate, which
  is a TimescaleDB object. On a stock PostgreSQL the read fails softly and every
  identity shows as unmeasured; scheduling still works, it just ranks on the
  failure streak and least-recently-used alone.
- For ranking, the score is bucketed into 5 coarse tiers. Ordering on the exact
  score would make the single healthiest identity win every time, which is a hot
  spot rather than a rotation.

## The Identities page (/identities)

The list is `GET /api/v1/admin/identities` (newest first, `limit` default 100,
maximum 500). The console asks for 200 rows and polls every 5 seconds; the
search, source and login filters run over that page in the browser, so every
count it quotes is exact.

### The automatic refill card

At the top of the page, reading `GET /api/v1/admin/identities/pool`:

- One tile per platform showing **usable** identities - live (`active` +
  `cooling`) with a failure streak below `pool.max_fail_streak`. Deliberately not
  the row count: an identity that fails every request stays live, so a pool
  counted by rows can sit at its target while serving nothing.
- Two editable numbers, **Mint below** (`pool.min_size`, default 3) and **Top up
  to** (`pool.target_size`, default 8). Saving writes only the number you
  changed. The target may not be lower than the minimum.
- A mint activity row: what is being minted right now, the last 20 attempts as a
  strip of ticks with the reason in each tooltip, and the backoff when repeated
  failures have put the sweep to sleep. It comes from the worker through Redis,
  because a failed mint writes no identity row - which is exactly why a pool that
  stubbornly will not refill used to look identical to one nobody asked to
  refill.
- If `DTK_BROWSER_RPC_URL` is not set, the number editors disappear and the card
  says so: nothing can be minted, the pool is whatever you import by hand, and
  the two thresholds have no effect.

The refill job itself lives in the worker: it checks every platform once a
minute and mints **one identity at a time**, holding a cross-process lock so
`--scale worker=N` does not turn one mint into N. Going from the low-water mark
to the target therefore takes minutes. That pace is part of the design - five
fresh visitors appearing from one deployment inside a second is a louder signal
than the requests those identities would go on to make.

The job also refuses to mint into a platform-wide incident: when every live
identity is failing (usable is 0 but live is not), it holds the level and lets
the pool alerts speak instead of adding fresh identities to be burned.

### The table

| Column | Notes |
| --- | --- |
| State | `active` / `cooling` / `degraded` / `retired`. |
| Health | The measured score, or "No traffic". |
| Platform | `douyin` or `tiktok`. |
| Credential | The static session check described above. |
| Identity id | The full uuid, with a copy button. Not hideable - this is what you paste into `?identity=`. |
| Proxy | The proxy's label when it has one, its id when it does not, "no proxy" when there is none. Never the URL, not even masked. |
| Cooldown left | Time remaining, or `—`. |
| Last used | Relative time, exact on hover. |
| Source | Minted or Imported. |
| Logged in | A lock icon when the jar carries a session. |
| Consecutive fails, Minted | Hidden by default; enable them from the column menu. |

Rows flash on a poll only when the state, credential verdict, failure streak or
health *band* changed - not on every drift of the score, because a table that
flashes every row on every poll teaches you to stop reading the flash.

### What the default view hides

By default the table drops two kinds of row and says so in a notice above it,
with the counts and a **Show all** button:

- **Retired** identities - the credential was wiped, so they cannot serve a
  request.
- Identities whose credential verdict is `missing` or `too_short` - the platform
  refuses them even with a correct signature.

Cooling and degraded identities are **not** hidden. Hiding a recovering pool is
how a console reports "no identities" to an operator who has forty. Filtering
explicitly by state `retired` shows those rows regardless.

### Row actions

- **Test** - queue one real signed request as this identity, then show the
  result. See [Testing, resetting, retiring](#testing-resetting-retiring).
- **Reset** - shown only when it would do something: the identity is cooling or
  degraded, or carries a non-zero failure streak.
- **Retire** - disabled on an already retired row.
- Selecting rows adds **Reset**, **Export N** and **Retire** to the toolbar.
- Clicking a row opens the drawer.

## The identity drawer

Clicking any row opens a drawer that answers two questions: what this identity
*is*, and what it has been *doing*.

### Cookie inventory

The card "What this identity is made of" shows the platform, source, whether the
jar carries a login, when it was minted, and the browser and time zone from the
fingerprint. Below that, one line per cookie from
`GET /api/v1/admin/identities/{id}/cookies`: the name, its role, a sentence
saying what that cookie is for, its length in characters, and a masked value
(first four and last four characters, with a fixed run of stars between them -
fixed, because a variable one would leak the length).

The length is here and deliberately absent from the listing: a truncated token
and a wrong token look identical from the outside, and the length is what tells
them apart.

| Role | Meaning |
| --- | --- |
| `required` | This build refuses to use an identity without it. Today that is `ttwid`, on both platforms. |
| `session` | Carries a logged-in session: `sessionid`, `sessionid_ss`, `sid_tt`, `sid_guard`, `uid_tt`, `sid_ucp_v1`. |
| `useful` | Not required, but an identity carrying it is refused less often: `odin_tt`, `s_v_web_id`, `msToken`, `passport_csrf_token`, `tt_csrf_token`, `__ac_nonce`, plus the cookies this build's signer reads. |
| `other` | Set by the platform and kept as received. Not something this build reads. |

The per-cookie sentences are keyed by cookie name with the role's own sentence
as a fallback, so a cookie this build has never heard of still gets an honest
answer instead of a blank.

Two banners can replace the list:

- *This jar is encrypted with a key this instance no longer holds* - the secret
  key was rotated without re-encrypting. The identity cannot sign anything
  either; retire it.
- *Retired identities have their jar wiped* - there is nothing left to show.

### Revealing a jar, and the fact that it is audited

**Show the real values** calls `GET /api/v1/admin/identities/{id}/cookies/reveal`
and prints the jar as it will be sent, plus buttons to copy it as a `Cookie:`
header (for curl) or as JSON.

This is a deliberate exception to the rule that no response carries a cookie,
and it is a separate route rather than a flag for a reason:

- It requires the **operator** role and the `admin` or `identity:manage` scope -
  a wider gate than the masked inventory, which any admin reader can load.
- It writes an audit line (`identity.cookies_revealed`, naming who asked and how
  many cookies were returned). Because it is its own route, that line is not
  drowned by the masked view the drawer loads on every open.
- The alternative it replaces is a shell and a hand-rolled decrypt: the same
  disclosure with no audit line and no scope check in front of it.

Treat a revealed jar as a credential. A logged-in one is somebody's account.

### Checking a login

`authenticated` on an identity says a session cookie was present in the paste.
Whether the platform still honours it is a different fact, and the only symptom
of the two diverging is logged-in-only data quietly missing from otherwise
successful responses. **Check the login** asks the platform
(`POST /api/v1/admin/identities/{id}/session`, queued as a task) and reports:

| Reason | Meaning |
| --- | --- |
| `live` | The platform confirmed a login, and named the account. |
| `signed_out` | No live login. TikTok answers a never-logged-in jar and a lapsed one identically, so this cannot say which. |
| `indeterminate` | Douyin only. Its endpoint answers a guest and a login the same way, with a uid of its own. What this confirms is that the jar reached the platform and was recognised - not that it is logged in. |
| `refused` | The platform refused the check rather than answering it. Says nothing about the session. |
| `unreachable` | No answer at all. A network or proxy problem, not a session one. |

The check runs through the normal pipeline, so it spends the identity exactly as
real traffic would, but it records no outcome against it.

### Request history

The drawer reads the last week of `request_log` rows for this identity (up to
200) and shows:

- A banner naming the shape of the failure. One endpoint refusing while the
  others answer is usually a signature this build gets wrong on that path, or a
  sign-protected endpoint the identity has no visitor id for - not a burnt
  cookie. *Every* endpoint refusing is the identity itself.
- A per-endpoint breakdown: calls, how many were refused, and the error codes.
- The 25 most recent requests with outcome, HTTP status and latency.

## Minting identities

Minting drives a real headless browser through the identity's own proxy, because
the cookie set and the fingerprint have to come from the same session that will
later use them. The exit's timezone and language are aligned with the proxy's
GeoIP: a proxy in Germany paired with an `Asia/Shanghai` clock is a tell given
away for free. The screen size is not derived from geography - it is whatever
the real browser reports.

It needs the `browser-rpc` container, which is optional by design and is the
heaviest service in the stack:

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
CLOAKBROWSER_COMMIT=<40-char sha> \
  docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

Three separate things have to be true, and each of them fails differently. The
container has to be running (the profile). `api` and `worker` have to be told
where it is (`DTK_BROWSER_RPC_URL` in `.env`). And the image has to have been
built with `CLOAKBROWSER_COMMIT`, which is read at **build time**, from your
shell or `docker/.env` rather than from the repository-root `.env` - an image
built without it contains no browser at all, comes up, reports healthy and mints
nothing. See [the two optional profiles and the CloakBrowser
pin](./02-installation.md#the-two-optional-profiles) for the pinned commit to use
and why it is a commit rather than a tag.

Without any of it the pool is whatever you import by hand, which is a supported
mode, not an error.

From the console, **Mint identities** takes a platform, a count of 1 to 10, and
optionally a proxy to mint behind. Each identity is submitted as its own task
(`POST /api/v1/admin/identities/mint`, `202`), the dialog waits for each, and
each takes seconds - tens of seconds is normal. Mints are serialized across the
whole deployment, so a request for five identities runs five mints one after
another; a job waits up to 180 seconds for its turn before giving up.

When a mint produces nothing, the task fails with a reason code:

| Reason | What it means |
| --- | --- |
| `no_free_proxy` | Every proxy already has a live identity of this platform behind it. Two identities behind one exit is the recombination the pool exists to prevent, so the job waits rather than doubling up. Add a proxy. |
| `proxy_not_found` | The proxy this mint was pinned to no longer exists. |
| `proxy_undecryptable` | The proxy URL could not be decrypted (wrong `DTK_SECRET_KEY`). Minting direct instead would bind the identity to an exit it will never use. |
| `rpc_unavailable` | The browser container did not answer. |
| `unusable_fingerprint` | The browser reported a fingerprint with no usable version, so the identity was refused rather than paired with a guessed TLS profile. |
| `busy` / `locked` | Another mint holds the lock. Retryable; the API answers `429` with a 30-second `Retry-After`. |

A failed *automatic* mint slows the sweep down with an exponential backoff (60 s
doubling to a 1 h ceiling, cleared by the next success). A failed *hand-pressed*
mint deliberately does not touch that counter, so a burst of button presses
against a down browser cannot silence automatic refill for an hour.

## Importing cookies you already have

**Import cookies** takes a jar you copied out of a browser. It is the whole pool
on a deployment with no browser container, and the only way to give this
instance a logged-in session.

### The four accepted paste formats

The format is detected, never declared - asking which of four shapes you hold is
how an import flow loses people.

| Format | Looks like | Where it comes from |
| --- | --- | --- |
| `header` | `ttwid=a; odin_tt=b; sessionid=c` | DevTools → copy request headers. A leading `Cookie:` is stripped. |
| `json_array` | `[{"name": "ttwid", "value": "..."}, ...]`, or a plain `{"name": "value"}` object | Cookie-manager extensions. |
| `netscape` | Tab-separated lines, 7 fields, `#` comments | `cookies.txt` exports. |
| `loose` | One `name=value` per line | Hand-copied. Also the fallback when detection picked wrong and the chosen parser found nothing. |

The whole paste is capped at 200,000 characters.

### What the preview tells you

Typing into the box runs a debounced dry run against the server, so what you see
is the server's own reading of the paste, not the browser's guess. Before
anything is stored it reports:

- the detected format;
- every cookie name, its role, and a masked value (the console never receives
  the real ones);
- the inferred browser family and major version, from the User-Agent you supply.
  Edge and Opera map onto Chrome, which is their real TLS profile;
- whether the jar carries a logged-in session;
- for a logged-in jar, the expiry read out of `sid_guard`;
- missing required cookies - without `ttwid` the jar cannot be stored at all;
- warnings, as stable codes plus a sentence: `no_cookies`,
  `logged_in_session`, `unknown_browser`, `no_useful_cookies`.

Two more fields are worth filling in:

- **User agent** - from the same browser session. Both platforms hash it into
  their signatures, so a jar sent under a different one is a contradiction they
  can see. Without it the browser cannot be inferred and the import is refused.
- **Proxy** - the egress this account normally signs in from. An imported jar is
  bound to it for life, exactly like a minted one.

The API also accepts `language` and `timezone` on
`POST /api/v1/admin/identities/import`; the console does not offer fields for
them.

### A logged-in jar is not a guest identity

The dialog puts a warning at the top and a checkbox at the bottom, and neither
is decoration:

- **A logged-in cookie set is equivalent to the account password.** Whoever
  holds it can act as that account. It is stored encrypted and no endpoint
  returns it, but an administrator of this instance can use it.
- **Use an account kept for this purpose, never your main one.**
- The pool is instance-wide and its rows have no owner. Naming an identity in a
  request (`?identity=<uuid>`) is therefore gated on the operator role and the
  `admin` or `identity:manage` scope - a plain read key must not be able to
  reach someone's logged-in session. See the [REST API guide](./11-api.md).
- A pinned request is never served from another identity and never from the
  response cache. The answer is only correct when it comes from that session.
- The worker warns 3 days before an imported session expires (a
  `cookie_expiring` notification), using the expiry it read out of `sid_guard`.
  That turns a silent mass failure into a warning days ahead of time.

Guest identities are cheap and disposable: when one burns, the pool mints
another. A logged-in identity is not, which is why every affordance around it -
the reveal audit line, the pin gate, the expiry warning - costs something.

## Exporting and restoring identity bundles

Selecting rows and pressing **Export N** calls
`POST /api/v1/admin/identities/export` and saves
`dtk-identities-<timestamp>.json` to your downloads folder. Use it to move a
working pool to a second instance, to keep a copy before a risky change, or to
hand one identity to somebody debugging.

**What lands on disk is a credential file.** Every jar in it works until the
platform expires it, and a logged-in one is somebody's account. The document
carries a `warning` field saying so for whoever finds it later, and the export is
audited by identity.

- Between 1 and 200 identities per call; the response holds every jar in full and
  is assembled in memory.
- Retired identities export as an entry with an empty jar rather than being
  dropped - silently returning fewer than you asked for is how you find out
  mid-restore.

Restoring goes through the same Import dialog: choose the file under **Restore
from a file** and the paste form switches off, because restoring a pool and
typing in one jar are different actions and a dialog that guessed which you meant
would guess wrong on the day it mattered.

The bundle route (`POST /api/v1/admin/identities/import/bundle`) carries each
entry's original fingerprint. That is the point of it: re-pasting an exported jar
through the ordinary import infers the browser from a User-Agent no longer beside
it, so the restored identity would sign with a *different* fingerprint from the
one the platform first saw it with - which is exactly the difference risk control
looks for.

- `version` is checked rather than trusted. A file written by a newer build is
  refused, not read on a guess. The current version is `1`.
- Every entry is judged on its own: one expired jar does not stop the rest, and
  the response says what was stored, what was skipped and why.
- `proxy_id` binds every restored identity to one exit.
- `dry_run` answers all of that without writing.

## Testing, resetting, retiring

**Test** (`POST /api/v1/admin/identities/{id}/test`) makes one real signed
request as this identity, through its own proxy, and classifies the answer. It
takes **no lease, no quota and records no outcome** - a probe that cooled the
identity it just probed would move the very state you are reading.

| Result | What it means |
| --- | --- |
| `ok` | The platform answered normally. Cookies accepted, signature read. |
| `business_error` | Counts as a pass. The platform answered and said the post itself is unavailable, which is a fact about the content. The built-in smoke links do rot, and this is why that does not read as an outage. |
| `risk_control` | The platform refused *this identity*, not the request. Its cookies are burnt or it has been flagged. |
| `network_error` | No answer at all: proxy, DNS or TLS between here and the platform. The same probe through a different exit is worth trying. |

**Reset** (`POST /api/v1/admin/identities/{id}/reset`) clears the cooldown and the
failure streak and marks the identity active. Recovery is deliberately slow -
an identity that has failed repeatedly usually deserves the probation - so this
is the override for the case automatic recovery cannot know about: **the failures
were not the identity's fault.** A run of bad ids, or a proxy that has since been
fixed. If the session itself is burnt, the platform will refuse it again within a
few requests and it will cool straight back down. Reset is refused on a retired
identity: the jar was wiped, so there is no session left to return to rotation,
and a button that appeared to bring one back would be a lie.

**Retire** (`DELETE /api/v1/admin/identities/{id}`) sets the state, records the
reason on the identity's timeline and **wipes the cookie column immediately**.
The statistics are worth keeping; a discarded credential is not. This cannot be
undone. Retired rows are deleted by the maintenance sweep once they are older
than `retention.retired_identity_days` (default 90).

## The Proxies page (/proxies)

A proxy is optional. Without one, every identity in the pool shares the server's
own exit address - which is usually fine for a personal instance behind a
firewall making a few hundred requests a day, and is exactly the correlation the
pool exists to avoid once you run more than a handful of identities.

Add proxies when:

- you run more than one or two identities per platform - without proxies every
  one of them leaves from the same address, and the more of them there are the
  more that pattern stands out;
- your server's address is already rate limited or blocked;
- you want an exit whose country and time zone match the locale the identity
  claims.

Do not bother when you are running one identity for personal use and it works.
A dead or slow proxy costs you more than no proxy at all - every identity behind
it gets cooled for 15 minutes when the probe fails.

### Accepted URL forms

Both the single-add box and the bulk import accept, per line:

```text
host:port
host:port:user:pass
user:pass@host:port
http://user:pass@host:port
socks5://user:pass@host:port
```

Schemes: `http`, `https`, `socks5`, `socks5h`. Anything else is a typo, not a
feature, and is rejected as `scheme_not_supported`. When a line carries no
scheme, `http` is assumed. A missing or out-of-range port is an error rather
than a default. Bracketed IPv6 literals are understood.

Note what is *not* checked: a proxy may point at a private or loopback address.
A local SOCKS listener or a LAN gateway is a perfectly ordinary egress for a
self-hosted deployment. The SSRF allowlist applies to URLs you ask the service
to *fetch*, which is a different question entirely.

A proxy URL carries a username and a password, so it is stored as AES-GCM
ciphertext bound to the row id and leaves the service only masked
(`http://***:***@host:port`). There is no "show me the proxy password" endpoint
and there will not be one.

### Adding one, or a hundred

**Add proxy** takes one URL plus an optional label, country and time zone; the
console previews how the line was read (masked) before you save.

**Bulk import** takes a pasted block, up to 500 lines. Blank lines and `#`
comments are skipped. Partial success is the point - a provider list with three
bad rows still imports the other ninety-seven - and the response tells you the
fate of every line, quoting rejected ones with any credential part stripped:

| Rejection code | Meaning |
| --- | --- |
| `unparsable` | Not a recognised proxy line. |
| `empty` | Empty line. |
| `scheme_not_supported` | Unsupported scheme. |
| `malformed_authority` | Host and port cannot be read. |
| `missing_host` | No host. |
| `missing_port` | No port. |
| `port_not_a_number` | The port is not a number. |
| `port_out_of_range` | The port is not between 1 and 65535. |

An optional label is applied to every proxy in the batch. Everything that
imported successfully is probed immediately.

### Probing, health and geography

**Test** (`POST /api/v1/admin/proxies/{id}/test`) runs one small JSON request
through the proxy and reports latency, exit address, country and time zone. A
fresh HTTP client is used per probe: connection pools are bound to an egress, and
reusing one across proxies would report the health of whichever exit the pooled
connection happens to hold.

In the background the worker:

- sweeps every proxy every five minutes and writes back health plus GeoIP (a
  proxy is never re-probed more often than once a minute);
- probes a proxy immediately when three `network_error` outcomes are recorded
  against identities behind it inside five minutes, rather than waiting for the
  next sweep - five more minutes of burning identities on a dead exit;
- **cools** every active or degraded identity behind a failed proxy for 15
  minutes. It never retires them and never moves them: the cookies are fine, only
  the egress is down;
- raises a `proxy_unhealthy` notification with the label (or the masked URL).

Retiring the identities of a permanently dead proxy is your call - a decision,
not a side effect of a timeout.

**Country and time zone matter** for one specific reason: they are handed to the
browser as a geo hint when minting behind that proxy, so the minted identity's
clock and locale agree with the address it comes out of. A probe fills them in;
you can override them by hand when the GeoIP lookup is wrong. Correcting them
does not change identities that were already minted.

The table's columns are State, Label, Address (masked), Exit (country over time
zone), Latency, Identities, Last check, and - hidden by default - Proxy id and
Created. Two caveats worth knowing, because otherwise they read as bugs:

- **Latency** is filled in from a probe you run on this page in this session. The
  listing itself carries no stored latency, so an untouched row shows `—`.
- **Identities** is `—` in the listing today; the count is not part of the list
  response. To see which identities sit behind a proxy, use the Identities page:
  the Proxy column shows the label, and the search box matches proxy ids.

You can also override health by hand (**Mark healthy** / **Mark unhealthy** on
selected rows, or the `healthy` field on `PUT /api/v1/admin/proxies/{id}`), for
an exit you know is fine but that fails the probe, or the reverse. The next
probe overwrites your verdict.

### Deleting a proxy

Deleting a proxy **retires every identity bound to it**, wiping their cookies.
That is not a safety margin the code could relax: an identity is tied to the exit
it was minted behind, so an identity whose proxy is gone has no future - keeping
it would eventually mean sending its cookies out through some other address. The
confirmation dialog says how many identities will go with it.

If the proxy is only temporarily down, do nothing: the prober cools its
identities and they come back when it does.

## How identities and proxies are paired

- The automatic mint job picks a **healthy** proxy that **no live identity of
  that platform already uses**. The number of healthy, unused proxies is
  therefore the ceiling on how many identities per platform automatic refill can
  add; when every proxy is taken it reports `no_free_proxy` and waits rather than
  doubling up. Retiring an identity frees its proxy again.
- A proxy you name explicitly (in the Mint dialog, `--proxy` on the CLI, or
  `proxy_id` in the API) overrides that search: you chose the exit deliberately,
  and a one-proxy install could otherwise never mint its second identity.
- With no proxies configured at all, identities are minted on the direct egress.
  That is what an install without proxies asked for.
- An imported jar takes whatever proxy you selected in the import dialog, or
  none.
- Nothing in the system ever moves an identity to a different proxy.

## The Scheduler page (/scheduler)

This page is the scheduler's settings, put beside the pool they act on rather
than buried in an alphabetical settings list. It shows:

- **Three tiles** - how many identities are `active`, `cooling` and `degraded`
  right now. Every number below is a statement about this census, and reading
  them apart from it is how a low-water mark gets set to a value the pool has
  never been near.
- **How one request picks an identity** - the five stages, in order:

  1. **The endpoint's circuit.** Opened by failures across at least
     `sched.circuit_min_identities` distinct identities, so one broken identity
     cannot close an endpoint for everyone. While open, one probe per interval is
     let through and everything else is refused without spending anything.
  2. **Ranking** - health tier first, then least-recently-used, then a per-call
     jitter.
  3. **In-flight** - one request at a time per identity.
  4. **Quota** - a token bucket per (identity, endpoint).
  5. **Outcome** - the result is folded back into the identity's state.

- **An honest note about fairness.** The ordering aims for "no identity is
  systematically favoured", not for round-robin. Measured against a uniform
  random baseline the resulting spread is no better than chance. What the
  scheduler does guarantee, and what its tests assert strictly, is exclusivity,
  quota, and that no identity is starved. Do not read the rotation as turn-taking.
- **Editors for every `sched.*` and `pool.*` setting**, and a disclosure saying
  how many of them are stored in the database rather than coming from a default
  or an environment seed.

Per-endpoint health and circuit state are **not** on this page - they are on the
Overview page (`/`), which lists every declared endpoint whether or not it has
traffic. See the [Console overview](./05-console-overview.md).

## Circuit breakers, token buckets and what to do when a breaker is open

**Token buckets.** Quota is keyed on `(identity, endpoint)`, not on the identity
alone. Without the endpoint dimension one identity can spend its entire budget on
the single most sensitive call, which reads as "this visitor only ever does one
thing, and does it constantly" - a stronger signal than an even spread. Buckets
refill lazily from elapsed time and idle ones expire after 24 hours.

| Endpoint | Burst (capacity) | Refill /s | Risk weight |
| --- | --- | --- | --- |
| `douyin.content_detail`, `tiktok.content_detail` | 5 | 0.30 | 1.0 |
| `douyin.author_profile`, `tiktok.author_profile` | 4 | 0.20 | 1.2 |
| `douyin.comments`, `douyin.comment_replies`, `douyin.mix_posts` and the TikTok equivalents | 3 | 0.15 | 1.5 |
| `douyin.author_posts`, `douyin.author_likes` and the TikTok equivalents | 3 | 0.12 | 1.8 |
| `tiktok.author_followers`, `tiktok.author_following` | 3 | 0.12 | 1.8 |
| `douyin.session_check`, `tiktok.session_check` | 3 | 0.20 | 1.0 |
| anything unregistered (`default`) | 3 | 0.15 | 1.5 |

Read a refill rate as "one request every 1/rate seconds, per identity": 0.12/s is
roughly one list call every eight seconds from one identity, however many are
queued. The risk weight multiplies the cooldown after a risk-control hit on that
endpoint.

**Circuit breakers.** One identity getting rate limited is routine. Every
identity failing on the same endpoint is a different event - usually a changed
upstream API or a dead signer - and continuing to retry only burns the pool. An
endpoint trips when all three conditions hold together over a rolling 300-second
window:

| Condition | Setting | Default |
| --- | --- | --- |
| Enough observations | `sched.circuit_min_samples` | 20 |
| Risk rate above the threshold | `sched.circuit_risk_threshold` | 0.6 |
| Failures span enough distinct identities | `sched.circuit_min_identities` | 3 |

The third is what separates "the endpoint broke" from "one identity broke";
without it a single flapping identity would trip the endpoint for everyone.

While a circuit is open it stays open for 300 seconds and lets exactly **one
probe through per 60 seconds** (neither of those two is exposed as a setting).
Callers get `503 ENDPOINT_CIRCUIT_OPEN` with a `retry_after`, immediately -
waiting cannot change the answer, so the scheduler does not spend its wait budget
on it. When a probe succeeds, the circuit is closed at once and the window is
cleared. Opening a circuit also raises an `endpoint_circuit_open` notification.

**What to do when a breaker is open**, in the order worth trying:

| First check | If it says | Then |
| --- | --- | --- |
| Is it one endpoint or all of them? (Overview page) | One endpoint | The signature or the upstream shape for that path changed, or that path is sign-protected and your identities lack the visitor id. Check the Playground against that endpoint - see [Playground and tools](./07-playground-and-tools.md). |
| | Every endpoint on one platform | The pool, not the endpoints. Look at the Credential column: a page full of `missing` or `too_short` is spent sessions. |
| The identities' Credential column | Mostly `ok` | The jars are fine; suspect signing or egress. Probe one identity, then probe its proxy. |
| | Mostly `missing` / `too_short` | Retire them and let the pool mint replacements. |
| A single identity's drawer | One endpoint refuses, others answer | Not a burnt cookie. A signature or a sign-protected path. |
| | Everything refuses | That identity is spent. Retire it. |
| The proxies page | A proxy just went unhealthy | Its identities were cooled automatically. Fix or delete the proxy; do not reset the identities until it is back. |

There is no "reset this breaker" button and no API endpoint for one, by design:
a tripped endpoint reopens itself when a probe succeeds, and forcing it open
again before the cause is fixed only burns the pool faster. If you have fixed the
cause, the ordinary answer is to let the next probe through - within a minute.
The manual escape hatch, for when a minute is too long, is to delete the
breaker's two Redis keys by hand: see [Troubleshooting](./14-troubleshooting.md)
for the exact command. It is a deliberate step outside the console, not a
missing button.

## The Endpoint Access page (/endpoint-access)

This page belongs in this document for one reason: **every open endpoint spends
your identity pool.** Anonymous callers do not bring their own identities.

By default every endpoint requires an API key or a console session, which is the
right default - an instance on a public server is a machine strangers can reach,
and its whole purpose is to spend someone else's pool. This page lists every
documented operation, grouped by tag, built from the API document itself so a
switch and the setting it writes cannot disagree about what a path is called.

- The switch means **"Require an API key for this endpoint"**, so ON is the
  protected state and a page of switches that are all on describes an instance
  where nothing is exposed. Each row says which state it is in - **Key
  required** or **No key needed**. Turning one **off** is what removes a
  credential check, and that is the direction that asks for confirmation;
  turning it back on does not.
- Opening one writes `api.public_endpoints`, a SENSITIVE setting (the write
  carries an explicit confirmation flag), as a list of
  `"<METHOD> <path template>"` strings exactly as the API document spells them.
- **Admin, authentication and setup paths can never be opened.** `/api/v1/admin`,
  `/api/v1/auth` and `/api/setup` are refused by the server whatever the setting
  says, so those rows render locked with no switch - a switch that lied would be
  worse than no switch.
- A handful of routes have **no credential check at all** in the code (you cannot
  log in to reach the login endpoint). They are listed separately as "No
  credential", not counted into the warning, and no switch here closes them.
  None of them fetch anything, so none of them spend the pool.
- An anonymous caller gets read scopes only (`douyin:read`, `tiktok:read`), never
  admin and never write, and is rate limited **by client address** rather than by
  key, using `api.default_rate_limit_per_min` (default 120). Behind Docker's
  userland proxy every caller can look like the bridge gateway, which degrades
  that to one shared bucket - stricter, not looser.

[Users and API keys](./09-users-and-api-keys.md) is the full treatment of this
page - what can never be opened, why a key is necessary but not sufficient, and
the routes that were never closed. See [Security](./15-security.md) before
opening anything on an instance that is reachable from the internet.

## How many identities do I need

Work from the token buckets, not from a feeling. One identity sustains roughly
`refill_per_sec` requests per second **on each endpoint separately**, and at most
one request at a time overall.

| What you do | Endpoint | Per identity | For 1 request/s you need |
| --- | --- | --- | --- |
| Look up single videos | `*.content_detail` (0.30/s) | ~18 / minute | ~4 identities |
| Read profiles | `*.author_profile` (0.20/s) | ~12 / minute | ~5 identities |
| Page through comments | `*.comments` (0.15/s) | ~9 / minute | ~7 identities |
| Walk an author's posts | `*.author_posts` (0.12/s) | ~7 / minute | ~9 identities |

Then add headroom, because the pool is not always all there:

- Cooling identities are not schedulable. On a healthy pool that is a small
  fraction; during an incident it is most of it.
- The refill job only counts identities with a failure streak under
  `pool.max_fail_streak` (default 3), so a burnt identity does not fill a slot.
- Every mint is one at a time and takes seconds, so a pool cannot grow quickly
  after you lose it. Keeping `pool.target_size` a few above what you need is
  cheaper than minting under pressure.

The defaults - mint below 3, top up to 8 per platform - suit a personal instance
doing a few thousand detail lookups a day. A pool of 3 with no proxies and a
watchlist running every six hours is a working configuration; a pool of 3 serving
a public endpoint is not.

Requests that cannot get a lease wait up to `sched.max_wait_seconds` (default 10)
and then fail with `503 IDENTITY_POOL_EXHAUSTED`. If you see those, the choice is
more identities, fewer callers, or accepting the queue.

## Why did they all go cooling at once

Because cooling is per-identity but almost every cause is shared. In rough order
of likelihood:

| Cause | Tell | What to do |
| --- | --- | --- |
| A proxy failed its probe | The proxies page shows it unhealthy; every cooled identity shares that proxy; the cooldowns are all 15 minutes | Fix the proxy. The identities come back on their own. Do not reset them first - they will cool again immediately. |
| The platform is refusing this deployment | Cooling identities across several proxies; an endpoint circuit opening shortly afterwards | Stop sending traffic and wait. Minting into it makes the signal louder, which is why the refill job deliberately refuses to. |
| The sessions are genuinely spent | Credential column full of `missing` / `too_short`; the drawer shows every endpoint refusing | Retire them and let the pool mint replacements, or import fresh jars. |
| A signing or upstream change | Credential column mostly `ok`, but every request classified `risk_control`; often one endpoint before all of them | Check the browser container and the endpoint health board; this is not a pool problem. |
| A classifier reading a normal answer as risk control | Identities cooled while probes pass and the platform is clearly fine | Reset the affected identities. This has happened: until 2026-09-09 this build read Douyin's answer for a post that does not exist as risk control, so looking up one wrong id cooled the identity that asked. Fixing the classifier repaired none of the damage - which is exactly what Reset is for. |
| Network trouble to the platform | `network_error` outcomes rather than `risk_control`; no cooldowns, but the failure streak climbs | Look at DNS, TLS and the proxies. A `network_error` refunds its token and never cools an identity by itself. |

## From the command line

The CLI lives in the `api` container and reads the same database and settings:

```bash
docker compose -p dtk -f docker/compose.yml exec api dtk identity list --platform douyin
```

| Command | What it does |
| --- | --- |
| `dtk identity list [--platform P] [--state S] [--limit N]` | Identities with state, browser, proxy, streak, cooldown and last use. Cookie jars never appear. |
| `dtk identity mint --platform P [--count N] [--proxy ID]` | Mint through browser-rpc, 1 to 20. Fails with an actionable message when `DTK_BROWSER_RPC_URL` is unset. |
| `dtk identity test <id> [--url URL] [--timeout S]` | One real signed request. No lease, no quota, no bookkeeping. |
| `dtk identity retire <id> [--reason TEXT]` | Retire and wipe. Retiring twice is refused rather than overwriting the original reason. |
| `dtk proxy list [--healthy]` | Proxies with masked URLs, geography and probe state. An unprobed proxy reads `unchecked`, not `healthy`. |
| `dtk proxy add <url> [--label L] [--country C] [--timezone Z]` | Add one; the URL is encrypted before it reaches the database. |
| `dtk proxy import <file>` | One URL per line, optionally followed by whitespace and a label; `#` comments skipped; duplicates skipped. Rejected lines are reported by line number and never by content. |
| `dtk proxy test <id> [--probe-url URL] [--write/--no-write]` | Probe one proxy and, by default, write health and GeoIP back onto the row. |

Ids may be given as the shortened prefix the tables print, as long as it is
unambiguous. Full reference: [CLI reference](./13-cli.md).

## Settings that shape the pool

Of the settings below, the `pool.*` and `sched.*` ones are edited on the
Scheduler page. The rest are on the Settings page - and `api.public_endpoints`
also has its own Endpoint access page, described above. All of them are
reachable with `dtk config`, and all of them are documented alongside every
other setting in [Configuration](./03-configuration.md).

| Setting | Default | What it does |
| --- | --- | --- |
| `pool.min_size` | 3 | Low-water mark per platform. Below it, the refill job mints. |
| `pool.target_size` | 8 | What it tops up to. Clamped up to `min_size` if set lower. |
| `pool.max_fail_streak` | 3 | Failure streak at which an identity stops counting towards the pool level, so the filler replaces it instead of counting it. |
| `pool.health_prior` | 0.8 | Assumed success rate for an identity with too little traffic to score. |
| `sched.max_wait_seconds` | 10 | How long a request waits for an identity before failing. |
| `sched.cooldown_base_seconds` | 60 | First cooldown after a risk-control hit. |
| `sched.cooldown_max_seconds` | 21600 | Cooldown ceiling; reaching it marks the identity degraded. |
| `sched.circuit_risk_threshold` | 0.6 | Risk rate above which an endpoint may trip. |
| `sched.circuit_min_samples` | 20 | Minimum observations in the 300 s window before a trip. |
| `sched.circuit_min_identities` | 3 | Distinct failing identities required to trip. |
| `sched.queue_max` | 500 | Maximum queued requests. |
| `retention.identity_events_days` | 90 | How long an identity's timeline is kept. |
| `retention.retired_identity_days` | 90 | How long a retired identity's row survives before it is deleted. |
| `api.public_endpoints` | `[]` | Endpoints served without a key. SENSITIVE. |
| `api.default_rate_limit_per_min` | 120 | Per-key and per-anonymous-address ceiling. |

Related reading: [Concepts](./04-concepts.md) for the model behind all of this,
[Operations](./10-operations.md) for alerts and routine maintenance,
[Troubleshooting](./14-troubleshooting.md) when something is already broken.
