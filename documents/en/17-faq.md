# FAQ and glossary

This page answers the questions people ask before and just after they deploy this
project, and then defines every term the rest of the documentation uses. After
reading it you should be able to decide whether this software fits your situation
at all, and to read any other page here without stopping to look something up.

Every answer is drawn from the code or the configuration defaults. Where an
answer depends on something the project cannot control — what a platform does,
what your local law says — it says so instead of guessing.

## Legality, responsibility and the licence

### Is this legal?

Nobody here can answer that for your jurisdiction, and this page is not legal
advice. What can be said precisely is what the software does and where the
responsibility sits.

This is a client. It runs on your machine, under your account, and makes requests
to Douyin and TikTok as a browser would. It publishes nothing, it is not a
service anyone else can use unless you open it, and it ships with every endpoint
requiring a credential (`api.public_endpoints` is empty by default). Nothing is
uploaded anywhere.

That means the whole of the responsibility is yours. Concretely:

| Your responsibility | What it means in practice |
| --- | --- |
| The platforms' terms of service | Douyin and TikTok each have their own. Automated collection is generally against them. Nobody in this project can waive that for you. |
| Local law | Data protection law where you are may apply to what you archive: an archived post carries another person's name, avatar, text and images. Some jurisdictions treat that as personal data regardless of it being public. |
| Redistribution | Downloading a video does not make it yours. The archive and the media volume are for you; publishing what is in them is a separate act with separate consequences. |
| Not using it against people | Following, snapshotting and re-collecting one person on a timer is a capability this software gives you. Whether that is research or harassment is decided by you, not by the tool. |

The README states the same thing in one paragraph, deliberately. The project's
position is that a self-hosted tool cannot enforce any of this, so it does not
pretend to — what it does instead is make the collection visible: every request
is logged, every setting change is audited, and no collection targets an author
or a post you did not name. The outbound work that runs on its own is minting
identities into the pool, probing the proxies you configured, re-checking
whether the things you already archived are still up, and re-collecting the
watchlist entries you added yourself.

### Can I use it commercially?

Yes. The licence is [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0),
which permits commercial use, modification, redistribution, and inclusion in
closed-source products. The grant is irrevocable. You do not need to ask, and you
do not owe the author anything.

What the licence asks in return:

| Obligation | Detail |
| --- | --- |
| Keep the licence and the copyright notices | Ship the `LICENSE` file, and keep existing copyright notices, with any copy you distribute (§4a, §4c). This repository ships no `NOTICE` file, so the `NOTICE` clause (§4d) has nothing to carry. |
| State your changes | Files you modified must carry prominent notices saying you changed them (§4b). |
| No trademark rights | The licence grants copyright and patent rights, not the right to use the project's name or logo to endorse your product (§6). |
| The patent grant is conditional | Apache-2.0 gives you a patent licence (§3), and it terminates if you initiate patent litigation alleging that this software infringes (§3, final sentence). |
| No warranty, no liability | It is provided "as is" (§7, §8). If it breaks your deployment, gets your address blocked, or loses data, that is yours to carry. |

Two things this licence does **not** do, and both matter here:

- **It is not permission from the platforms.** Apache-2.0 is a grant from the
  author over the author's code. It says nothing about Douyin's or TikTok's
  terms, and it cannot.
- **It is not a support contract.** Nobody owes you an answer; see "Getting
  help, reporting a bug and sponsoring" below for what does work.

The author has a request attached to the commercial permission — if you make
money from this, consider sponsoring rather than only taking. It is written in
the README and shown on the console's About page as a request, explicitly
labelled as separate from the licence, because running the two together would
overstate the licence and understate the ask. Declining it costs you nothing.

## Accounts, cookies and bans

### Do I need cookies from my own account?

No, and the default configuration never asks for one.

The instance maintains its own pool of **guest identities**: a headless browser
loads the platform through a proxy you configured, lets the platform issue its
own cookies to that session, and records the fingerprint the browser actually
presented. `ttwid` is the only cookie this build refuses to work without, and a
platform hands it to any visitor. No login is involved anywhere in that path.

There are exactly two situations where your own cookies come into it:

- **You are running without the `browser` profile.** No browser container means
  no minting, so the pool lives on jars you paste in. That jar can come from a
  **signed-out** browser window and still work — it will carry `ttwid`, and
  usually `s_v_web_id`, `odin_tt` and `msToken` too, which is a perfectly good
  guest identity. Import it from the console's Identities page or with
  `POST /api/v1/admin/identities/import`.
- **You want content that is only visible to a logged-in account** — your own
  private posts, a following list, anything the platform gates. Then you import
  that account's jar, and every request that needs it has to be *pinned* to that
  identity, because an answer served from a substitute session would not be the
  answer you asked for.

See [Identities and proxies](./06-identities-and-proxies.md) for both flows.

