# Downloads, library and watchlist

> **[Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)** —
> a self-hosted Douyin and TikTok data API: REST, MCP and a web console, with
> an identity pool that maintains itself.
> [All docs](../README.md) · [中文](../zh/08-downloads-and-library.md)

This page is about keeping things rather than just reading them: putting video and images on your own disk, finding them again months later, and collecting the same target on a timer. After reading it you will know what each of the three pages costs, where the bytes actually go, what the size ceiling will take away from you, and how to stop it taking the one post you meant to keep.

## The three pages at a glance

The console splits "what I have seen" from "what I am holding" from "what I keep collecting", and they are separate for a reason: the first is free, the second fills a disk, and the third spends the identity pool forever.

| Page | Path | What it holds | What it costs |
| --- | --- | --- | --- |
| Library | `/library` | The archive: one row per post this instance has ever parsed — title, author, tags, the media manifest | Nothing. It answers from Postgres; no identity is spent and nothing is fetched |
| Downloads | `/downloads` | The download index: one row per request to store a post's media, plus what landed on disk | Disk, and one identity-backed request per post that has to be fetched first |
| Watchlist | `/watchlist` | Standing instructions: collect this author or this post every N hours, forever | The identity pool, on a schedule, for as long as the entry exists |

All three live in the **Tools** group in the sidebar. A post can be in the archive without its media being on disk (the usual case), and a download can outlive its archive row — the downloads table still describes a real directory on the volume even if the post was later deleted from the library.

## The media downloader is optional

Storing bytes is done by a separate container, the `downloader` sidecar. It is an opt-in Compose profile, and an instance without it is correctly configured, not broken — the archive, the API, the console and the watchlist all work unchanged. Only the byte-fetching half is missing.

Start it, from the repository root:

```bash
echo 'DTK_DOWNLOADER_URL=http://downloader:9100' >> .env
docker compose -p dtk -f docker/compose.yml --profile downloader up -d --build
```

Without `DTK_DOWNLOADER_URL`, `POST /api/v1/downloads` answers `501` with `NOT_CONFIGURED` and the message *"this instance has no media downloader; start the downloader compose profile and set DTK_DOWNLOADER_URL"*. The Downloads page shows the same thing as a card at the top and keeps working as a read-only index of whatever was stored earlier.

The sidecar is the container that dials arbitrary CDN hosts, so it is deliberately the emptiest one in the stack: a `scratch` image holding one static Go binary, no shell, no package manager, a read-only root filesystem, all capabilities dropped, `no-new-privileges`, and exactly one writable path. It is on the `edge` network only — it has no route to Postgres or Redis — and Compose blanks `DTK_SECRET_KEY`, `DTK_DATABASE_URL` and `DTK_REDIS_URL` for it even though the shared env file carries them. It is never handed a cookie, a proxy, an API key or a database address; it receives a list of mirrors, the host allowlist for that one platform, and a byte ceiling.

Its own limits, all read from `.env`:

| Variable | Default | What it does |
| --- | --- | --- |
| `DTK_DOWNLOADER_URL` | *(empty)* | Read by `api` and `worker`. Empty disables media downloads entirely |
| `DTK_DOWNLOADER_TOKEN` | *(empty)* | Shared secret. Empty means the sidecar checks nothing; it listens on an internal network no other container is on, so this is defence in depth rather than the boundary |
| `DTK_DOWNLOADER_WORKERS` | `4` | Jobs pulled in parallel |
| `DTK_DOWNLOADER_ITEM_WORKERS` | `4` | Files pulled in parallel *within* one job |
| `DTK_DOWNLOADER_QUEUE` | `64` | Jobs it will accept before answering "the download queue is full" |
| `DTK_DOWNLOADER_TIMEOUT_SECONDS` | `900` | Ceiling on one transfer |
| `DTK_DOWNLOADER_HISTORY` | `500` | Finished jobs kept in memory for progress reporting |
| `DTK_DOWNLOADER_MAX_REDIRECTS` | `5` | Redirect hops a mirror may take |

The ceiling on concurrent transfers is the product of the two worker counts — 16 with the defaults. Raise them for a fast connection; they cost goroutines and a 32 KiB buffer each, not much else. The container is capped at 512 MB of memory and 2 CPUs because transfers are streamed, so resident memory is a handful of buffers rather than the size of the files.

The job history is in memory and is lost on restart. That has one visible consequence: after restarting the sidecar, live progress bars for jobs it has forgotten disappear, and the row draws without one. The durable record is written when a job settles, so nothing is lost except the bar.

## Where the files land

Files go to the named Docker volume `media-data`, mounted at `/var/lib/dtk/media`. The downloader mounts it read-write and is the only thing that writes to it; the `api` container mounts it **read-only**, which is how the console can hand you a stored file without the downloader ever becoming a relay. The path inside the containers is fixed — if you want the media somewhere else on the host, change the volume definition in `docker/compose.yml`, not a setting.

