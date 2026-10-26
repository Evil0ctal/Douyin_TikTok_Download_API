# Identities and proxies

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin, TikTok and Bilibili data API: REST, MCP and a web
> console, with an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/06-identities-and-proxies.md)

The pool is the part of this system that decides whether it works at all. After
reading this you will be able to read the Identities table and its state
machine, mint or import identities, bind them to proxies, understand the
scheduler that spends them, size the pool for your own traffic, and tell apart
the three different failures that all look like "nothing is answering".

## Why the pool exists at all

None of the platforms issues API keys. Every request has to look like an ordinary
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
| `source` | `minted` (this instance produced it: a headless browser for Douyin and TikTok, one plain HTTP request for Bilibili) or `imported` (you pasted it). |
| `state` | `active`, `cooling`, `degraded`, `retired` - see below. |
| `consecutive_fails` | Failure streak. Cleared by one success; nothing else clears it except a manual reset. |
| `cooldown_until` | When a cooling or degraded identity becomes schedulable again. |
| `minted_at`, `last_used_at`, `retired_at`, `retire_reason` | Timeline. |
| `note` | The operator's label for it: one line, at most 200 characters - whose account a login is, where its cookie came from. Nothing in the request path reads it. See [The identity drawer](#the-identity-drawer). |
| `account_id`, `account_seen_at` | The account the platform named the last time the session was checked, and when. Empty until a check has named one. See [Checking a login](#checking-a-login). |

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
| `minting` | The schema's column default. The mint path never leaves an identity in it - it writes `active` as soon as a jar comes back - so you will not normally see a row in this state. | Nothing writes it today. | — |

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
| `bilibili` | `buvid3` | 1 - present is enough |

Douyin's value goes by more than one name: a minted identity holds `UIFID_TEMP`,
a login exported from a browser holds `UIFID`, and the signer accepts a few
other spellings too. The column looks for them in the order the signer reads
them, judges the first one that has a value, and names that one in the cell; it
shows `UIFID_TEMP` only when the jar holds none. Earlier builds looked at
`UIFID_TEMP` alone, so every Douyin login imported from a browser read
`missing` (Missing) and the list hid it as a spent session - while the signer
was signing with it all along.

No bootstrap value of Bilibili's `buvid3` has been measured, so being present
is enough. Earlier builds registered nothing for Bilibili and every Bilibili row
read `unknown` (Unreadable) here - which on this column means "this build cannot
tell", and that was not true of a cookie it refuses to import without.

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
maximum 500, paged with `offset`). Retired identities are left out unless the
request sets `include_retired`, asks for state `retired`, or names one
`identity_id`. They are kept for 90 days, and when the console fetched them and
hid them itself, a bad week of retirements pushed older logins off the page
without a word. `meta.total` says how many identities match, and `meta.retired`
how many retired ones the same filters left out.

The console asks for 200 rows and polls every 5 seconds. The platform, state,
source, login and rotation filters go to the server; the search box runs over
the rows already loaded, because it matches what people type - a proxy label, a
platform's name in this language - which the database does not hold. It matches
an identity's note and its platform account as well, which is how one login is
found among several. Under the table the page says "Showing N of M", and **Load
more** asks for another 200, up to the server's 500; past that, narrow the
filters.

The filters live in the address - `/identities?platform=&login=&id=`, plus
`state`, `source`, `rotation`, `serves`, `q` and `all` - so the Overview's
per-platform alerts link straight to one platform's identities and the request
log straight to one identity's drawer, and a reload or the back button keeps the
view. `login` is `yes` or `no`. A value this build does not know reads as no
filter rather than an empty table, so a link written against a newer build still
opens.

`serves=<endpoint>` has no control in the toolbar: it is the question "who can
serve this endpoint", asked from the endpoint's side, and the Scheduler page's
coverage table links with it. It keeps the identities rotation may send there -
those in rotation for every endpoint and those limited to a list that names it,
never a reserved one, and only the logins where the endpoint needs one from its
first page, as Douyin's author likes do - and the page says so in a notice above
the table, with
**Clear** beside it. Without the notice, a table narrowed that way would read as
a pool that had lost most of its identities. It filters on rotation alone, so
cooling identities are listed too - unless `ready=1` comes with it, as it does from
the coverage table's **Can serve**: then only the active and degraded ones the
scheduler would send there now, the identities that number counted.

Every write on this page takes the operator role. Below it, Mint, Import, the
row buttons, row selection and the refill card's editors are hidden rather than
disabled; the server is the gate either way.

### The automatic refill card

At the top of the page, reading `GET /api/v1/admin/identities/pool`:

- One tile per platform showing **usable** identities - live (`active` +
  `cooling`) with a failure streak below `pool.max_fail_streak`. Deliberately not
  the row count: an identity that fails every request stays live, so a pool
  counted by rows can sit at its target while serving nothing.
- One row per platform with its own **Auto** switch and two numbers, **Mint
  below** and **Top up to**. A platform follows the global `pool.min_size`
  (default 3) and `pool.target_size` (default 8) until you give it numbers of
  its own, and a number that is still following says **default** next to it.
  Typing the global value back makes it follow again, and **Restore default**
  drops both of the platform's own numbers at once. Switching **Auto** off
  stores `pool.<platform>.min_size = 0`: that platform is never minted for
  automatically and raises no pool alerts, which is what a deployment that only
  serves one platform wants. Saving writes only what you changed. The mark must
  be at least 1 while Auto is on, and the target may not be lower than it.
- A login line per platform: how many logged-in identities rotation can reach,
  with the reserved, cooling and limited ones counted beside it. Where a platform
  has endpoints that refuse a guest (Douyin's author posts past their first page
  and an author's likes on every page, Bilibili's comments past their first) and no login is in rotation, the line names those
  endpoints, says they will answer `IDENTITY_LOGIN_REQUIRED`, and offers
  **Import a login** preset to that platform. Until this line, that error was
  the first sign that the only imported login had been reserved, had cooled or
  had lapsed.