### Will my account be banned?

If you do not import an account, there is no account to ban. That is the point of
the guest pool: when an identity gets refused it cools off, and if it keeps
failing it stops counting toward the pool level (`pool.max_fail_streak`, default
3), so the filler mints a replacement instead of counting a dead identity as
capacity. Retiring the dead one is a manual act — nothing auto-retires — but the
cost of a burnt guest identity is a few minutes of minting.

If you *do* import a logged-in jar, then that account is what is exposed, and the
project is explicit about it in the import dialog:

- **A logged-in cookie set is equivalent to the account password.** It is stored
  encrypted (AES-256-GCM, keyed by `DTK_SECRET_KEY`) and no endpoint returns it
  by accident — revealing it needs the `operator` role plus the `admin` or
  `identity:manage` scope, and writes an audit line — but an administrator of
  your instance can use it.
- **Use an account kept for this purpose, never your main one.**

What the software does to keep any identity out of trouble, guest or not:

| Mechanism | Effect |
| --- | --- |
| One in-flight request per identity | An identity never has two requests open at once, because a real session does not. |
| Per-(identity, endpoint) token bucket | One identity cannot spend its whole allowance on the single most sensitive endpoint. |
| Cooldown on a risk-control answer | `sched.cooldown_base_seconds` (60s) doubled per consecutive failure and multiplied by the endpoint's risk weight, capped at `sched.cooldown_max_seconds` (21600s = 6 hours). |
| Never recombining the parts | A cookie jar is never moved to a different proxy or paired with a different User-Agent. Rotating cookies over one exit address is a louder signal than plain request frequency. |
| Circuit breaker | When an endpoint is failing across several identities, requests to it stop rather than burning the rest of the pool. |

None of that is a guarantee. Neither platform publishes its rules, both change
them, and any tool claiming your account is safe is guessing. The honest summary:
guest identities are cheap and designed to be burnt; a logged-in identity is
not, so use a throwaway account, give it its own proxy, and watch the Identities
page for cooldowns.

### Somebody else has an account on my instance — can they use my imported jar?

Only if you let them. The pool is instance-wide and its rows have no owner, so
naming an identity in a request (`?identity=<uuid>`) is gated on the `operator`
role *and* the `admin` or `identity:manage` scope. A plain read key cannot reach
a logged-in session, and a pinned request is never written to the shared response
cache — a session-scoped answer must not become readable by another caller. See
[Users and API keys](./09-users-and-api-keys.md).

## Proxies, identities and throughput

### Do I need proxies?

No, and you will probably want them anyway.

With no proxy rows configured, the pool filler mints on the host's direct
connection. That works, and it is what a user without proxies asked for. It also
means every identity you own sits behind one address — which is precisely the
recombination the rest of the design refuses to do. It is the configuration most
likely to get your address noticed, and it is your call.

With proxies configured, the filler binds each new identity to an egress that no
live identity of that platform is already using, and **waits rather than doubling
up** when every proxy is taken. So the number of proxies is effectively the
ceiling on the size of your pool.

Two consequences worth knowing before you buy any:

- A dead proxy **cools** the identities behind it rather than re-homing them.
  The cookies are fine; only the exit is down. A permanently dead proxy
  eventually means retiring its identities.
- Country and time zone are not decoration. They are handed to the browser as a
  geo hint when minting, so the identity's language and clock line up with where
  its traffic appears to come from. A proxy in Germany paired with an
  `Asia/Shanghai` clock is a contradiction given away for free.

### How many identities do I need?

The defaults are `pool.min_size` 3 and `pool.target_size` 8, **per platform**.
The filler starts topping up when the usable count drops below the minimum and
stops at the target, so a healthy pool sits between them rather than being
re-triggered every minute.

What an identity is actually worth is set by the token bucket, which is per
(identity, endpoint):

| Endpoint | Burst | Refill | Sustained, one identity | Sustained, 8 identities |
| --- | --- | --- | --- | --- |
| `*.content_detail` | 5 | 0.30/s | 1 per 3.3s | ~2.4/s |
| `*.author_profile` | 4 | 0.20/s | 1 per 5s | ~1.6/s |
| `*.comments`, `*.comment_replies`, `*.mix_posts` | 3 | 0.15/s | 1 per 6.7s | ~1.2/s |
| `*.author_posts`, `*.author_likes` | 3 | 0.12/s | 1 per 8.3s | ~1/s |
| `tiktok.author_followers`, `tiktok.author_following` | 3 | 0.12/s | 1 per 8.3s | ~1/s |
| anything unregistered | 3 | 0.15/s | 1 per 6.7s | ~1.2/s |

Those right-hand figures are ceilings under perfect spread, not promises: an
identity also holds an exclusive lock for the duration of each request, so a slow
upstream lowers them.