The layout is one directory per post:

```
/var/lib/dtk/media/<platform>/<author_uid>/<content_id>/
├── video.mp4        # or .mov, .webm, .m4v
├── cover.jpg        # one, never two
├── image-01.jpg     # image posts: one file per slide
├── image-02.jpg
└── meta.json
```

`meta.json` is written beside the files and is the reason a folder found two years later still says what it is. It carries the platform, post id, kind, `web_url`, title, description, publish time, duration, author uid and nickname, music, tags, location and the file list. It deliberately carries **no** signed CDN URLs — those expire within hours and would be the one misleading part of it.

What the planner decides before anything is fetched:

- **One video per post.** Every bitrate the platform offers is treated as a mirror of the same file — the first that lands wins. Storing four copies of one clip is not a feature. The parser's order is kept, and it puts the watermark-free stream first.
- **One cover**, added last, and only if the post has not already hit the item cap.
- **At most 64 files** per post. A Douyin image album tops out around 35 slides; the cap guards against a malformed payload becoming a thousand-item job.
- **Images are capped at 32 MiB** each, or `media.max_file_bytes` if that is lower. A 500 MB "image" is a sign something is wrong.
- **Extensions are taken from the URL only when recognised** — `mp4`, `mov`, `webm`, `m4v` for video and `jpg`, `jpeg`, `png`, `webp`, `heic`, `gif` for images — otherwise the kind's default is used. A query string ending in `.php` cannot name a file on your disk.
- **Every mirror is checked against the media host allowlist** before a job exists, and anything dropped is reported back as a `skipped` reason.

A transfer in flight is written to `<name>.part` and renamed into place when it completes, so a name only ever appears when the file behind it is whole. A `.part` counts toward the volume total while it exists.

## Starting a download

The Downloads page has a mode tab row at the top. Each mode takes a different shape of input, which is why they cannot share one submit button — a post link and a profile link are different things, and a form that guessed would quietly download the wrong one.

| Mode | Takes | Result |
| --- | --- | --- |
| A post | One share link, the whole share caption around one, or a bare post id | The post's media on the server's volume |
| Many posts | A paste of many links | A parse result per link, which can then be saved in one go |
| Comments | A post link or id | A JSON file **in your browser** — comments are data, not media |
| An author's posts | A profile link or a bare author id | One queued download per post from their feed |

The **Skip what is already downloaded** checkbox is shared by every mode that can act on more than one thing (all but Comments). It hands back the copy this instance already has instead of fetching the post again — which is what re-running a feed wants: the author added three posts and the other forty are here already. An evicted copy does not count; there are no bytes left to skip.

### One post

Paste a link or a post id. Digits are treated as an id, anything else as a link. The platform menu beside the box is only consulted for a bare id and is disabled while a link is in the box, because the link says which platform it is — and if you send both and they disagree, the API refuses rather than guessing.

Three things can happen, and the toast tells you which:

- **Started.** The post was already in the archive, the plan was built from the stored mirrors, and a job is queued. The response is `202` with a `download_id` and the `task_id` running it.
- **Fetching the post first.** This instance has never seen the post. The worker fetches it through the same pool, scheduler and request log as any other read, then downloads what comes back. Slower first response; you do not have to parse it yourself first.
- **Already downloading / already downloaded.** The request was answered with an existing download (`reused: "in_flight"` or `"already_stored"`, HTTP `200`). Joining an in-flight download is **never optional**, whatever `skip_existing` says: two downloads of one post write into the same directory and the loser of that race renames a file the winner has already moved, and comes back `partial` with a file missing.

Short links (`v.douyin.com/...`, `vm.tiktok.com/...`) are refused here, by design: resolving one means following it, and this endpoint makes no network calls. Send them to `POST /api/v1/parse` — which expands them in the background — and the post is archived by the time you come back. The **Many posts** mode accepts short links directly, because it goes through the parse pipeline.

If the post is archived and has nothing downloadable, you get a `400` with the reasons now, rather than a task that fails a minute later.

### Many posts (batch parse and download)

This mode replaces the one-line form with a textarea, its own results table and its own toolbar. Paste up to **500** links; they are submitted to the parse pipeline in chunks of **50**.

The input you actually have is the thing this is built around. A Douyin share caption looks like `2.84 nqe:/ <title> https://v.douyin.com/L4FJNR3/ <sentence>` — one link surrounded by prose. Splitting that on whitespace produces four rows, three of them nonsense. Instead, as you type (after a 300 ms pause) the paste is sent to `POST /api/v1/tools/parse-batch`, which extracts URLs using the same host allowlist the rest of the system uses and reports what it found. That endpoint touches no network and spends no identity; it accepts up to 512 KiB of text and returns at most 1000 items, saying `truncated` when it dropped some.

