## What changed

<!-- Behaviour, not files. "A post that does not exist is an answer, not risk
     control" beats "edited fetch.py". -->

## Why

<!-- The problem this solves. Link the issue if there is one: Fixes #123 -->

## How you verified it

<!-- Commands you ran and what you saw. If something could not be verified — a
     code path that needs the live platform, a browser backend you do not have
     installed — say so. An honest "not verified against a real browser" is
     worth more than a confident claim a reviewer has to disprove. -->

## Checklist

- [ ] `make fmt` — run first, so the format gate does not fail on whitespace
- [ ] `make lint && make type` — ruff and mypy
- [ ] `make test` — unit, replay and integration, with the fixtures managed for you
- [ ] `(cd web && npm run verify)` — only if you touched anything under `web/`
- [ ] User documentation updated in **both** `documents/en/` and `documents/zh/`, if a reader would act on the change: a setting, a CLI command, an endpoint, a console page, a default
- [ ] None of: CJK in source, a hex colour outside the tokens file, literal prose in JSX, a `TODO`, a `.env`, a fixture with a real cookie in it
- [ ] No key, password, cookie or API key in the diff or in this description

<!--
Pull requests target `main`. The `v4` branch is frozen and takes security fixes only.

Adding a platform endpoint, a console page or a translated string each has its own
section in documents/en/16-contributing.md.
-->