Work backwards from what you actually need. If you parse a few dozen links a day,
the defaults are already generous and you can drop `pool.target_size`. If you are
walking an author's whole feed, `author_posts` at one request per 8.3 seconds per
identity is your real constraint, and the only way to go faster is more
identities — which means more proxies. Raising the bucket is not the knob; it is
the thing protecting the identities you have.

### Why is the pool taking so long to fill?

By design. The filler mints **at most one identity per tick**, a tick is 60
seconds, and that one mint is shared across platforms rather than granted to each
of them: every tick picks the single neediest pool. A deployment starting from
empty therefore needs roughly 16 ticks — about a quarter of an hour — before both
pools sit at a target of 8, plus however long each mint takes. It holds a
cross-process Redis lock while minting so `--scale worker=N` cannot turn one mint
into N, and it backs off exponentially when minting keeps failing (60s doubling
to a 3600s ceiling).

Five fresh visitors appearing from one deployment inside a second is itself a
stronger signal than anything those identities would go on to do. Nothing waits
on minting — it is off the request path entirely, so a slow refill is not a slow
API, it is a smaller pool until it finishes.

There is one deliberate exception: if every identity that exists is failing, the
filler **holds the level** instead of minting. Everything failing at once is a
platform-wide event, and minting into it just feeds fresh identities to whatever
is burning the others.

## What it can fetch, and what it cannot

### Does it support Bilibili, Xiaohongshu, Kuaishou or Weibo?

No. v5 supports two platforms, `douyin` and `tiktok`, and that is the whole of
the `Platform` enum in `src/dtk/core/types.py`.

V4 did support Bilibili. v5 dropped it rather than
carrying it forward, because a platform in v5 is a much larger commitment than a
crawler file: it needs its own signature implementation, its own parameter
builders, its own parser producing the shared normalized model, its own endpoint
policies for the token bucket, and its own entries in the outcome classifier.
Half a platform — one that fetches but whose failures are misclassified — would
poison the health data every other platform depends on.

If you want to add one, [Contributing](./16-contributing.md) describes what the
work involves.

### Can it download without a watermark?

Yes, when the platform serves a stream without one. The parser puts the
watermark-free stream first in the media manifest and the downloader takes the
first stream, so the default path gets the clean file. Douyin's separate
"download address" carries a burned-in watermark; it is kept as an additional
stream flagged `watermark: true` rather than being thrown away, and nothing
reorders the list by bitrate — doing so would quietly prefer a larger file with a
watermark on it.

To be exact about what that means: nothing here removes a watermark. It selects
the stream the platform already publishes without one. If a post has only a
watermarked stream, that is what you get.

### What can it actually fetch?

A link, a share message with text around it, a short link, or a bare post id, for
either platform — plus author profiles, an author's posts and likes, comments and
their replies, mixes/collections, and on TikTok followers and following. The
Playground page and the REST API expose the same set. See
[Playground and tools](./07-playground-and-tools.md) and the
[REST API guide](./11-api.md) for the full list, and
[Concepts](./04-concepts.md) for why some of them cost more identity budget than
others.

### Why does a link that opens fine in my browser answer `NOT_FOUND`?

Because the platform said so. Douyin answers a post that does not exist with HTTP
200, a null payload and a reason code; TikTok answers a deleted post with two
different status fields. Both are classified as `business_error`, which costs the
identity nothing — the alternative, treating them as risk control, is how one
typo'd id used to cool a perfectly good identity and, repeated, trip the circuit
breaker for everyone.

If it opens in your browser but not here, the usual causes are: it is visible
only to a logged-in account (import a jar and pin the request), or it is
region-restricted for the exit your identity is behind. [Troubleshooting](./14-troubleshooting.md)
walks through telling those apart.

## Storage, disk and hardware

### Does it store video on the server?

Only if you turn that on, and it is off unless two things are true: the
`downloader` profile is running and `DTK_DOWNLOADER_URL` points at it. Without
them, `POST /api/v1/downloads` answers `501` with a message saying how to enable
it, and nothing else changes.

Parsing itself stores **metadata, not bytes**. The response carries the platform's
own CDN URLs and the server never relays them — proxying video traffic through
the instance is explicitly out of scope. What the archive keeps is the parsed
post: title, author, tags, counters, and the media manifest with those URLs in
it. Those URLs are signed and expire, which is why a download re-parses a post
whose stored mirrors are older than `media.mirror_max_age_seconds` (600s).

When the downloader *is* enabled, files land on a Docker volume as
`<platform>/<author>/<content id>/` with a `meta.json` beside them, and the API
can hand one back to a browser from `GET /api/v1/downloads/{download_id}/files/{name}`
with the `media:read` scope.

### How much disk does it need?