Each line comes back as one of:

| Kind | Submittable | Meaning |
| --- | --- | --- |
| `link` | yes | A recognised Douyin or TikTok URL |
| `short_link` | yes | A share link that needs following; the parse pipeline expands it |
| `content_id` | no | A bare post id with no platform attached to it |
| `bad_id` | no | Looks like a post id but cannot be one — both platforms mint ids whose high bits are the second they were issued |
| `unknown` | no | No Douyin or TikTok link in this line |

The rejected kinds are answered locally, without spending a task, and appear in the table as failed rows with a reason.

Submission is one request per chunk; **tracking is per row**. Each row then lives its own life — its own state, its own error, its own retry — because a synchronous bulk endpoint would smear one dead link into a single error for the whole paste. Rows are polled every 1.5 seconds, 8 at a time, round-robin so the tail of a long paste is not starved.

The results table columns are: state, link, title, author, platform, kind, content id, plays, likes, and the error code. Comments, shares and published time are available but hidden by default — use the table's column chooser. Clicking a finished row opens a drawer with the video player, the images, the counters, per-file download buttons and the raw result payload.

The toolbar:

- **Save N to this instance** — makes exactly the same request the single-post mode makes, once per row, so these rows land in the archive with the same progress, retry, dedupe and skip-what-is-already-here behaviour as everything else. One failure does not abandon the rest; the toast reports how many were queued, how many were already here and how many could not be queued.
- **Export CSV** — one row per parse result, saved as `dtk-parse-<timestamp>.csv`. The columns are `url`, `state`, `error_code`, `platform`, `kind`, `content_id`, `web_url`, `title`, `author`, `author_uid`, `created_at`, `duration_ms`, `play_count`, `digg_count`, `comment_count`, `share_count`, `collect_count`, `video_url`. Cells that contain a comma, a quote or a newline are quoted, and a leading `=`, `+`, `-` or `@` is prefixed with an apostrophe so a spreadsheet cannot execute it.
- **Export JSON** — the same rows with the full result payload, as `dtk-parse-<timestamp>.json`.
- **Retry failed (N)** — retries only the rows whose error code is not permanent. A dead link is not worth another identity.
- **Clear** — empties the table. Nothing on the server is touched; these rows are browser state and do not survive a reload.

The drawer's own download buttons are a **different thing** from "Save to this instance": they fetch from the platform CDN straight to your browser, so the file lands in your downloads folder and the server never relays media. Mirrors are tried in order. Some CDNs refuse cross-origin reads, and when all of them do, the first mirror is opened in a new tab and the toast says so — that is CORS or hotlink protection, not a dead file.

### An author's recent posts

Paste a profile link, or the bare `sec_user_id` the library and playground hand you (both platforms spell it the same way: it starts `MS4wLjABAAAA`). The **How many posts** menu offers the newest 20, 50, 100, 200, or everything the feed will give up. It is a menu rather than a number field on purpose: every post is a download, every page of the feed is a read through the identity pool, and a free-text depth invites "99999" typed without a thought.

The page walks the feed 20 posts at a time until it has enough, stopping at 40 pages, then queues one download per post. Failures on individual posts do not abandon the rest, and the toast reports queued / total / already here.

One trap worth knowing: the platform menu is **disabled** in this mode, because a profile link carries the platform. If you paste a bare author id instead, it is looked up on whichever platform the menu was left showing — so for a bare TikTok `secUid`, paste the profile link instead.

For deeper history than the feed will hand over in one pass, use **Archive their history** in the library's post drawer, which is a different job with a different cost.

### A post's comments

Reads up to **5 pages of 50** comments through the ordinary read endpoint and hands your browser a JSON file named `comments-<platform>-<id>.json`. Each page is a real request through the identity pool. Comments are data rather than media, so nothing is written to the server's volume and the downloader is not involved.

## The downloads table

| Column | What it shows |
| --- | --- |
| Post | The stored cover as a thumbnail, the archive's title, and the author and platform beneath. Falls back to the content id when the archive knows nothing about the post |
| State | `queued`, `running`, `done`, `partial`, `failed` or `cancelled`, plus a live progress bar while it is running |
| Platform | `douyin` or `tiktok` |
| Files | How many files completed |
| Size | Bytes on disk, or **Cleaned up** for a row whose files were evicted |
| Finished | When it settled |
| Keep | The pin toggle |
| Retry | Present only on a settled row that is worth retrying |

`partial` is its own state rather than a success or a failure: a post whose video landed and whose third image 404'd is neither, and calling it `done` would hide the gap while calling it `failed` would hide a video that is on the disk.

**Filtering** by state and by platform happens on the server, so "the most recent TikTok downloads" means the most recent of all of them and not the most recent of whatever page happened to load. **Sorting**, by contrast, is done in the browser over the rows already loaded — the default is Finished, newest first. The list returns 100 rows per request (`GET /api/v1/downloads` defaults to 100, maximum 500) and the console does not paginate it, so on a large instance the table is a window on the newest hundred.