- A mint activity line per platform: what is being minted right now, that
  platform's share of the last 20 attempts as a strip of ticks with the reason
  in each tooltip, and the backoff when repeated failures have put the sweep to
  sleep. Per platform because the worker rests the browser and the plain-HTTP
  route separately; one shared strip read a down browser as Bilibili failing
  too. It comes from the worker through Redis, because a failed mint writes no
  identity row - which is exactly why a pool that stubbornly will not refill used
  to look identical to one nobody asked to refill.
- If `DTK_BROWSER_RPC_URL` is not set, Douyin and TikTok cannot be minted: their
  number editors disappear, their pools are whatever you import by hand, and
  their thresholds have no effect. Bilibili's row stays editable and keeps
  refilling, because its guests are minted without a browser (see [Bilibili
  identities](#bilibili-identities)). The card says so.

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
| State | `active` / `cooling` / `degraded` / `retired`, with **Reserved** or **Limited** under the badge when rotation does not hand the identity out everywhere. |
| Health | The measured score, or "No traffic". |
| Platform | Douyin, TikTok or Bilibili, by name. |
| Credential | The static session check described above. |
| Identity id | The uuid, cut short where the column is narrow; a click copies the whole id and the drawer prints it in full. Not hideable - the id is what you paste into `?identity=`. |
| Note | The operator's label on one line, cut short when it is long with the whole text on hover, or `—`. A column of its own rather than a line under the id, where it read as part of the id and went unnoticed; it is what tells three logins apart at a glance. Sortable, and matched by the search box. |
| Proxy | The proxy's label when it has one, its id when it does not, "Server egress" when there is none. Never the URL, not even masked. |
| Kind | Minted or Imported, then Guest or Logged in (with a lock). A login also says when it lapses - "expires in 3d", "expires in 5h" or "expired", the date on hover - highlighted inside a week, which is the time to re-import one. The date is read from the session cookie (`sid_guard`, or Bilibili's `SESSDATA`), so a session the platform ended early still shows its old date; **Check the login** is for that. Sorting puts logins first, soonest to lapse first. One column where Source and Logged in used to spend two on two words. |
| Cooldown left | Time remaining, or `—`. |
| Last used | Relative time, exact on hover. |
| Consecutive fails, Minted | Hidden by default; enable them from the column menu. |

Rows flash on a poll only when the state, credential verdict, failure streak or
health *band* changed - not on every drift of the score, because a table that
flashes every row on every poll teaches you to stop reading the flash.

### What the default view hides

By default the table drops two kinds of row and says so in a notice above it,
with the counts and a **Show all** button (`all=1` in the address):

- **Retired** identities - left out by the server and counted from
  `meta.retired`; **Show all** asks for them with `include_retired`. The
  credential was wiped, so they cannot serve a request.
- Identities whose credential verdict is `missing` or `too_short` - held back
  in the browser from the rows loaded; the platform refuses them even with a
  correct signature.

Cooling and degraded identities are **not** hidden. Hiding a recovering pool is
how a console reports "no identities" to an operator who has forty. Filtering
explicitly by state `retired` shows those rows regardless.

### Row actions

- **Test** - queue one real signed request as this identity, then show the
  result. See [Testing, resetting, retiring](#testing-resetting-retiring).
- **Reset** - shown only when it would do something: the identity is cooling or
  degraded, or carries a non-zero failure streak.
- **Retire** - disabled on an already retired row. Retiring the last login a
  platform has in rotation names the endpoints that will then answer
  `IDENTITY_LOGIN_REQUIRED`.
- Selecting rows adds **Reset**, **Set rotation for N identities**, **Export N**
  and **Retire** to the toolbar. They act on the selected rows the current
  filters still show - a selection used to survive a filter change and act on
  rows nobody could see - and report "n done, m failed" rather than stopping at
  the first refusal.
- **Set rotation for N identities** gives every selected identity that is not
  retired one rotation choice, the same three the drawer offers (see [Keeping an
  identity out of rotation](#keeping-an-identity-out-of-rotation)). Three logins
  bought for the author feed were otherwise three trips through three drawers. A
  list of endpoints is offered only when every selected identity is on one
  platform, because an endpoint name belongs to one; across platforms the choice
  is every endpoint or only requests that name them. When the selection holds
  every login a platform has in rotation for an endpoint that needs one, and the
  choice takes them off it, the dialog names the endpoints that would then answer
  `IDENTITY_LOGIN_REQUIRED` wherever a login is needed and turns the button red. It warns
  rather than asks.
- Clicking a row opens the drawer and puts the identity's id in the address
  (`?id=`).

## The identity drawer

Clicking any row opens a drawer that answers two questions: what this identity
*is*, and what it has been *doing*.

It reads its row from the polled list, so a reset, a retirement or a cooldown
ending shows in it rather than only in the table behind it; a link naming an
identity that is not on the loaded page fetches that one on its own. Along the
top are the platform, the failure streak, the last use, any cooldown, a login's
expiry, and for a retired identity when and why it was retired. Then the
actions: **Copy id**, **Open in Logs** (the request log filtered to this
identity, `/logs?tab=requests&identity_id=`), and for an operator **Test**,
**Reset** where it would do something, **Export** and **Retire** - the same
actions as the row, so having read why an identity is failing you do not have to
close the drawer to find the button. A probe's outcome, HTTP status and latency
show under them.

Below the facts along the top sit the identity's note and its platform account.
Three imported logins were three UUIDs, and the only way to know which account
was which was to remember the order they went in.

- **Note** is the operator's: one line, at most 200 characters - whose account
  it is, where the cookie came from. **Add a note**, or **Edit** on one that
  exists, opens it in place; Enter saves, Escape cancels without closing the
  drawer, and saving it blank clears it. Below the operator role it is shown and
  not editable. It has its own column beside the id in the table, which the
  search box also matches, and it appears under the identity in the request
  log and first in each option of the Playground's identity picker.
  Nothing in the request path reads it. A retired identity keeps its note, and
  it can still be changed - "which account was this" is exactly what somebody
  asks of one afterwards.
- **Platform account** is the account the platform named the last time the
  session was checked, and when - see [Checking a login](#checking-a-login).
  The two answer one question from two sides: the note is what somebody
  believes, the account id is what the platform answered, so a stale note, or
  one login imported twice, still shows.

The note is written with `PATCH /api/v1/admin/identities/{id}` and
`{"note": "..."}`, or `{"note": null}` to clear it. The field is required, so
an empty body cannot read as clearing it; a line break or more than 200
characters once trimmed is refused with `INVALID_PARAM`. It takes the operator
role, answers with the identity as the list shows it, and a change is audited
as `identity.note_changed` - recording whether there was a note before and
after, never its text, because a note is free text and somebody writes the
phone number a login belongs to into it.

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
| `required` | This build refuses to use an identity without it. Today that is `ttwid` on Douyin and TikTok, and `buvid3` on Bilibili. |
| `session` | Carries a logged-in session: `sessionid`, `sessionid_ss`, `sid_tt`, `sid_guard`, `uid_tt`, `sid_ucp_v1`, and Bilibili's `SESSDATA`. Bilibili's `bili_jct` and `DedeUserID` are shown here too, as part of a login, though neither proves one on its own. |
| `useful` | Not required, but an identity carrying it is refused less often: `odin_tt`, `s_v_web_id`, `msToken`, `passport_csrf_token`, `tt_csrf_token`, `__ac_nonce`, Bilibili's `buvid4`, `b_nut`, `buvid_fp` and `_uuid`, plus the cookies this build's signer reads. |
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

When the platform names an account, the check stores it on the identity as
`account_id`, with the time in `account_seen_at`, and the drawer shows it as
**Platform account**. A check that names none - `refused`, `unreachable` - leaves
the stored one as it was: it says nothing about whose cookies these are, and must
not erase what an earlier check learned. On Douyin the id comes with
`indeterminate`, for a guest as much as for a login, so there it confirms that
the platform recognised the client, not that it is logged in. What the id is
for is telling identities apart: two logins with the same note, or one account
imported twice, show the same id.

The check runs through the normal pipeline, so it spends the identity exactly as
real traffic would, but it records no outcome against its health. The account id
is a fact about the cookies, not a verdict on them.

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

For Douyin and TikTok, minting drives a real headless browser through the
identity's own proxy, because the cookie set and the fingerprint have to come
from the same session that will later use them. The exit's timezone and
language are aligned with the proxy's GeoIP: a proxy in Germany paired with an
`Asia/Shanghai` clock is a tell given away for free. The screen size is not
derived from geography - it is whatever the real browser reports. Bilibili keeps
the same rule without a browser; see [Bilibili identities](#bilibili-identities).

It needs the `browser-rpc` container, which is optional by design and is the
heaviest service in the stack:

```bash
echo 'DTK_BROWSER_RPC_URL=http://browser-rpc:9000' >> .env
docker compose -p dtk -f docker/compose.yml --profile browser up -d --build
```

Two separate things have to be true, and each of them fails differently. The
container has to be running (the profile). And `api` and `worker` have to be
told where it is (`DTK_BROWSER_RPC_URL` in `.env`). The CloakBrowser revision
the image installs comes from `docker/compose.yml`, so a Compose build puts a
browser in it without anything else set. See [the CloakBrowser
pin](./02-installation.md#building-the-browser-image-the-cloakbrowser-pin) for
why it is a commit rather than a tag, and how to build another one.

Without any of it the Douyin and TikTok pools are whatever you import by hand,
which is a supported mode, not an error. Bilibili goes on minting.

From the console, **Mint identities** takes a platform, a count of 1 to 10, and
optionally a proxy to mint behind. Each identity is submitted as its own task
(`POST /api/v1/admin/identities/mint`, `202`), the dialog waits for each, and
each takes seconds - tens of seconds is normal. Mints are serialized across the
whole deployment, so a request for five identities runs five mints one after
another; a job waits up to 180 seconds for its turn before giving up. The dialog
offers only the platforms this instance can mint (`can_mint` per platform), names
the others with a pointer to importing instead, and ends with one summary - "3
minted, 2 failed" and the worker's reason - rather than a toast per failure.

When a mint produces nothing, the task fails with a reason code:

| Reason | What it means |
| --- | --- |
| `no_free_proxy` | Every proxy already has a live identity of this platform behind it. Two identities behind one exit is the recombination the pool exists to prevent, so the job waits rather than doubling up. Add a proxy. |
| `proxy_not_found` | The proxy this mint was pinned to no longer exists. |
| `proxy_undecryptable` | The proxy URL could not be decrypted (wrong `DTK_SECRET_KEY`). Minting direct instead would bind the identity to an exit it will never use. |
| `rpc_unavailable` | The browser container did not answer. |
| `unusable_fingerprint` | The browser reported a fingerprint with no usable version, so the identity was refused rather than paired with a guessed TLS profile. |
| `mint_refused` | Bilibili only: the site did not hand out a guest session this time. The refill job backs off and tries again. |
| `busy` / `locked` | Another mint holds the lock. Retryable; the API answers `429` with a 30-second `Retry-After`. |

A failed *automatic* mint slows the sweep down with an exponential backoff (60 s
doubling to a 1 h ceiling, cleared by the next success). A failed *hand-pressed*
mint deliberately does not touch that counter, so a burst of button presses
against a down browser cannot silence automatic refill for an hour. The backoff
is kept per minting route - the browser, or Bilibili's plain request - so a
browser container that is down no longer stops Bilibili refilling.

## Importing cookies you already have

**Import cookies** takes a jar you copied out of a browser. It is the whole
Douyin and TikTok pool on a deployment with no browser container, and the only
way to give this instance a logged-in session.

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
- which platform the cookie names point at. When that is not the platform
  chosen in the dialog, it says so and offers **Switch to ...** - offered, not
  done, because switching under someone still typing would re-judge their
  paste. The dialog used to open on Douyin whatever was pasted, so a Bilibili
  jar was judged against Douyin's required cookies and refused for a `ttwid` it
  was never going to have. Each platform's box also lists the cookies it needs;
- every cookie name, its role for the chosen platform as the server judges it,
  and a masked value (the console never receives the real ones);
- the inferred browser family and major version, from the User-Agent you supply.
  Edge and Opera map onto Chrome, which is their real TLS profile;
- whether the jar carries a logged-in session;
- for a logged-in jar, the expiry read out of `sid_guard` (Douyin, TikTok) or
  `SESSDATA` (Bilibili);
- missing required cookies - without `ttwid` (Douyin, TikTok) or `buvid3`
  (Bilibili) the jar cannot be stored at all;
- warnings, as stable codes plus a sentence: `no_cookies`,
  `logged_in_session`, `unknown_browser`, `no_useful_cookies`.

Three more fields are worth filling in:

- **User agent** - from the same browser session. Douyin and TikTok hash it into
  their signatures, so a jar sent under a different one is a contradiction they
  can see. Without it the browser cannot be inferred and the import is refused.
- **Proxy** - the egress this account normally signs in from. An imported jar is
  bound to it for life, exactly like a minted one.
- **Note** - optional: whose account this is, or where the cookie came from. One
  line, at most 200 characters, shown beside the id wherever the identity
  appears and changeable later in the drawer. The import call takes it as
  `note`.

The API also accepts `language` and `timezone` on
`POST /api/v1/admin/identities/import`; the console does not offer fields for
them.

Storing closes the dialog and opens the new identity's drawer, and for a login
runs **Check the login** at once - whether the platform takes the session is the
one question an import leaves open. The success toast carries a **Copy id**
button, for `identity=`.

### A logged-in jar is not a guest identity

The dialog always puts a warning at the top, and for a jar that carries a login -
or a restore file holding one - asks for a checkbox at the bottom before it
stores anything. Neither is decoration. The checkbox is asked only of a login:
making someone swear a guest's visitor id is a password taught them to tick it
without reading.

- **A logged-in cookie set is equivalent to the account password.** Whoever
  holds it can act as that account. It is stored encrypted and no endpoint
  returns it, but an administrator of this instance can use it.
- **Use an account kept for this purpose, never your main one.**
- **The account could be banned.** Platforms rarely ban an account for reading
  data with it, but a ban cannot be ruled out. You import a login, and use the
  features that need one, at your own risk; the checkbox is where the dialog
  asks you to accept that.
- The pool is instance-wide and its rows have no owner. Naming an identity in a
  request (`?identity=<uuid>`) is therefore gated on the operator role and the
  `admin` or `identity:manage` scope - a plain read key must not be able to
  reach someone's logged-in session. See the [REST API guide](./11-api.md).
- A pinned request is never served from another identity and never from the
  response cache. The answer is only correct when it comes from that session.
- The worker warns 3 days before an imported session expires (a
  `cookie_expiring` notification), using the expiry it read out of `sid_guard`,
  or Bilibili's `SESSDATA`. That turns a silent mass failure into a warning days
  ahead of time.

Guest identities are cheap and disposable: when one burns, the pool mints
another. A logged-in identity is not, which is why every affordance around it -
the reveal audit line, the pin gate, the expiry warning - costs something.

### Keeping an identity out of rotation

Naming an identity serves the one request that names it, and on its own does
nothing about the rest: the scheduler goes on handing the same account to every
other request too, which is exactly the traffic that gets a logged-in account
flagged. The drawer's **Rotation** card decides what rotation may use the
identity for, as one of three choices under **What rotation may use it for**:

| Choice | `endpoints` it stores | What rotation does with the identity |
| --- | --- | --- |
| **Every endpoint** | `null` | Hands it to any endpoint - the default. Endpoints that need a login still prefer or require one, and with guests first on, the rest reach a login only when no guest can; see [What needs a logged-in identity](#what-needs-a-logged-in-identity). |
| **Only the endpoints ticked below** | A list of endpoint names from the identity's own platform | Hands it out for the ticked endpoints and nothing else. This is how a login is kept for the endpoints that need one. The table marks it **Limited**. |
| **Only requests that name it** | `[]` | Never hands it out, not even for a later page that needs a login. The card shows the `identity=<id>` it is reached with, and the table marks it **Reserved**. |

Until this card offered the middle choice, the console had one switch for the
last, and somebody who wanted their login kept for the author feed turned it on
and watched the author feed stop using it: the feed never names an identity, and
"only the requests that name it" is all an empty list means.

The middle choice opens a checklist of the platform's endpoints. The ones a guest
is short-changed on are marked **Needs a login**, **Later pages need a login** or
**Fuller with a login**, with what a guest gets instead on hover; today that is
Douyin's author posts and Bilibili's comments and a comment's replies, all three
**Later pages need a login**, and Douyin's author likes, **Needs a login**. **Only the ones that need a login** ticks the
marked ones and nothing else; **All** and **None** do what they say. Endpoints
that answer only about the identity asking - bookmarks, and Douyin's bookmark
folders - are not listed: they always need `identity=`, so rotation never
reaches them whatever a list says. An endpoint named in a list set through the
API that this build does not offer is shown ticked, so saving does not drop it.

Nothing changes until **Save**, and **Cancel** puts the stored choice back; both
appear once something has changed. Taking the last login a platform has in
rotation off an endpoint that requires one asks first - in the card, not in a
dialog over the drawer, because both would answer Escape and cancelling the
question used to close the drawer. It names the endpoints that would then answer
`IDENTITY_LOGIN_REQUIRED` wherever a login is needed unless the request names
an identity, and **Change it anyway** goes ahead. For several identities at once,
select them and use **Set rotation for N identities** (see [Row
actions](#row-actions)).

For an identity kept out of rotation - wholly when reserved, for the endpoints
left unticked when limited:

- **Rotation never hands it out there.** Unpinned requests, the self-check and
  an ad-hoc `dtk fetch` all skip it.
- **A request with `identity=` still uses it**, for any endpoint, in any state
  but retired. That is the point of keeping it.
- **It is left out of the pool level**, limited or reserved. The refill job
  counts only identities rotation may spend on every endpoint, so it mints a
  replacement for general traffic rather than counting an account kept for one
  endpoint or for none.
- **It does not travel with an export.** It is a decision about this pool, not
  part of the identity.
- **It is not a fallback.** When the only identities left for an endpoint are
  ones kept off it, an unpinned request answers `IDENTITY_POOL_EXHAUSTED` rather
  than borrowing one, and `details.reserved` says how many it skipped - so a
  pool page full of active identities beside that error is a setting, not a
  scheduler fault.

The import dialog offers the last choice as a checkbox, **Only for requests that
name it**, and the import call takes `reserved: true`: the identity is stored out
of rotation from the start, so a login imported to serve the requests that name
it is never handed to anything else, not even in the moment between storing it
and reserving it. Ticking it for a login when the platform has no other login in
rotation says which endpoints will go on answering `IDENTITY_LOGIN_REQUIRED`
wherever a login is needed.

The card and the bulk dialog both write
`POST /api/v1/admin/identities/{id}/rotation` with `{"endpoints": ...}`, taking
the values in the table above; a list must name endpoints of the identity's own
platform. It needs the same role as resetting or retiring, and every change is
audited.

### What needs a logged-in identity

Douyin gives a guest identity less of an author's posts than it gives a
signed-in account. Measured on 2026-10-17:

| Request | What a guest gets | What dtk does |
| --- | --- | --- |
| First page of `/api/v1/douyin/user/posts` | An answer, short of the author's newest few posts | Uses a logged-in identity whenever rotation has one. With none, a guest answers, the result carries `meta.guest_fallback: true`, and that answer is not cached |
| Any later page (a `cursor` other than `0`) | `{"status_code":0}` and nothing else | Logged-in identities only. With none, `IDENTITY_LOGIN_REQUIRED` (503) at once - no guest is sent to be refused |

An author's likes go further: a guest gets nothing at all. Measured on 2026-10-20,
on one author of fifteen whose likes were public:

| Request | What a guest gets | What dtk does |
| --- | --- | --- |
| `/api/v1/douyin/user/likes`, any page | Zero bytes - the same answer as a private list, even for a public one | Logged-in identities only. With none, `IDENTITY_LOGIN_REQUIRED` (503) at once. A private list answers `NOT_FOUND`, for a login as it did for a guest |

Until 5.1.6 these went to guests, so every author's likes read as private.

Bilibili does the same with a video's comments. Measured on 2026-10-18:

| Request | What a guest gets | What dtk does |
| --- | --- | --- |
| First page of `/api/v1/bilibili/video/comments` | The three hottest comments, and word that this is the last page | Uses a logged-in identity whenever rotation has one. With none, a guest answers, the result carries `meta.guest_fallback: true`, and that answer is not cached |
| Any later page | Nothing - it was told the first page was the last | Logged-in identities only. With none, `IDENTITY_LOGIN_REQUIRED` (503) at once |

TikTok does the same with its search. Measured on 2026-10-20:

| Request | What a guest gets | What dtk does |
| --- | --- | --- |
| First page of `/api/v1/tiktok/search/posts` | A thin page - one post for one keyword, eight for another - however many posts match | Uses a logged-in identity whenever rotation has one. With none, a guest answers, the result carries `meta.guest_fallback: true`, and that answer is not cached |
| First page of `/api/v1/tiktok/search/users` | Ten accounts, and word that there are more | Guests first, like the endpoints below |
| Any later page of either | No list at all | Logged-in identities only. With none, `IDENTITY_LOGIN_REQUIRED` (503) at once |

A signed-in TikTok search has not been measured, so how much more a login gets
there is not known.

Everything else gives a guest the same answer as a login. On Douyin that is
single posts, profiles, and comments and their pages; all of TikTok but its
search; and on Bilibili everything but the comment list and a comment's replies
past their first page - a guest is served the first page of replies, up to 20,
and nothing after it, so later pages of replies need a login just as comments
do. Sounds, hashtags, mixes, live rooms - by link, by broadcast, many at once,
and a Douyin room's audience ranking - related posts and trending boards, and
Bilibili's search, were measured with guests only: a guest got a full answer,
so they are treated the same way. The one thing a guest was not told is the
score beside each place on that ranking, and no score is returned to anyone.

Those endpoints are answered by **guests first**, `pool.<platform>.guests_first`,
on by default and set per platform. A guest is minted for nothing and replaced
in a minute; a login is somebody's account, and spending it on a request a guest
would have answered identically only wears it out for the endpoints that need
it. So rotation tries the guests first - the active ones, or the degraded ones
when none is active, because a guest failing costs a mint and a login failing
costs the account - and the logins after them.

The logins are a second tier the request moves on to, not a pool consulted only
once the guests are gone. A guest that is mid-request or out of quota gives way
to an idle login, so the setting never turns a request away that a free login
could have served; it decides who goes first, and a pool of logins alone keeps
working. Off, logins and guests share these endpoints by recency, as they did
before the setting existed. Endpoints that need a login do not read it at all:
they prefer or require one either way, and a login an endpoint prefers is waited
for rather than swapped for a guest.

The setting is the switch on the Scheduler page's coverage card (see [The
Scheduler page](#the-scheduler-page-scheduler)). The worker reads it on every
request, so a change takes effect without a restart.

Before endpoints declared what a guest misses on them, rotation did not look at
whether an identity was logged in. On Douyin, page 2 went to a guest, came back as 17 empty bytes, was reported as
`UPSTREAM_RISK_CONTROL` and cooled that guest, while the imported login sat
idle. The first page came from a guest too, and was missing the newest posts.

An identity counts as logged in when its jar carried a session cookie
(`sessionid` and the like, `SESSDATA` on Bilibili) at import - **Logged in** in
the Kind column, and the table's login filter. A session that
lapses later still counts, and its refusals cool it like any other; **Check the
login** in the drawer tells you which it is.

To read authors' full feeds:

1. Import a logged-in cookie, from an account kept for this purpose.
2. Keep it for the endpoints that need it. In its drawer's **Rotation** card,
   choose **Only the endpoints ticked below**, press **Only the ones that need a
   login**, and **Save**. For several logins at once, select them in the table
   and use **Set rotation for N identities**. Rotation then hands them author
   posts and nothing else, comments and lookups keep going to guests, and the
   table marks them **Limited**. With guests first on, a login in rotation for
   every endpoint already reaches the others only when no guest can; limiting it
   also keeps it off them on the day the guests are gone - a browser container
   down, a pool burnt - and takes it out of the pool level, so the refill job
   mints guests to carry the rest. The same through the API:
   `POST /api/v1/admin/identities/{id}/rotation` with
   `{"endpoints": ["douyin.author_posts"]}`.
3. Expect it to be the bottleneck. One account serves every author-feed request
   one at a time, within its token bucket, and paging fast through many authors
   is exactly the pattern that gets an account flagged. Add accounts, not speed.

`IDENTITY_LOGIN_REQUIRED` carries `details.cooling`, the logged-in identities
this request could use that are cooling down. `0` means none is in rotation for
this endpoint: none was imported, or it is kept out of rotation. More than `0`
means wait, or check whether their sessions have lapsed. The Scheduler page's
coverage card shows, for every endpoint, how many logins rotation can send there
and how many identities are cooling - before a request fails on it.

Some lists belong to the account asking, not to anyone the request names:
bookmarks on Douyin and TikTok (`/user/bookmarks`), and Douyin's bookmark folders
(`/user/collections`). Rotation is never used for these. Pass `identity`
naming the imported identity whose list you want, or the request is refused
with `INVALID_PARAM` on the `identity` field; the answer is not cached, because
it is that account's alone. Without the rule, an unpinned call would have
returned whichever account rotation drew, and the shared cache would then have
served that account's list to the next caller. Bilibili has neither: it does not
serve `/user/bookmarks`, and its `/user/collections` names an author and lists
that author's public folders.

## Bilibili identities

Bilibili is the platform whose guest session is plain HTTP rather than something
a page script hands to a browser, and the pool treats it accordingly.

- **Minted without a browser.** A guest's `buvid3` and `buvid4` are the answer
  to one request, the site's own `/x/frontend/finger/spi`, made through this
  instance's TLS-impersonating transport and behind the identity's proxy. The
  session is still created by the same client, from the same exit, that will
  later spend it - the rule the browser path keeps. So an install with no
  browser container still keeps a Bilibili pool: the refill job, the **Mint
  identities** dialog and `dtk identity mint` all mint it, the refill card
  shows Bilibili as mintable, and since the refill
  job backs off per minting route, a browser container that is down does not
  stop Bilibili refilling.
- **Its own marks.** `pool.bilibili.min_size` and `pool.bilibili.target_size`
  follow the same rules as the other platforms': `-1` follows the global value,
  `0` turns automatic minting off.
- **Importing.** A Bilibili jar needs `buvid3`, or it cannot be stored.
  `SESSDATA` marks it as a logged-in session - treat it like the account's
  password - and carries its own expiry, which the table shows and the
  `cookie_expiring` warning uses. `buvid4`, `b_nut`, `buvid_fp` and `_uuid` are
  worth bringing along.
- **Checking the login.** **Check the login** asks Bilibili's
  `/x/web-interface/nav`, which really does tell a live login apart: `live`,
  with the account's mid, when it answers `isLogin: true`; `signed_out` for a
  guest or a lapsed session.
- **What a login is for.** Comments: a guest gets the three hottest and nothing
  more (see [What needs a logged-in identity](#what-needs-a-logged-in-identity)).
  Later pages of a comment's replies need one too. To keep an imported account
  for those, **Only the ones that need a login** in the Rotation card ticks
  `bilibili.comments` and `bilibili.comment_replies`; the rotation call above
  takes the same list.
- **The uploader video list is the endpoint Bilibili guards hardest.** A burst
  of requests to `/api/v1/bilibili/user/posts` from one egress gets HTTP 412
  pages for a few minutes whatever the headers (measured), which this instance
  reports as `UPSTREAM_RISK_CONTROL` and answers by cooling the identity. Its
  token bucket is the slowest of any Bilibili endpoint; spreading requests out
  and putting identities behind proxies is what helps. See
  [Troubleshooting](./14-troubleshooting.md#http-412-on-a-bilibili-uploaders-video-list).

## Exporting and restoring identity bundles

Selecting rows and pressing **Export N** calls
`POST /api/v1/admin/identities/export` and saves
`dtk-identities-<timestamp>.json` to your downloads folder; the drawer's
**Export** does the same for one identity. More than 200 go out as several files
of 200 (`-part-1.json`, `-part-2.json`, ...), because that is what one export
call and one restore accept. Use it to move a working pool to a second instance,
to keep a copy before a risky change, or to hand one identity to somebody
debugging.

**What lands on disk is a credential file.** Every jar in it works until the
platform expires it, and a logged-in one is somebody's account. The document
carries a `warning` field saying so for whoever finds it later, and the export is
audited by identity.

- Between 1 and 200 identities per call; the response holds every jar in full and
  is assembled in memory.
- Retired identities export as an entry with an empty jar rather than being
  dropped - silently returning fewer than you asked for is how you find out
  mid-restore.
- Each entry carries the identity's `note`. It is a label, not a credential, and
  it is the one thing in the file that says whose account a login is. Rotation
  does not travel: a restored identity is in rotation for every endpoint until
  you choose otherwise.

Restoring goes through the same Import dialog: choose the file under **Restore
from a file** and the paste form switches off, because restoring a pool and
typing in one jar are different actions and a dialog that guessed which you meant
would guess wrong on the day it mattered. A loaded file is first checked with a
dry run, so the dialog lists what can be restored and what will be skipped, with
the reasons, before anything is written.

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
- An entry's `note` is restored with it. A note this build would refuse - longer
  than 200 characters, or more than one line - is dropped and the identity
  restored without it: the identity is worth more than the label on it.
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

- this server cannot reach the platform at all - mainland China to TikTok is
  the usual case. Requests then fail with `UPSTREAM_UNREACHABLE`, and only
  identities behind a proxy fix that: see [A platform this server cannot reach
  directly](#a-platform-this-server-cannot-reach-directly);
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
imported successfully is probed immediately, the same way **Test all** probes
(below); the dialog does not wait for the probes to finish.

### Probing, health and geography

**Test** (`POST /api/v1/admin/proxies/{id}/test`) runs one small JSON request
through the proxy and reports latency, exit address, country and time zone. A
fresh HTTP client is used per probe: connection pools are bound to an egress, and
reusing one across proxies would report the health of whichever exit the pooled
connection happens to hold.

**Test selected** and **Test all** run that probe over many proxies, four at a
time, with the progress on the button ("Testing 4 of 15") and one summary at the
end ("12 ok, 3 failed"). Each probe has a 90-second budget, so one at a time a
list of twenty dead proxies took half an hour to report, and a toast per failure
buried the page; the rows carry each proxy's answer, and the summary is the only
thing worth interrupting for.

`socks5://` and `socks5h://` proxies are probed exactly like `http://` ones.
Earlier builds could not probe them at all: **Test** answered `INTERNAL` and the
sweep left them with no verdict.

When a probe fails, its reason is kept with the proxy, credentials stripped, and
shown under the **Unhealthy** badge until a probe passes; the list response
carries it as `last_error`. `ProxyError: 400 Bad Request` there means the
address answered but is not a forward proxy - see [the troubleshooting
entry](./14-troubleshooting.md#upstream_unreachable-and-a-platform-this-server-cannot-reach).

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
Created. Two of them need a note:

- **Latency** is filled in from a probe you run on this page in this session. The
  listing itself carries no stored latency, so an untouched row shows `—`.
- **Identities** counts the identities bound to the proxy that are not retired,
  across all platforms. A `0` on a proxy you meant identities to use is the
  first thing to check when requests still go out direct: an identity is bound
  when it is minted or imported, never afterwards. To see which identities sit
  behind a proxy, open its drawer (below), or use the Identities page: the Proxy
  column shows the label, and the search box matches proxy ids.

Clicking a row opens the proxy's drawer: the whole last error (the column clamps
it to two lines), the probe result when there is one, the identities bound to it
(`GET /api/v1/admin/proxies/{id}`; retired ones left out, each linking to its
identity, and marked when kept out of rotation), and the last 24 hours of
requests that went out through it, with **View requests** linking to
`/logs?tab=requests&proxy_id=`. That last part is the evidence when a platform
starts refusing an egress: a Bilibili 412 is a verdict on an address, and every
identity behind one proxy shares it. Test, Edit and Delete are in the drawer's
footer. Every write on this page takes the operator role, so below it Add, Bulk
import, Test, Edit, Delete and row selection are hidden; the list and the drawer
stay readable.

You can also override health by hand (**Mark healthy** / **Mark unhealthy** on
selected rows, or the `healthy` field on `PUT /api/v1/admin/proxies/{id}`), for
an exit you know is fine but that fails the probe, or the reverse. The next
probe overwrites your verdict.

### Deleting a proxy

Deleting a proxy **retires every identity bound to it**, wiping their cookies.
That is not a safety margin the code could relax: an identity is tied to the exit
it was minted behind, so an identity whose proxy is gone has no future - keeping
it would eventually mean sending its cookies out through some other address. The
confirmation dialog says how many identities will go with it, and lists each
proxy with its own count. When that number is above zero you confirm by typing:
the proxy's label, or its host when it has none - or, deleting several at once,
the number of identities about to be retired.

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
  That is what an install without proxies asked for. Leaving the proxy unset in
  the Mint dialog runs the same search as the automatic job, so a mint goes
  direct only in this case.
- An imported jar takes the proxy you select in the import dialog. The default
  option, **No proxy - direct from this server**, means exactly that, for the
  identity's whole life; an import never searches for a proxy.
- Nothing in the system ever moves an identity to a different proxy.

### A platform this server cannot reach directly

From mainland China, TikTok is not reachable without a proxy. Every request
then fails on the network before the platform answers, and the API reports
`UPSTREAM_UNREACHABLE` with `details.proxied: false`. Adding a proxy afterwards
changes nothing by itself: the identities already in the pool were bound to the
direct egress when they were minted or imported, and that binding is permanent.

1. Add a forward proxy the containers can reach - a local Clash or mihomo
   inbound, not a VPN subscription URL - and press **Test** until it is healthy.
2. Retire that platform's direct identities.
3. Mint behind the proxy, or import with the proxy selected in the dialog.
4. Check that the proxy's **Identities** column counts them.

With a single proxy, choose it explicitly in the Mint dialog: automatic refill
takes only a proxy no identity of that platform uses, so it stops after the
first. The addresses to use and the errors along the way are in
[troubleshooting](./14-troubleshooting.md#upstream_unreachable-and-a-platform-this-server-cannot-reach).

## The Scheduler page (/scheduler)

This page is the scheduler's settings, put beside the pool they act on rather
than buried in an alphabetical settings list. It shows:

- **Three tiles** - how many identities are schedulable, `cooling` and
  `degraded` right now. Schedulable is `active` minus the reserved identities
  (`reserved_active` in `GET /api/v1/system/status`): a reserved identity is
  active, but only a request that names it can use it, so it is not capacity.
  Every number below is a statement about this census, and reading them apart
  from it is how a low-water mark gets set to a value the pool has never been
  near.
- **Per platform** - the same census one row per platform, with schedulable,
  reserved, cooling and degraded side by side. A request is only ever served by
  an identity of its own platform, so a sum across platforms can read healthy
  while one of them has nothing to give.
- **Logins and who serves each endpoint** - the same pool, counted per endpoint,
  because that is how the scheduler hands it out. A login limited to the author
  feed is in the pool and absent from every other endpoint, a reserved one is
  absent from all of them, and an endpoint that needs a login with none in
  rotation fails every later page while the pool reads full - none of which the
  per-platform counts show. Per platform the card has:

  - the **Guests first where no login is needed** switch, which is
    `pool.<platform>.guests_first` (see [What needs a logged-in
    identity](#what-needs-a-logged-in-identity)), with a line saying what the
    current position does;
  - how many identities serve only requests that name them;
  - a table, one row per endpoint rotation can reach. **Login** is the tag the
    Rotation card's checklist uses. **Can serve** counts the active and
    degraded identities rotation may send there - in rotation for every
    endpoint, or limited to a list that names it, and only logins where the
    endpoint needs one from its first page - and links to exactly those,
    `/identities?platform=<platform>&serves=<endpoint>&ready=1`. **Logins** is how many
    of those are logins, a 0 shown in caution where the endpoint or its later
    pages need one, because those pages then answer `IDENTITY_LOGIN_REQUIRED`.
    **Limited to
    it** is how many of them are limited to a list naming it, and **Cooling**
    the ones resting that come back to it when they recover, linked the same way
    with `state=cooling`.

  Endpoints that answer only about the identity asking are left out: they always
  need `identity=`, and nothing on the card limits a request that names an
  identity. The card reads `GET /api/v1/admin/identities/coverage`; it is shown
  from the viewer role up and not to the demo account, which that route refuses.
  Changing the switch takes the operator role.
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
- **Editors for every `sched.*` and `pool.*` setting** but the per-platform ones -
  the guests-first switches are on the coverage card and the marks are the grid
  below - and a disclosure saying how many of them are stored in the database
  rather than coming from a default or an environment seed.
- **Pool size per platform** - the six `pool.<platform>.min_size` and
  `target_size` settings as one grid, platform by low-water mark and target,
  rather than six rows reading `-1` among the global marks, where `-1` looked
  like a value someone had chosen. An empty cell inherits, and shows the global
  value it inherits; clearing a cell stores `-1` rather than deleting the
  override, because a delete falls back to the environment seed, which this page
  cannot see. One **Save** writes the grid's changes together, so a target is
  not left below its own floor between two clicks - the writes still go one
  after another, not atomically.

Writing takes the operator role. The demo account sees the census and the
rotation, and neither the settings nor the coverage card, since both routes
refuse it.

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
| `douyin.related_posts`, `douyin.live_room`, `douyin.live_room_by_id` and the TikTok equivalents | 4 | 0.20 | 1.2 |
| `douyin.music_detail`, `douyin.hashtag_detail`, `douyin.live_status`, `douyin.trending` and the TikTok equivalents | 4 | 0.20 | 1.0 |
| `douyin.comments`, `douyin.comment_replies`, `douyin.mix_posts`, `douyin.author_mixes`, `douyin.music_posts`, the TikTok equivalents and `tiktok.hashtag_posts` | 3 | 0.15 | 1.5 |
| `douyin.live_ranking` | 3 | 0.15 | 1.2 |
| `douyin.author_posts`, `douyin.author_likes` and the TikTok equivalents | 3 | 0.12 | 1.8 |
| `tiktok.author_followers`, `tiktok.author_following` | 3 | 0.12 | 1.8 |
| `tiktok.search_posts`, `tiktok.search_authors` | 2 | 0.08 | 1.8 |
| `douyin.session_check`, `tiktok.session_check`, `bilibili.session_check` | 3 | 0.20 | 1.0 |
| `bilibili.content_detail`, `bilibili.content_streams` | 5 | 0.30 | 1.0 |
| `bilibili.author_profile` | 4 | 0.20 | 1.0 |
| `bilibili.related_posts`, `bilibili.live_room` | 4 | 0.20 | 1.2 |
| `bilibili.trending`, `bilibili.live_streams` | 4 | 0.20 | 1.0 |
| `bilibili.comments`, `bilibili.comment_replies`, `bilibili.author_collections`, `bilibili.collection_posts`, `bilibili.author_mixes`, `bilibili.mix_posts` | 3 | 0.15 | 1.2 |
| `bilibili.author_posts` | 2 | 0.08 | 1.8 |
| `bilibili.search_posts`, `bilibili.search_authors` | 2 | 0.08 | 1.5 |
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
  Being SENSITIVE, only an administrator can change it: below that role the
  switches are hidden, with a line saying why.
- **Each open endpoint says which platforms it serves**, in the warning card
  that lists them and in the confirmation. A route with `{platform}` in its path
  serves every platform this build supports, including any added later - an
  endpoint opened before 5.1.6 began spending a Bilibili pool the day Bilibili
  shipped, and nothing on this page said so. The warning card also has **Close
  all**, which asks first.
- **Admin, authentication and setup paths can never be opened.** `/api/v1/admin`,
  `/api/v1/auth` and `/api/setup` are refused by the server whatever the setting
  says, so those rows render locked with no switch - a switch that lied would be
  worse than no switch.
- A handful of routes have **no credential check at all** in the code (you cannot
  log in to reach the login endpoint). They are listed separately as "No
  credential", not counted into the warning, and no switch here closes them.
  None of them fetch anything, so none of them spend the pool.
- An anonymous caller gets read scopes only (`douyin:read`, `tiktok:read`,
  `bilibili:read`), never admin and never write, and is rate limited **by client
  address** rather than by key, using `api.default_rate_limit_per_min` (default
  120). Behind Docker's userland proxy every caller can look like the bridge
  gateway, which degrades that to one shared bucket - stricter, not looser.

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

Bilibili's rates are the same except for an uploader's video list,
`bilibili.author_posts`, at 0.08/s: about 5 a minute, so about 13 identities for
one request a second.

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
| `dtk identity mint --platform P [--count N] [--proxy ID]` | Mint 1 to 20: Bilibili over plain HTTP, Douyin and TikTok through browser-rpc. For those two it fails with an actionable message when `DTK_BROWSER_RPC_URL` is unset. |
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
| `pool.<platform>.min_size` | -1 | This platform's own mark (`douyin`, `tiktok` or `bilibili`). -1 follows `pool.min_size`; 0 turns automatic minting and pool alerts off for the platform. |
| `pool.<platform>.target_size` | -1 | This platform's own target. -1 follows `pool.target_size`. |
| `pool.<platform>.guests_first` | true | This platform's endpoints that need no login are answered by guest identities first, and by a login only when no guest can take the request - none in rotation for them, or every one busy or out of quota. Endpoints that need a login are unaffected. Off, logins and guests share them by recency. Read on every request. |
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
