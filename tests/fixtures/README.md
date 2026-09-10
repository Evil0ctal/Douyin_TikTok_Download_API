# Replay fixtures

Input for the L2 replay tests in `tests/replay/` (see `docs/design/13-testing.md`).
Each file is one decoded JSON response body, exactly as a platform endpoint returns it,
fed straight into a parser.

## These are hand-built. They must be replaced before release.

**Every file here was written by hand from V4's parsing and request code
(branch `main`, commit `8c98fb7`), not captured from a live platform.** They reproduce the response
shapes that code demonstrably worked against, so the parsers are exercised against a
realistic structure - but a hand-built fixture can only contain the fields whoever wrote
it already knew about. It cannot tell you that the platform renamed a key last week, and
it cannot show you the field nobody thought to look for.

Replacing them with real captured responses is the browser task described in
`docs/design/16-salvage-and-debug.md` - the first of the five browser tasks listed
there, capturing real responses as fixtures. Until that lands, a green replay suite means
"the parsers do what we think the platform does", not "the parsers match the platform".

The capture task, per file:

1. Open the relevant page in a fresh browser context, find the call in the network panel
   (`/aweme/v1/web/aweme/detail/`, `/api/item/detail/`, and so on).
2. Save the complete response body - do not trim fields you think are irrelevant. The
   whole point of a fixture is being a permanent record of what the platform sent.
3. Run it through the redaction pass below.
4. Replace the file and re-run `uv run pytest tests/replay -q`. Every difference the
   parsers notice is real information: either the fixture was wrong, or the parser is.

## Redaction rules

Captured responses contain real people. Before a file lands in this directory:

- **No credentials of any shape.** No key whose name resembles `cookie`, `token`,
  `msToken`, `sessionid`, `sid_tt`, `passport`, `ttwid`, `odin_tt`, `verifyFp`,
  `s_v_web_id`, `_signature`, `authorization` or `credential`.
  `tests/replay/test_platform_contract.py` asserts this on every fixture, and CI runs it.
  A bare `secret` is not on that list on purpose - TikTok's `itemStruct.secret` is a post
  visibility flag, and a check that fires on a legitimate field gets switched off.
- **No real personal data.** Nicknames, handles, signatures, comment text and avatar
  paths are replaced with obviously synthetic values ("Synthetic Creator",
  `synthetic.viewer.a`). Ids are replaced with reserved-looking runs of zeros.
- **No live CDN hosts.** Media URLs point at `*.example-cdn.invalid`. `.invalid` is
  reserved by RFC 2606 and can never resolve, so no test can accidentally reach out to
  the network and no expired signed URL leaks a real account's media.

Keep those properties when you replace a fixture with a captured one: capture the
*structure*, then substitute the *values*.

## What each file is for

Same set on both platforms, so the cross-platform normalization can be compared
directly (`docs/design/11-data-contracts.md`, acceptance criterion 1).

| File | Covers |
|---|---|
| `video_normal.json` | The happy path: a public video with media, stats, music, hashtags and a location |
| `video_image_album.json` | Douyin image album / TikTok photo mode - `kind=image_album`, no video stream, `duration_ms=None` |
| `video_deleted.json` | Withdrawn post: metadata survives, media URLs do not. Must parse, not raise |
| `video_private.json` | Restricted post, same media-free shape but a different flag |
| `video_long_desc.json` | Multi-line description with emoji and six hashtags - pins the title/description split |
| `user_profile.json` | Author profile with counters |
| `user_posts_page1.json` | Paged post list with `has_more` and a cursor |
| `comments_with_replies.json` | Top level comments, one pinned, one carrying inline reply previews |
| `comment_replies_page1.json` | Reply page, last page (`has_more` false, so no cursor) |
| `risk_control_empty.json` | Structurally valid envelope with the payload emptied out - must raise `UPSTREAM_RISK_CONTROL`, never `UPSTREAM_CHANGED` |
| `risk_control_captcha.json` | Captcha / verification decision response - same requirement |

The two risk-control files are the ones most worth replacing with real captures first.
The parsers' risk signatures are inferred from V4's behaviour, and a signature that is
too permissive cools down healthy identities every time somebody looks up a deleted
video - the failure mode `docs/design/02-identity-pool.md` warns about.

## Adding a fixture

Per `docs/design/13-testing.md`: when an endpoint breaks in production, the first step is
to save that response here and write a failing replay test. Then fix the parser. Every
incident leaves a permanent regression guard behind instead of being forgotten.