The table polls every 5 seconds while anything is in flight and every 10 seconds otherwise. The progress bar is read live from the sidecar for at most 8 in-flight rows at a time, and it counts **files, not bytes**: the platform declares a size and the sidecar does not trust it — the ceiling is enforced on bytes actually written — so there is no denominator to divide by. The bytes transferred so far are shown beside it as the number that keeps moving.

Clicking a row opens a drawer with the player, the per-file list, each file's SHA-256, and a save link per completed file.

## Retrying, and why there is no resume

Retry is offered on `failed`, `partial` and `cancelled` rows. It calls `POST /api/v1/downloads/retry` with the `download_id` and starts the same post over from scratch, re-parsing the post first if the stored mirrors have gone stale (older than `media.mirror_max_age_seconds`, 600 seconds by default).

There is no resume, and there could not usefully be one. The sidecar writes to `<name>.part` and truncates it on every attempt, because media URLs are signed and expire: bytes fetched an hour ago cannot be continued against a link that now answers `403`. Asking again is what works.

Retry is refused while a download is still running — a second attempt would race the first into the same directory. A download that gets stuck is failed by the maintenance pass **two hours** after it was created (the pass runs every five minutes), after which the row is retryable. Nothing on disk is touched by that: files that did land stay where they are.

## Pinning and the size ceiling

This is the part that deletes things, so it is worth reading before you need it.

Stored media is capped by `media.max_bytes`, **2 GiB by default**. When the volume goes over it, the maintenance pass removes stored downloads oldest-first until it is back under. What that pass does, precisely:

- The total comes from **the volume**, measured by the sidecar, not from the database. Files removed by hand or restored from a backup are therefore accounted for; a sweep that trusted its own bookkeeping would delete to satisfy a number nothing else agrees with.
- **Pinned downloads are skipped, always.** This is the only exemption there is.
- Only the **files** go. The row keeps its file list, sizes and digests and reports `on_disk: false`, so the console can still tell you what was collected and that it was cleaned up. That distinction matters: "collected and later evicted" can be undone by asking again; "never fetched" cannot be undone by anything.
- Every sweep that removes something raises a `media_evicted` alert (throttled to one every six hours). It is the only notice you will get that your disk policy just ran — see [Operations](./10-operations.md) for wiring up notification channels.
- If everything above the ceiling is pinned, nothing is removed and a warning is logged saying there is nothing evictable.
- `media.max_bytes = 0` disables eviction entirely. That is a choice you are allowed to make; it means nothing is ever cleaned up.

**Pinning** is done from the Keep column, or in bulk after selecting rows. It exempts the whole download from the sweep for good. Pin anything you would be upset to lose, particularly a post the platform has since taken down — that one cannot be fetched again.

Separately from the media ceiling, a **capacity guard** watches actual disk usage on the volumes this instance can see. Past `capacity.warn_percent` (80) it raises a warning; past `capacity.hard_stop_percent` (92) new download jobs are refused with `Retry-After: 300` and scheduled collection stands down, while every read carries on. Nothing is deleted by the guard. A full disk should degrade an instance, not take it offline.

The four tiles at the top of the page report: bytes stored (against the ceiling, turning amber at 75% and red at 90%), total downloads and how many are in flight, how many are pinned, and how many have been cleaned up.

## Duplicates, cancelling, and what actually deletes bytes

**Duplicate downloads.** Re-downloading a post is normal — the first attempt failed, the mirrors went stale, the file was evicted — and each attempt leaves a row. The successful ones leave a second complete copy. The *Duplicate downloads* card runs a dry run first and shows the count in a confirmation dialog, because the count is the whole decision. Confirming keeps the newest copy of each post that still has its files and removes the rest. A directory is only deleted when no surviving row still uses it, so in the ordinary case where two attempts wrote to the same place, the records go and the file stays — which is correct, since there was only ever one copy on disk. It requires the downloader to be reachable if any directory has to be removed; otherwise it refuses rather than leaving bytes nothing points at.

**Cancelling.** `DELETE /api/v1/downloads/{id}` stops a transfer that has not finished: it tells the sidecar to cancel and marks the row `cancelled`. It does **not** delete anything. Files that already completed stay where they are, and a download that has already settled is returned unchanged. The Downloads page's bulk **Delete** button calls this endpoint once per selected row that still has files on disk (rows already cleaned up are skipped), so on rows that have already finished it does not free any bytes, despite the wording of its confirmation dialog.

**What actually removes stored files**, then, is one of three things:

1. **Deleting the post from the Library** (its bulk Delete), which removes the archive row, its collection memberships, the download records and the directories on disk.
2. **The size-ceiling sweep**, for anything not pinned. Unpinning a download and letting the ceiling reach it is a legitimate way to reclaim space.
3. **The duplicate removal** above, for redundant copies.