| Thing | Size | Notes |
| --- | --- | --- |
| Images, core stack | ~4.5 GB | `timescale/timescaledb-ha:pg17` is ~3.9 GB of that |
| Images, with the `browser` profile | ~6.3 GB | The browser image is ~1.7 GB |
| Stored media | `media.max_bytes`, default 2 GiB | Past it, the oldest unpinned downloads have their files removed and a `media_evicted` alert says what went. `0` disables eviction |
| One media file | `media.max_file_bytes`, default 512 MiB | Counted on bytes actually written, not on what the server claims |
| Postgres volume | Grows with what you keep | See below |

The database is the part that grows on its own. Three things decide how fast,
and a fourth that reads like a retention knob decides nothing:

- `retention.request_log_days` (14) trims the one-row-per-request log.
- `archive.store_raw` (**off**) would additionally keep each platform's untouched
  payload. It is the largest single thing this instance can choose to store,
  which is why it is off.
- `audit_log` is never trimmed by anything, on purpose. It grows for the life of
  the instance, slowly.
- `retention.content_days` (**0 = never**) is declared for the archive and shows
  up on `/settings`, but **no code reads this key today**, so the archive is
  never pruned automatically, whatever you set it to. The "never" default is
  deliberate — the archive exists precisely to outlive the platform — and an
  instance you leave running for a year keeps every post it ever parsed. To
  remove archived posts, delete them in the
  [Library](./08-downloads-and-library.md).

Nothing deletes to save space except media eviction. Instead, disk pressure is
handled by standing down: past `capacity.warn_percent` (80) you get a warning,
and past `capacity.hard_stop_percent` (92) background collection and new download
jobs pause while interactive reads carry on. Turning a full disk into "my API is
down" would be a worse outage than the one being prevented. See
[Operations](./10-operations.md).

### Can I run it on a Raspberry Pi or a NAS?

The core stack, plausibly. The browser container, no.

What runs comfortably in 2 GB of RAM — the measured figures at rest are postgres
112 MiB, api 104 MiB, worker 71 MiB, redis 8 MiB. What does not is `browser-rpc`:
it is capped at 4 GiB and 4 CPUs in the compose file and was measured warm at
2.57 GiB and 629% CPU, and its browser profiles live on tmpfs (3 GB on `/tmp`,
1 GB on `/profiles`), which is RAM rather than disk.

So the realistic small-hardware deployment is: core stack only, no `browser`
profile, identities imported by hand from a browser on your laptop. That is a
supported mode, not a broken one — `DTK_BROWSER_RPC_URL` unset means the minting
job is skipped entirely rather than logging an error every minute.

On architecture: nothing in the code is x86-specific, and the one dependency that
could have been a problem — `wreq`, which does the TLS emulation — publishes
`manylinux` aarch64 wheels. The images are built locally on your host, so there
is no multi-architecture registry to worry about, and the pinned Postgres image
publishes both `arm64` and `amd64`. If you change that pin, check the new one
the same way:

```bash
docker manifest inspect timescale/timescaledb-ha:pg17 | grep architecture
```

Also budget the disk: ~4.5 GB of images before any data is not nothing on an SD
card, and a database on an SD card is a bad idea for unrelated reasons.

## Network, privacy and what leaves your machine

### Does it work outside China? Does Douyin need a Chinese exit?

There is no region gate anywhere in this code. Which exits a platform accepts is
the platform's decision and it is not documented by them, so the project does not
make a promise about it either way.

What the software gives you to work with:

- Identities are per platform, and each is bound to one egress. Nothing stops you
  from putting your Douyin identities behind exits in one region and your TikTok
  identities behind exits in another — that is the normal configuration for a
  host that cannot reach both networks equally.
- A proxy's country and time zone are handed to the minting browser as a hint, so
  the identity's locale matches its exit. `browser-rpc` also probes
  `DTK_BROWSER_GEO_PROBE_URL` (`https://ipinfo.io/json` by default) *through the
  proxy* to learn the exit country before creating a context, falling back to
  `DTK_BROWSER_DEFAULT_COUNTRY` (`US`).
- The proxy prober sweeps every proxy every 300 seconds and writes back latency,
  exit address, country and time zone.

The way to answer the question for your host rather than in general is the
Diagnose page (`/diagnose`) or `dtk diagnose`, which walks six steps —
components, egress, proxies, pool, signing, and a smoke fetch — and tells you
which one fails.

### Does it phone home?

No, and the places it could have are each an explicit opt-in:

| Outbound request | Default | Notes |
| --- | --- | --- |
| Update check | `system.check_updates` is `false` | Documented as "outbound request; the user opts in rather than being opted in". The console's check runs from **your browser** to the GitHub releases API when you click it, never from the server |
| Sponsor logo | Served from your own instance | A 16 KB local copy, so loading the console does not tell a third party that your deployment exists |
| Geo probe | `ipinfo.io` through the identity's proxy, at mint time | Set `DTK_BROWSER_GEO_PROBE_URL` empty to disable it |
| Task callbacks | `security.enable_task_webhook` is `false` | And when enabled, the URL must be `https` and must not resolve to a private or loopback address, because a caller-supplied callback URL is a request-forgery vector |

There is no analytics, no crash reporting and no license check. Everything else
that leaves the machine goes to the platforms, through the identity you
configured, and is recorded in `request_log`.

### What does the instance expose if I put it on the internet?

By default, one published port on loopback, and every endpoint on it requiring a
credential. Publishing it more widely is an explicit act (`DTK_BIND_HOST=0.0.0.0`)
and the container does not terminate TLS. Read [Security](./15-security.md)
before doing it — particularly the sections on `api.public_endpoints`,
`security.cors_allow_origins` and `security.request_proxy`, which are the three
settings that widen exposure most.

## How v5 differs from v4, and whether there is a hosted version

### What changed between v4 and v5?