## Saving a stored file to your own computer

A stored file can be handed to your browser. In the downloads drawer each completed file has a **Save this file** link; in the library drawer, **Export the video file** and one link per stored image or cover.

This goes through `GET /api/v1/downloads/{download_id}/files/{name}` and requires the `media:read` scope. The endpoint fetches nothing: it rebuilds the path from the stored row — the download's own directory plus a name that has to appear in that row's file list — resolves it, and checks the result still lands inside the media root. A name that is not in the record is a `404` whether or not something of that name exists on disk. A download whose files were evicted answers `404` with `reason: "evicted"` rather than going back to the platform; ask for the download again to restore it.

The file arrives with the content type the downloader recorded and a `Content-Disposition` that renames it:

```
dtk-<platform>-<content_id>-<stored name>
```

for example `dtk-douyin-7408915107113127220-video.mp4`. On the volume every post owns a directory, so files can be called `video.mp4` and never collide. Your downloads folder has no such directory — fifty saved posts would be fifty files called `video.mp4`, the second of them `video (1).mp4`, with nothing in either name saying which post it came from. Characters outside `A-Za-z0-9._-` become hyphens and each part is capped at 64 characters. The name is produced by the server, so `curl`, the console and any other client all get the same one.

Note what this is not: the server does not proxy media on demand. Saving a file serves a copy that is already on the disk, behind an authenticated scope. "Store on this instance" and "save this file to my computer" are two different destinations, which is why the console labels them differently.

## The Library page

`/library` is the archive: everything this instance has parsed, answered entirely from local storage. Nothing here spends an identity, nothing is rate limited upstream, and a post that has since been deleted is still here with its availability saying so. That is the whole point of keeping an archive.

The tiles across the top show total posts, authors, how many posts are marked **Gone** (deleted upstream), and a count per platform. Each count carries a "N of M downloaded" footer that is also a link: clicking it filters the list to the posts whose media is on this disk. Seen and kept are different numbers, and reading the first as the second is how an archive of 66 posts gets mistaken for 66 videos on a volume.

Two views:

- **Covers** (the default) — a wall of cover tiles with duration and, for saved posts, a size chip. The cover comes from this instance's disk when the post has been stored and from the platform otherwise, in that order, because an archived `cover_url` is a CDN link that expires and may be hotlink-protected. The grid can be grouped by nothing, by author, or by the day the media was saved.
- **Table** — sortable columns for the questions a grid cannot answer: post, kind, length, availability, published, last seen.

Paging is by cursor, never by page number, because the table is written to while you walk it and an offset silently skips and repeats rows. Previous walks back through the cursors you have already visited. A page is 50 posts.

Selection is by post key and **survives a page turn** — selecting a few here, a few there, then acting on all of them is the intended workflow. A bulk bar appears at the bottom of the viewport while anything is selected.

## Filters, search and views

| Filter | Values | Applies |
| --- | --- | --- |
| Search | Substring of the title or description | On submit |
| Platform | `douyin`, `tiktok` | On change |
| Kind | Video, image album | On change |
| Length | Under a minute (< 60 s), under three minutes (< 180 s), long (≥ 180 s), unknown | On change |
| Availability | Live, deleted, private, unknown | On change |
| Collection | Any of your collections | On change |
| On this disk | Downloaded or not / downloaded only / not downloaded | On change |

Filters combine. The menus apply the moment they change, because picking "douyin" from a closed list *is* the decision; the text box waits for the button, because typing is not a decision until you stop and firing a query per keystroke would page the archive on the way to a word.

Search is **substring matching, not word matching**, and that is a deliberate trade-off rather than an oversight: Postgres' full-text search does not segment Chinese, so a word search would silently match nothing on the primary platform. The cost is that a search for a short string can match more than you meant.

"On this disk" describes what was *kept*, not the post: an evicted download does not count as downloaded, because there is nothing left to play. The counter beside Clear tells you how many filters are narrowing the list, which is usually the answer to "why does a library of 660 posts show four".

## Collections

A collection is a named set you make by hand. It is the one grouping the library offers that is not derived from the posts themselves — author, platform, length and save date all come out of the record; this one comes out of you deciding.

Open **Collections** beside Clear to create, list and delete them. A name is at most 80 characters and is unique case-insensitively (enforced by the database, so two console tabs cannot race); the optional note is at most 500 characters and is never parsed.

Add posts by selecting them and using **Add to collection** in the bulk bar. **Remove from this collection** appears only while you are filtering by one — "remove from which collection" has no answer otherwise. Adding a post that is already in a collection is a no-op rather than an error, and a post that is not in the archive is skipped, so one stale card does not refuse the other nineteen. A single call takes at most 500 posts.

Deleting a collection removes the label only. The posts stay in the archive and their files stay on disk. It is the one delete on this page that does not touch a post or a byte.

## Saving media from the Library

Open a post to get its drawer. The footer carries three actions:

- **Archive their history** — a backfill: walks this author's posts past the first page and archives every one. Deliberately separate from the watchlist, because a watchlist entry is for what is new and runs forever, while a backfill is a one-off with a very different cost. The console asks for the server default of 5 pages; the API accepts 1–20 and the worker caps it at 20 pages of 20 posts. It stops at the first empty page, at the ceiling, or when the platform says there is no more history. It costs one upstream request per page and needs that platform's read scope.
- **Store on this instance** — starts a download for this post. If the media is already stored the button reads **Saved · fetch again** and its tooltip says how large the stored copy is; using it fetches a fresh copy and replaces what is there. (This button does not set `skip_existing`, which is why it re-fetches rather than doing nothing.) Downloading is a queued task, so the row is not stored the instant the toast appears — the page re-checks a few seconds later, and the download itself appears immediately on the Downloads page.
- **Export the video file** — hands your browser the stored video, when there is one. Image posts have no video, so this button does not appear for them; their slides get one link each in the drawer body instead, because a browser will not start thirty downloads from a single click and pretending otherwise would silently save one file out of thirty.

The drawer also plays the stored video inline, from this instance's disk rather than from the platform: no CDN link to expire, no hotlink check to fail, and nobody outside this machine is told what is being watched. It preloads metadata only, so a 240 MB video does not start transferring because a drawer opened.

## Exporting the archive

**Export** in the page header streams the archive as newline-delimited JSON and saves it as `archive-YYYY-MM-DD.ndjson`.

It is streamed a page at a time rather than assembled server-side, so the size of your own data is not what breaks the export. One post per line, in the same shape the list endpoint returns, **without** the media manifest — use the list endpoint or `GET /api/v1/archive/{platform}/{content_id}` when you need the mirrors. A single export is capped at 50 000 rows; past that, page through the list endpoint with `cursor`.

Two things to know before relying on it:

- It needs the `archive:export` scope, which ordinary read keys do not carry. This is the one call that turns a read key into a copy of the database, so it is scoped separately on purpose. A console session carries every scope, so the button works for any signed-in user.
- The export applies the platform, kind, length, availability and search filters, but **not** the collection filter or the "on this disk" filter — those are not parameters of the export endpoint and are ignored. Exporting while a collection filter is active gives you the whole archive, not the collection.

For a copy of the whole instance, including identities and settings, see the backup tooling in [Operations](./10-operations.md); this export is the archive only.

## Re-checking what still exists

**Re-check** in the page header answers "which of the things I saved are gone". Each post is one real request through the identity pool, so the pass runs in the background as a task and is bounded.

Defaults, all settings you can change: `archive.recheck_batch` posts per pass (**25**, hard-capped at 200), only posts whose last check is older than `archive.recheck_after_days` (**7**), and `archive.recheck_pause_seconds` (**3**) between individual requests. That pause is measured rather than chosen: at 0.5 s a live sweep sent 20 TikTok requests in 13 seconds and every one was rejected, while a single manual request a minute later succeeded — the scheduler paces per identity, which does nothing about a platform limit that counts requests per address. Nobody is waiting on this pass, so slow is free.

A deleted post answers the platform's own not-found, and **that is the finding, not a failure**: the row is kept and marked `deleted`, so it stays searchable and exportable with the truth attached. A private post is marked `private`. Any other error leaves the row untouched, because a risk-control response says nothing about whether the post exists. If an endpoint's circuit opens mid-pass the sweep stops early rather than marking the rest unchecked.

The worker queues a recheck by itself roughly every six hours, so the button is for when you want the answer now. Setting `archive.recheck_after_days` to 0 turns rechecking off entirely.

Because it spends the identity pool, this endpoint requires a *platform* read scope (`douyin:read` or `tiktok:read`) rather than `archive:read` — reading the archive is promised to cost nothing, and a recheck is a platform read wearing an archive name.

## Deleting posts and their media

Select posts and press **Delete** in the bulk bar. This is the one call in the archive that destroys something: it is not reversible and there is no trash. The archive rows go, their collection memberships go with them, and unless you call the API with `media: false` the download records and the directories on disk go too.

The files are removed first and the rows second. The other order can leave a record pointing at a directory that is already gone — a download the console offers and cannot deliver; this order can at worst leave bytes with no record, which the storage panel already reports.

If the posts have stored media and the downloader is unreachable — not running, or not answering — the call **refuses and deletes nothing**, rather than orphaning bytes on a volume nothing can account for. If you genuinely want the record gone and the files kept, that is `media: false` on `POST /api/v1/archive/delete`; the console always sends `true`.

A single call takes at most 500 posts, and it needs `media:write` (or `admin`): the same scope that may start a download and cancel one may also remove what it produced, and nothing weaker can.