v5 is a rewrite that started from an empty branch and shares no code with V4. It
is what you get by cloning the repository:

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
```

| | V4 | v5 |
| --- | --- | --- |
| Platforms | Douyin, TikTok, Bilibili | Douyin, TikTok |
| Credentials | Cookies pasted into `crawlers/*/config.yaml`, updated by hand when they expired | A pool of identities the instance mints and maintains itself, stored encrypted |
| Failure behaviour | An expired cookie or changed algorithm failed silently until a user reported it | Per-identity health, per-endpoint circuit breaker, one structured row per request, alerts |
| Infrastructure | None required | Postgres (with TimescaleDB) and Redis |
| Web interface | PyWebIO | A React console |
| Other entry points | REST | REST, MCP, a CLI |
| Storage | None; results were transient | Content archive, counter snapshots, media downloads, collections, watchlist |
| Deployment | `pip install -r requirements.txt`, `python3 start.py` | `docker compose -p dtk -f docker/compose.yml up -d` |

The reason for the rewrite, in the maintainer's own framing: V4's problem was
never a shortage of features, it was that the API would **die quietly and nobody
would know**. v5 puts observability and self-healing ahead of features, and the
explicit non-goals are just as much part of it — no AI features, no billing or
multi-tenancy of any kind, no Kafka/Elasticsearch/MinIO/k8s, and no proxying of
video bytes through the server.

One more difference that matters if you redistribute: v5's signature
implementations were reverse-engineered from the platforms' own artifacts for
this project, specifically so the tree is clean under Apache-2.0. V4's A-Bogus
module was a port of GPL-3.0 code.

### Can I upgrade a V4 install to v5?

Not in place. They share no schema, no configuration file and no code — v5 keeps
everything in Postgres and encrypts credentials with `DTK_SECRET_KEY`, while V4
kept cookies in YAML. Treat it as a new deployment: bring v5 up alongside, and if
you want your old cookie jars, import them from the console as identities.

### Is there a hosted version?

Not from this project. There is no hosted v5, no free tier, no API key you can
buy here — the software is built to run on a machine you control, which is also
the only place your `DTK_SECRET_KEY`, your identities and your proxies should
live.

If you would rather buy data than run a scraper, the project's sponsor
[TikHub.io](https://www.tikhub.io/) sells hosted social-data APIs commercially.
That is a paid third-party service, disclosed as the sponsor here and labelled as
such in the console; it is not run by this project and this project makes no
claim about it.

## Getting help, reporting a bug and sponsoring

### How do I get support?

Two routes, in the order they should be tried:

1. **[GitHub issues](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/issues)** —
   public, keeps its own history, and can be answered by anybody who has hit the
   same thing.
2. **Email the author** — reaches one person's inbox. The address is on the
   console's About page and in the repository. Most of what arrives by email
   would have been answered faster in the tracker.

Before either, work through [Troubleshooting](./14-troubleshooting.md). A large
share of "I deployed it but I get no data" reports are a proxy, a jar, a signing
path or a network — four places the diagnostics already look.

### What should I put in a bug report?

| Include | Where to get it |
| --- | --- |
| The diagnostic report | `/diagnose` in the console, or `dtk diagnose`. It is redacted at the source: proxy passwords, cookies and API keys never enter it, which is what makes "paste this into the issue" safe advice |
| The `request_id` | `meta.request_id` on the response envelope. It is the same id written to `request_log`, which is what makes a report actionable rather than a description |
| Version and commit | The console's System page, or `dtk --version` |
| What you asked and what came back | The exact URL or call, and the full error envelope — `error.code` especially, since it is a stable enum |
| Whether the browser and downloader profiles are running | `docker compose -p dtk -f docker/compose.yml ps` |

**Never paste** a cookie jar, an API key, a proxy URL with credentials, or your
`.env`. An issue is public and a jar is a credential; a logged-in one is somebody's
account.

Worth reporting specifically, because they usually mean the platform moved rather
than that you configured something wrong: `UPSTREAM_CHANGED`, and any
`signature.refused` or `signature.rejected` rule name showing up on the Logs page.

### How do I sponsor the project?

Through [GitHub Sponsors](https://github.com/sponsors/evil0ctal),
[Afdian](https://afdian.net/@evil0ctal), [Ko-fi](https://ko-fi.com/evil0ctal) or
[Patreon](https://www.patreon.com/evil0ctal). Cryptocurrency addresses for
Solana, Tron (TRC20), Ethereum (ERC20), BNB Smart Chain (BEP20) and Bitcoin are
listed in the README and on the console's About page — copy them from there
rather than from anywhere else, and **send only on the network an address is
listed under**, because a transfer on the wrong chain cannot be recovered by
anybody.

Sponsorship of the *project* (the logo placement in the README and console) is a
separate arrangement from supporting the *person* who maintains it; the README
keeps them in separate sections for that reason.

## Glossary

Terms used throughout this documentation, in plain language. Where a term has a
precise definition elsewhere, the linked page has it.

### Identities and the browser

| Term | What it means |
| --- | --- |
| **Identity** | The unit this system schedules: a cookie jar, a browser fingerprint, a proxy binding and a failure history, bound together for the life of the row. Not a cookie — the four parts are never recombined. |
| **Cookie jar** (or just *jar*) | The platform's own session cookies for one identity, stored encrypted with `DTK_SECRET_KEY`. `ttwid` is the only one this build refuses to work without. |
| **Fingerprint** | What the minting browser reported about itself: browser family and major version, User-Agent, `navigator.platform`, screen geometry, language, time zone, CPU and memory hints. It selects the TLS profile used on the wire and is fed into the signature. |
| **Minting** | Producing a new identity by driving a real headless browser through the proxy it will be bound to, so the cookies, the fingerprint and the exit all agree. Done by `browser-rpc`, one identity per 60-second tick. |
| **Importing** | The other way to get an identity: paste a cookie jar from your own browser. Four formats are recognized — a `Cookie:` header, a cookie-editor JSON array, Netscape `cookies.txt`, and loose `key=value` lines. |
| **Egress** | The address a request leaves from: a proxy, or the host's own connection. One identity is bound to one egress for life. |
| **Guest identity** | An identity with no login on it. Cheap, disposable, and what the pool mints by default. |
| **Logged-in jar** | An imported identity carrying a real session (`sessionid`, `sid_tt`, `sid_guard`, …). Equivalent to the account's password; use a throwaway account. |
| **Identity state** | One of `minting`, `active`, `cooling`, `degraded`, `retired`. Only `active` is in normal rotation; `retired` has had its jar wiped and cannot be brought back. |
| **Cooldown** | The rest an identity takes after a risk-control answer: `sched.cooldown_base_seconds` doubled per consecutive failure and multiplied by the endpoint's risk weight, capped at `sched.cooldown_max_seconds`. |
| **Health score** | `success_rate × (1 − risk_rate) × 0.5^consecutive_fails`, in `[0, 1]`. Multiplied rather than summed so any one factor collapsing drags the whole score down. |
| **Pinning (an identity)** | Naming the identity a request must go out on. A pin is a pool of one — it never widens on a refusal, skips the response cache in both directions, and gets one transport attempt instead of three. Requires the `operator` role plus the `admin` or `identity:manage` scope (a console session is bounded by role alone). |

### Signing

| Term | What it means |
| --- | --- |
| **Signing** | Computing the extra query parameters and headers a platform requires before it will answer. Without them a request is refused, or worse, answered with an empty body. |
| **Native signer** | The platform algorithms reimplemented in pure Python (`src/dtk/signing/native/`). Microseconds per signature, no browser involved. The default (`signing.mode` = `native`). |
| **Browser signer (`rpc`)** | Signing by running the site's own JavaScript inside `browser-rpc`. Hundreds of milliseconds, but it follows the platform automatically when the algorithm changes. Where to go the day a port goes stale. |
| **`a_bogus`** | Douyin Web's current signature parameter, produced by its `bdms.js` bundle. It carries browser geometry inside the signed payload, which is why it must be computed with the same fingerprint the request is sent with. |
| **`X-Bogus`** | The older Douyin/TikTok Web signature. TikTok Web still accepts it, and so do some Douyin endpoints. |
| **`X-Gnarly`** | One of the four parameters TikTok Web's `webmssdk` appends to every API call — the per-request seal. It is an encrypted blob carrying its own key. |
| **`X-Dynosaur`** | The companion parameter to `X-Gnarly`: TikTok's environment report about the browser making the call. |
| **`x-secsdk-web-signature`** | The header Douyin requires on the subset of its API it sign-protects (14 paths, copied from Douyin's own web SDK). Computing it needs the identity's `uifid` cookie. Outside that path list, Douyin's own pages send requests unsigned. |
| **`msToken`** | A session token both platforms set as a cookie and echo in the query string. TikTok's `X-Dynosaur` and `X-Gnarly` are computed from the identity's own `msToken`, so a jar without one cannot be signed natively. |
| **`ttwid`** | The visitor id cookie. Required on both platforms; an identity without it is refused. |
| **`s_v_web_id` / `verifyFp`** | The same value in two places: Douyin's `verifyFp` query parameter is the `s_v_web_id` cookie of whatever browser computed the signature. Sign in one session and send with another's cookies and the two name different visitors. |
| **Sign-protected path** | A Douyin endpoint that the platform's own SDK signs. The list lives in `src/dtk/signing/protection.py`; everything outside it is sent unsigned by Douyin's own pages too. |

### Scheduling and failure

| Term | What it means |
| --- | --- |
| **Scheduler** | The component that answers one question — which identity may send this request right now — or refuses with a reason that is shown to the caller and recorded. |
| **Token bucket** | Per (identity, endpoint) budget: a burst size and a refill rate. Spending a token is what lets a request go out; it is refunded only for a `network_error`, because a request that never reached the platform did not spend the allowance. |
| **In-flight lock** | One per identity, across all endpoints, with a 120-second TTL. One identity makes one request at a time, because a real session does not issue parallel API calls. |
| **Quantised LRU** | The rotation order: health *tier* (5 coarse buckets), then least-recently-used with recency rounded to a quantum, then jitter. Bucketing is what stops the single healthiest identity winning every time. |
| **Circuit breaker** | Per-endpoint. It trips when the risk rate exceeds `sched.circuit_risk_threshold` (0.6) over at least `sched.circuit_min_samples` (20) observations spanning at least `sched.circuit_min_identities` (3) distinct identities in a rolling 300s window. Open for 300s, one probe per 60s. |
| **Risk control** | The platform refusing *this identity* — a captcha interstitial, a verification envelope, a 429, or a 200 with the payload withheld. It increments the identity's failure streak, applies a cooldown, and counts toward the circuit breaker. Distinct from a business error on purpose: a deleted video must never be counted against a good cookie. |
| **Outcome** | The classification of one upstream response: `ok`, `business_error`, `risk_control` or `network_error`. Every health number in the system hangs off this one decision. |
| **Reject reason** | Why the scheduler refused a lease, recorded in `request_log`: `circuit_open`, `no_identity`, `no_token`, `all_inflight`, `pinned_unavailable`, `wait_timeout`. One more value, `queue_full`, is an API-layer refusal raised before a task exists, so it reaches you in the error envelope's `details.reject_reason` and never as a `request_log` row. |
| **Coalescing** | When several callers ask for the same thing at once, the first task claims the digest in Redis (90s TTL) and the rest attach to that task instead of queuing their own. A claim left by a task that already failed is dropped rather than joined. |
| **Response cache** | Keyed on the normalized business parameters, not the raw URL, so two spellings of the same link hit the same entry. A hit costs no identity quota and is still logged. `?refresh=true` skips the read but still writes. |

### Requests, tasks and data

| Term | What it means |
| --- | --- |
| **Envelope** | The four top-level keys every response carries: `success`, `data`, `error`, `meta`. `error.code` is a stable enum you branch on; `error.message` beside it is localized and must never be parsed. (The word is also used for the platform's own JSON wrapper around a payload, as in "an intact envelope with nothing in it".) |
| **`request_id`** | The id in `meta.request_id`, also written to `request_log`. Quoting it is what turns a bug report into something someone can look up. |
| **Task** | A queued unit of work. The data endpoints are asynchronous by default: they answer `202` with a `task_id`, and you poll `/api/v1/tasks/{id}`, pass `?wait=N`, or use a callback. State is `queued → running → done \| failed`. |
| **`?wait=N`** | Asks the server to hold the connection until the task settles, up to `api.max_wait_seconds` (30). Not finished in time is `202` and the task id — not an error, and nothing was lost. |
| **Cursor** | An opaque string for the next page of a list. The value underneath is a millisecond publish timestamp on some endpoints and a plain offset on others, on both platforms; the encoded string hides which, so callers never need to know. |
| **`raw`** | The platform's untouched payload, kept on every parsed model so new fields can be back-computed later. Excluded from API responses unless asked for, and stored in the archive only when `archive.store_raw` is on. |
| **Archive** | The record of every post this instance has parsed, as structured rows in Postgres — not files. It is what makes a post deleted upstream still readable here. |
| **Snapshot** | One observation of a post's or author's counters at a moment in time (`content_snapshots`). A series of them is what turns "1.2M plays" into a growth curve. |
| **Availability** | The archive's answer to "is this still up": `live`, `deleted`, `private` or `unknown`. `unknown` is what a failed check leaves behind, deliberately not `deleted`. |
| **Library** | The console page over the archive. It answers from Postgres, so browsing it spends no identity and fetches nothing. |
| **Collection** | A named set of archived posts, made by hand — "things I am keeping", "for the edit". The one grouping in the library that is not derived from the post itself; nothing infers membership. |
| **Watchlist** | Entries that re-collect an author or a post on a timer, turning snapshots into a real time series. Interval floor `watchlist.min_interval_seconds` (900s), default `watchlist.default_interval_seconds` (6 hours). |
| **Download** | A request to store one post's media on your disk, and the row that records it. Callers never supply a media URL — a download names a post the instance has already parsed. |
| **Pinning (a download)** | Marking stored files exempt from eviction. Nothing sets it automatically, and it is the only exemption; a size-based policy without one eventually deletes the file somebody meant to keep. |
| **Eviction** | Removing the files of the oldest unpinned downloads once the media volume passes `media.max_bytes`. The row, its digests and its file list survive with `files_removed_at` set, so "collected and later cleaned up" stays distinguishable from "never fetched". |

### Access and integration

| Term | What it means |
| --- | --- |
| **Role** | What a console account may do: `viewer` < `operator` < `admin`, a ladder rather than a set. |
| **Scope** | What an API key may do: `douyin:read`, `tiktok:read`, `archive:read`, `archive:export`, `media:read`, `media:write`, `identity:manage`, `admin`. A key is bounded by its scopes **even when it belongs to an administrator**. |
| **API key** | The credential a script or agent sends in `X-API-Key` or `Authorization: Bearer`. Shown once at creation; only a prefix and a SHA-256 digest are stored. May carry its own per-minute rate limit, otherwise `api.default_rate_limit_per_min` (120) applies. |
| **Rate limit** | Abuse protection, never metering and never billing. Its only purpose is stopping one runaway script from draining the identity pool. |
| **Setup token** | The one-time token printed in the API log on a fresh instance, used to create the first administrator. Lives 24 hours in Redis, survives four wrong attempts — the fifth deletes it — and `/setup` closes for good once an account exists. |
| **MCP** | Model Context Protocol — how an AI client (Claude Code, Claude Desktop, Codex CLI, …) discovers and calls tools. This instance serves it at `/mcp/` from inside the API process, with 8 read-only tools and no tool that touches a credential. See [MCP and AI agents](./12-mcp.md). |
| **`explain`** | An option on a fetch that hands back the request exactly as it went out — signed URL, headers and jar. It returns a credential, so it needs the `operator` role plus the `admin` or `identity:manage` scope (a console session is bounded by role alone), and is audited. |
| **Diagnose** | The six-step self check (components, egress, proxies, pool, signing, smoke) in the console and as `dtk diagnose`. Its report is redacted at the source, which is what makes it safe to paste into an issue. |

### Platform identifiers

| Term | What it means |
| --- | --- |
| **`aweme_id`** | Douyin's post id, a 19-digit number. Handled as a string everywhere, because it exceeds JavaScript's safe integer range and an int would silently lose precision in the UI. Douyin's comment-replies endpoint spells the same value `item_id`; its comment list still calls it `aweme_id`. |
| **`content_id`** | This project's neutral name for a post id, on either platform. |
| **`sec_user_id`** | Douyin's stable author key, and what the normalized model stores as the author's `uid`. Douyin's own mutable `uid` is deliberately not used, and neither is the handle. |
| **`secUid`** | TikTok's opaque author key, which its own endpoints take alongside or instead of the handle. The normalized model stores TikTok's numeric id as the author's `uid` and preserves `secUid` in `raw`; a URL-shaped profile lookup uses the `@handle` (`uniqueId`), because that is what TikTok's user-detail endpoint accepts without a prior fetch. |
| **`unique_id`** | The `@handle`. Never used as a key, because users edit it. |
| **Content kind** | `video`, `image_album` or `live`. Only the detail response settles which — a URL shape is a hint, never authoritative. |

## Where to go next

- [Quick start](./01-quickstart.md) — the shortest path to a working instance
- [Concepts](./04-concepts.md) — the long form of most of this glossary
- [Configuration](./03-configuration.md) — every setting named on this page, with its scope and default
- [Identities and proxies](./06-identities-and-proxies.md) — minting, importing, binding, retiring
- [Troubleshooting](./14-troubleshooting.md) — when something specific is broken
- [Security](./15-security.md) — what the defaults protect you from, and what they do not
- [Contributing](./16-contributing.md) — including what adding a platform actually involves