The posts stay on the platform. Deleting here deletes your copy.

## The Watchlist page

`/watchlist` is the only place in the console that creates *standing* work. Everything else happens once and finishes; an entry here spends the identity pool every few hours for as long as it exists. The page is built around making that cost legible: the interval is a first-class column, the failure count is visible, and **Pause all** is a button rather than something you assemble out of per-row toggles.

What it is for is one table: the snapshot history. Until something collects on a schedule, that history holds whatever a human happened to parse at whatever moments they happened to do it — a scatter of unrelated observations rather than a series. This is what makes growth curves real.

Nothing on this page fetches. When an entry comes due, the worker submits it as an **ordinary task**: the same queue, the same scheduler, the same identity pool, the same archive and snapshot writes at the end. Scheduled collection therefore queues *behind* interactive requests, which is the correct priority — somebody is waiting on those — and it cannot outrun the rate limits, because there is only one set of them.

| Column | Meaning |
| --- | --- |
| Target | The label (filled in from the first successful run) or the raw id, with the kind and platform beneath |
| Interval | How often it runs |
| Next run | Relative time, or **Paused** |
| Runs | Observations collected since it was added |
| Last run | Relative time, or a red "N failures in a row" carrying the last error as a tooltip |
| Running | The enable/pause switch |
| *(unnamed)* | Remove |

The three tiles show how many targets exist and how many are running, total runs, and how many are currently failing.

## Adding a target

Four fields: platform, what to watch, the target id, and the interval.

| Kind | Target id | What one run collects |
| --- | --- | --- |
| An author | The stable author id — `sec_user_id` on Douyin, `secUid` on TikTok. Both start `MS4wLjAB` | One page of their posts (the newest 20). That page carries the author record on every item, so a single request answers both "what is new" and "how many followers now" |
| A post | The post id, as the archive holds it | That post's detail, which is how its counters move over time |

An `@handle` will not work for an author entry. Nothing checks the shape of the id when you add it, so the entry is created — but every run then fails, because the post-list endpoint has no parameter that takes a handle and refuses it by name rather than sending it somewhere it cannot work. Look the author up in the playground or the library first and use the id from the result. Watching the same platform + kind + target twice *is* refused, as a duplicate.

The first run is **due immediately**. Somebody who just added an author wants to see it collect, and holding the first run back by the interval would only make the feature look broken for six hours.

What an entry collects is not configurable from this page, and that is deliberate — see the table above for why one request answers two questions. The API's `pages` field (1–10, default 1) is stored on the entry and returned by the list endpoint, but a scheduled run submits a single request for the newest 20 posts; deeper history is what the library's **Archive their history** backfill is for.

## Intervals, the floor, and a failing entry

The console offers 15 minutes, 1 hour, 6 hours, 12 hours and 24 hours, hiding anything below the server's floor. New entries default to `watchlist.default_interval_seconds` (**6 hours**).

The floor is `watchlist.min_interval_seconds`, **900 seconds** by default, and it cannot be set below 60 whatever you do. An interval under the floor is **refused** rather than silently raised to a number you did not ask for. The reason is not politeness: a target collected every few seconds does not produce a better time series, it burns the whole identity pool on one author and starves everything else. Nothing on either platform moves fast enough to need more than 15 minutes.

Cost, so you can budget: one entry at 6 hours is 4 requests a day. A hundred watched authors at 6 hours is 400 requests a day, one page each. Shortening an interval takes effect immediately rather than after the old one elapses.

Failures back the entry off rather than the task. After **2** consecutive failures the interval starts doubling per additional failure, capped at **8 hours**. A watched author whose id was mistyped would otherwise fail every run forever; this turns that into a few attempts a day and leaves the row visible, with its error, for whoever comes to fix it. A platform outage ends, so it is still retried. Re-enabling a paused entry clears the backoff and its error and makes it due now — you are asserting the problem is fixed, and making you wait out an eight-hour backoff to find out would be its own bug.

Two more bounds worth knowing:

- The watcher ticks once a minute and queues at most `watchlist.batch_size` (**10**) due entries per tick. The rest stay due and go on the following tick, so a hundred entries coming due on the same minute are spread over minutes rather than landing in front of whoever is using the API.
- The next run is scheduled from **submission**, not from completion. A task that never finishes would otherwise stop the entry forever, and "collect every six hours" should keep meaning that even when one run is lost.
- Past the capacity hard stop, scheduled collection stands down entirely. Nobody is waiting on it, and standing down the writers is the whole point of the guard.

## Pausing, removing and turning it all off

Three different switches, in increasing order of scope:

| Action | Effect | Reversal |
| --- | --- | --- |
| The **Running** switch on a row | Pauses that entry. It keeps its history, its runs count and its label | Flip it back; the backoff and last error are cleared and it becomes due now |
| **Pause all** in the page header | Disables every enabled entry at once. The button for "something is wrong and I want the pool back" | Per entry, deliberately — there is no "resume all" |
| The `watchlist.enabled` setting | Stops scheduled collection globally, leaving every entry exactly as configured. The page shows a banner saying so | Turn the setting back on; nothing else changes |

**Remove** deletes the schedule and nothing else. Everything already collected stays in the archive and in the snapshot history — deleting the instruction is not a request to delete what it produced.

Changing, pausing, adding and removing entries all require the **operator** role or higher. Viewing the list only needs a signed-in console session.

## Settings reference

All of these are runtime settings, editable from the console's Settings page or the settings API; see [Configuration](./03-configuration.md) for how settings scopes and editing work.

| Setting | Default | What it does |
| --- | --- | --- |
| `media.enabled` | `true` | Allow media downloads. Off refuses new jobs and leaves stored files alone; nothing is deleted by turning it off |
| `media.max_bytes` | `2147483648` (2 GiB) | How much disk stored media may occupy. Past it the oldest unpinned downloads are removed until the volume is back under, and an alert says what went. `0` disables eviction entirely |
| `media.max_file_bytes` | `536870912` (512 MiB) | Ceiling for one file, counted on bytes written rather than on what the server claims. A transfer that reaches it is refused and leaves nothing behind. Must be `0` or at least 1 MiB |
| `media.mirror_max_age_seconds` | `600` | How old an archived media link may be before a download re-parses the post for fresh ones. `0` always re-parses |
| `capacity.warn_percent` | `80` | Disk usage percent at which to raise a warning |
| `capacity.hard_stop_percent` | `92` | Disk usage percent at which new download jobs and scheduled collection pause. Interactive reads are never paused and nothing is deleted |
| `archive.enabled` | `true` | Keep parsed posts and authors after the task result expires. Off makes the instance stateless beyond its logs |
| `archive.store_raw` | `false` | Also keep each platform's untouched payload. Off by default: it is the largest single thing this instance can choose to store |
| `archive.recheck_after_days` | `7` | How old a post's last existence check may be before it is checked again. `0` turns rechecking off |
| `archive.recheck_batch` | `25` | How many posts one recheck pass verifies |
| `archive.recheck_pause_seconds` | `3` | Seconds between the individual requests a recheck or backfill makes |
| `watchlist.enabled` | `true` | Run scheduled collection. Off leaves the entries and their history alone and simply stops submitting runs |
| `watchlist.min_interval_seconds` | `900` | Shortest interval an entry may be given. Cannot be set below 60 |
| `watchlist.default_interval_seconds` | `21600` (6 h) | Interval a new entry gets when none is given |
| `watchlist.batch_size` | `10` | How many due entries one tick may queue |

## Scopes and roles

The API surfaces behind these three pages are scoped separately on purpose: seeing what was collected, seeing what is on the operator's filesystem, and putting something there are different questions.

| Scope | Grants |
| --- | --- |
| `archive:read` | Search the archive, read its stats, read one post |
| `archive:export` | The bulk NDJSON export, and only that |
| `media:read` | List downloads, read storage usage, fetch a stored file |
| `media:write` | Start, retry, pin and cancel a download; deduplicate; create and change collections; delete archived posts |
| `douyin:read` / `tiktok:read` | The availability recheck and the author backfill, because both spend the identity pool |
| `identity:manage` | The watchlist endpoints (`/api/v1/admin/watchlist/*`); operator role or above for anything that writes |
| `admin` | Everything, the watchlist endpoints included |

Two things follow from this that are easy to get wrong:

- An API key minted with `archive:read` cannot export the archive and cannot see a single byte on your disk. That is the intent. Give a key the narrowest set that does its job — see [Users and API keys](./09-users-and-api-keys.md).
- A **console session carries every scope**, bounded by the account's role rather than by scopes. Only the watchlist endpoints that write additionally check the role (operator or above). So any signed-in console user — including a viewer — can start a download or delete an archived post from the UI. If that is not what you want, do not hand out console accounts you would not hand out `media:write` to.

## Where to go next

- [Configuration](./03-configuration.md) — every setting named here, and how to change one.
- [Concepts](./04-concepts.md) — the identity pool, the scheduler and the task queue these pages spend.
- [Console overview](./05-console-overview.md) — the rest of the pages and how they fit together.
- [Playground and tools](./07-playground-and-tools.md) — parsing one link, signing a request, and the URL identifier this page's inputs use.
- [Users and API keys](./09-users-and-api-keys.md) — minting a key with the right scopes.
- [Operations](./10-operations.md) — alerts, notification channels, disk and backups.
- [REST API guide](./11-api.md) — the endpoints behind every button here.
- [Troubleshooting](./14-troubleshooting.md) — when a download fails, a watchlist entry keeps failing, or the disk fills up.
