# Contributing

This page is for someone who wants to change the code. After reading it you will be able to set up a development environment, find your way around the tree, run the API, the worker and the console locally, run every layer of the test suite, and get a change past the gates CI enforces.

## What you need

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | `>=3.12,<3.14` | The pin in `pyproject.toml`. The container image builds on 3.12.8 (`PYTHON_VERSION` in `docker/compose.yml`). |
| uv | current | The only supported installer and runner. `uv.lock` is the pin; CI runs with `UV_FROZEN=1`. |
| Node.js | 22 | The console. That is the version the CI console job uses. |
| Docker + Compose v2 | current | PostgreSQL and Redis for the integration tests, and the full stack. |
| Go | 1.23 | Only if you touch the media downloader in `docker/downloader/`. |

There is no root `requirements.txt` and no `setup.py` (the browser-rpc sidecar keeps its own `requirements.txt`, since it ships as a separate image). Dependencies are declared in `pyproject.toml` and resolved into `uv.lock`; changing a dependency means changing both and committing the lock file, or CI fails at `uv sync` rather than at your code.

You do not need a headless browser to develop. `browser-rpc` is an optional compose profile, the signing default is the in-process `native` implementation, and the identity pool falls back to manual cookie import when no browser service is configured. See [Identities and proxies](./06-identities-and-proxies.md).

## Setting up

v5 lives on the `v5` branch. `main` still holds V4 and shares no code with it; CI is wired to run on pushes and pull requests against `v5`.

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
git checkout v5
```

Python side:

```bash
uv sync --all-extras
```

`make install` is the same command. `--all-extras` pulls the `dev` extra — pytest, pytest-asyncio, pytest-cov, ruff, mypy, types-redis — which nothing else installs.

Console side:

```bash
(cd web && npm ci)
```

`npm ci` rather than `npm install`: it installs exactly what `package-lock.json` says, which is what CI does, and it will not quietly move a version underneath you.

Check that both halves work:

```bash
uv run dtk --version
(cd web && npx tsc --version)
```

Everything below assumes you are at the repository root unless a command says otherwise.

## Repository layout

Top level:

| Path | What belongs in it |
| --- | --- |
| `src/dtk/` | The Python application. One installable package, `dtk`, declared in `pyproject.toml` as `packages = ["src/dtk"]`. |
| `tests/` | The Python test suite, in four layers — see [The test suite](#the-test-suite). |
| `web/` | The React console. Built to static files and served by the API container; no Node in production. |
| `docker/` | Images, compose files, and the two optional sidecars that live outside the `dtk` package: `browser_rpc/` (Python, FastAPI + headless browser, its own dependency set) and `downloader/` (Go). |
| `scripts/` | `smoke.sh`, the full-stack end-to-end check. |
| `documents/` | This documentation set, `en/` and `zh/`. |
| `logo/` | The project mark, as SVG and PNG. |
| `Makefile` | The short names for everything in this page. Read it when a command here surprises you — it is the source. |
| `alembic.ini` | Migration config. Deliberately carries no database URL; `env.py` reads `DTK_DATABASE_URL`. |
| `.env.example` | The bootstrap variables; the three secrets ship empty, everything else carries the default it documents. Your own `.env` is git-ignored and must stay that way. |

One path you will see cited and will not find: docstrings and comments across `src/`, `web/`, `tests/` and `docker/` — around 160 files — point at `docs/design/NN-*.md`, and `.github/workflows/ci.yml` does too. `.gitignore` lists `docs/`, so that directory is never distributed and your clone does not have it. Those files are the author's working design notes; `documents/` is the published equivalent, organized for a reader rather than file-for-file against them. A docstring citing `docs/design/13-testing.md` is not a broken link to fix — it is a note the author left for themselves.

Inside `src/dtk/`:

| Package | What belongs in it |
| --- | --- |
| `api/` | FastAPI: the app factory, middleware, the response envelope, dependencies, and `routes/` (with `routes/admin/` for console-only routes). Also `console.py`, which serves the built SPA. |
| `cli/` | The `dtk` command. One module per command group; `runtime.py` owns the per-invocation lifecycle. |
| `core/` | Config, crypto, the engine and session, Redis, logging, error codes, shared enums. Everything else may import this; it imports nothing else. |
| `db/` | ORM models, repositories, Alembic revisions under `migrations/versions/`, TimescaleDB helpers. |
| `i18n/` | Backend catalogues and locale formatting. Chinese text lives in `locales/*.json`, never in Python. |
| `identity/` | The identity pool, cookie import, the mint log, and the browser-rpc client. |
| `mcp/` | The MCP server. It calls the service layer directly, not this project's own HTTP API. |
| `media/` | The client for the Go downloader sidecar, its CDN host allowlist, and the download plan. |
| `models/` | The normalized domain models (`Content`, `Author`, `Comment`, `Page`) both platforms produce. |
| `ops/` | Health, backup, notifications, diagnostics, retention, masking, webhooks — everything an operator needs and no diagram shows. |
| `platforms/` | One subpackage per platform. Endpoint tables, parameter builders, parsers. **No IO of any kind.** |
| `scheduler/` | Ranking, leases, token buckets, circuit breaker, per-endpoint policy. |
| `services/` | The layer REST, MCP and the CLI all share: fetch, archive, downloads, tasks, watchlist, collections, cache, settings. |
| `signing/` | The signature algorithms (`native/`), the browser fallback (`rpc.py`), and the registry that chooses between them. |
| `transport/` | The wreq client, TLS/browser emulation, headers, and response classification. Nothing above this package imports wreq. |
| `urls/` | URL recognition, id extraction, short-link expansion. Also the SSRF chokepoint — do not parse a platform URL anywhere else. |
| `worker/` | The task loop, the background loops (pool filler, proxy prober, maintenance, watcher), `ops/` for one-shot operations, and `registry.py` — the one table that says what `douyin.content_detail` means. |

Inside `web/src/`:

| Path | What belongs in it |
| --- | --- |
| `main.tsx` | Theme and i18n bootstrap, root render, document title. |
| `App.tsx` | The shell, the router, and the setup gate. |
| `pages/` | One file per route. |
| `components/` | The design-system inventory. Import from `@/components`. |
| `hooks/` | Shared hooks. |
| `lib/` | API client, endpoint paths, formatting, i18n, theme, navigation. |
| `locales/{en,zh}/` | `common`, `console`, `errors`, `setup`. The only place Chinese text may live. |
| `styles/` | `tokens.css`, `base.css`, `utilities.css`. The only place a colour literal may live. |

`@/` resolves to `web/src/`. TypeScript is strict, plus `noUncheckedIndexedAccess` and `verbatimModuleSyntax` — index access is `T | undefined` and type-only imports need `import type`.

## The services you develop against

Postgres and Redis in the production compose file sit on an `internal: true` network and publish no ports, so a process on your host cannot reach them. Development uses a second, separate compose project instead:

```bash
make fixtures-up
```

which is:

```bash
docker compose -p dtk-test -f docker/compose.test.yml up -d --wait
```

| Service | Address | Credentials |
| --- | --- | --- |
| PostgreSQL (`timescale/timescaledb-ha:pg17`) | `127.0.0.1:55432` | user `dtk`, password `dtk_test_password`, database `dtk_test` |
| Redis 8 | `127.0.0.1:56379` | no password |

Both are bound to loopback. The integration suite reads those addresses from `DTK_TEST_DATABASE_URL` and `DTK_TEST_REDIS_URL` and falls back to exactly the values above, so point them somewhere else if you already run Postgres and Redis locally.

**These fixtures are disposable.** `make fixtures-down` runs `down -v`, which removes the volumes with the containers, and the Redis service runs with persistence switched off. Nothing you put in them survives. That is deliberate: a test fixture that accumulates state is a test fixture that eventually explains a failure that has nothing to do with your change.

Two consequences worth knowing before you use them as a development database:

- The integration suite clears every table before each test and flushes Redis. If you are developing against the same fixtures, running the suite will delete your data.
- The suite reserves Redis logical database 9 for the API tests, so it will not flush a neighbouring suite's session mid-test. Your own process defaults to database 0.

The full stack is a different thing, and it is what [Installation and deployment](./02-installation.md) covers:

```bash
make up      # docker compose -p dtk -f docker/compose.yml up -d --wait
make logs
make down    # stops the stack, keeps the data
```

Use it to check that a change works in a real deployment, not as your edit-and-reload loop.

**`make clean` is deliberately not in that block, and it is not the fixture command.** It runs `down -v --remove-orphans` against the `dtk` project — the real stack — as well as against the test fixtures and against the `dtk-smoke` project `scripts/smoke.sh` leaves behind. So it removes every `dtk` container and network *and* the volumes: `postgres-data`, `redis-data`, `backup-data` and `media-data`. That is your instance's database, its backups and its downloaded media, not just the disposable fixtures above. It is the same command [Installation and deployment](./02-installation.md) calls irreversible — copy any archive you want to keep off the backup volume first. `make down` is the one that stops the stack and leaves the data where it is.

## Running the API and the worker

Three environment variables are read before the database exists, and the process refuses to start without the first one:

```bash
export DTK_SECRET_KEY=$(openssl rand -base64 48)
export DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test
export DTK_REDIS_URL=redis://127.0.0.1:56379/0
```

`DTK_SECRET_KEY` must be at least 32 characters. It encrypts every stored cookie and proxy credential, so nothing ships a default; a key that is the same on every install is not encryption. Generate a throwaway one for development and do not reuse a production key.

**A gotcha worth naming.** `BootstrapSettings` reads a `.env` file from the current working directory as well as the environment. If you have written a repository-root `.env` for the compose stack, it names `postgres:5432` and `redis:6379` — container hostnames that do not resolve on your host. Real environment variables take precedence over the file, which is why the exports above are the reliable form.

Apply the schema, then run:

```bash
uv run dtk migrate
uv run dtk serve --reload
```

`dtk serve` reads `bind_host` and `bind_port` from the bootstrap settings (`127.0.0.1:8000` by default) and `--host` / `--port` override them. `--reload` cannot be combined with `--workers`; the command says so rather than starting something confusing.

The worker is a second process, and everything asynchronous — parses, downloads, watchlist collection, identity minting, backups — is dead without it:

```bash
uv run dtk worker
```

It is deliberately free of tuning flags. Concurrency and the background intervals belong to the process configuration, not to the invocation, so that a worker you start by hand during an incident behaves exactly like the one in the compose file. It drains on SIGTERM: claiming stops at once, in-flight work is given time to finish.

`uv run alembic upgrade head` is equivalent to `dtk migrate` and is what CI uses; `alembic.ini` carries no URL, and `env.py` reads `DTK_DATABASE_URL` (then `DATABASE_URL`) from the environment, so no connection string with a password can end up in a committed file.

If `web/dist` exists, the API serves the built console at `/`. If it does not — the normal state in a checkout — those routes simply do not exist, and you use the dev server instead.

## Running the console dev server

```bash
(cd web && npm run dev)
```

That serves the console on `http://localhost:5173` and proxies `/api`, `/docs`, `/redoc`, `/openapi.json`, `/healthz` and `/readyz` to `http://127.0.0.1:8000` — the API process from the previous section. Nothing else is proxied.

| Variable | Effect |
| --- | --- |
| `DTK_API_TARGET` | Where the dev proxy sends those prefixes. Default `http://127.0.0.1:8000`. |
| `VITE_API_BASE_URL` | Only needed when the console is served from a different origin than the API. |

The other scripts, all run from `web/`:

| Command | What it does |
| --- | --- |
| `npm run dev` | Vite dev server on port 5173. |
| `npm run typecheck` | `tsc --noEmit` against both `tsconfig.json` and `tsconfig.node.json`. |
| `npm run lint` | ESLint with `--max-warnings 0`, then `node scripts/check-i18n.mjs`. |
| `npm run build` | Typechecks `tsconfig.json`, then emits `web/dist`. |
| `npm run preview` | Serves the built output. |
| `npm run verify` | `typecheck`, `lint`, `build` — the three CI runs, in CI's order. |

## The test suite

Four layers, and the split is what makes each one trustworthy. Configuration is in `pyproject.toml` under `[tool.pytest.ini_options]`: `asyncio_mode = "auto"` (async tests need no decorator), `testpaths = ["tests"]`, and two declared markers.

| Layer | Path | Needs | Marker | Run with |
| --- | --- | --- | --- | --- |
| Unit | `tests/unit/` | Nothing | none | `uv run pytest tests/unit -q` |
| Replay | `tests/replay/` | Nothing | none | `uv run pytest tests/replay -q` |
| Integration | `tests/integration/` | PostgreSQL + Redis | `integration` | `uv run pytest tests/integration -q -m integration` |
| Contract | `tests/contract/` | A running deployment and the live platform | `live` | `uv run pytest tests/contract -m live -v` |

The two markers are declared as:

- `integration: requires PostgreSQL and Redis`
- `live: hits the real platform; never runs in CI gating`

### Unit and replay

```bash
make test-unit          # uv run pytest tests/unit tests/replay -q
```

No services, no network, no configuration. Unit tests cover the scheduler maths, the signing algorithms, URL parsing, the worker's classification, the ops modules, the CLI, and a set of repository hygiene checks described under [Code style](#code-style).

Replay tests feed stored response bodies straight into the platform parsers — `dict` in, model out, no IO — which is only possible because parsers are pure functions. They also assert **normalization parity**: for the same logical object both platforms must produce the same field set, differing only in which fields are `None`. A per-platform test cannot notice when the two drift apart.

`tests/fixtures/<platform>/` holds eleven JSON files per platform, one per response shape (a normal video, an image album, a deleted post, a private post, a long description, a profile, a post page, comments, replies, and two risk-control shapes). Read `tests/fixtures/README.md` before adding or replacing one — it carries the redaction rules, and `tests/replay/test_platform_contract.py` fails the build on a fixture containing a credential-shaped key.

Know what those fixtures currently are: **every one of them was written by hand from V4's parsing and request code**, not captured from a live platform, and they are meant to be replaced by real captures before release. That is what the README says, and `test_fixture_readme_states_they_are_hand_built` pins the warning so it cannot quietly be edited away. Until the captures land, a green replay run means "the parsers do what we think the platform does", not "the parsers match the platform".

### Integration

```bash
make test-integration
```

which brings the fixtures up, runs `uv run pytest tests/integration -q -m integration`, and tears them down with `down -v` afterwards whether the suite passed or failed.

The first time you run the suite against fresh fixtures, apply the migrations first — CI does this in its own step, and `make test-integration` does not do it for you. `tests/integration/test_migrations.py` asserts the *physical* shape of the database (hypertables, chunk intervals, policies), which only the Alembic revisions produce:

```bash
make fixtures-up
DTK_DATABASE_URL=postgresql+asyncpg://dtk:dtk_test_password@127.0.0.1:55432/dtk_test \
  uv run alembic upgrade head
uv run pytest tests/integration -q -m integration
```

Every module in this layer sets `pytestmark = pytest.mark.integration`, so `-m integration` is a filter and not a requirement — but keep the mark on anything you add, because it is what lets a run exclude the layer entirely. If Postgres or Redis is unreachable, the fixtures in `tests/integration/conftest.py` **skip** with a message telling you to run `make fixtures-up`, rather than failing with a connection error.

`tests/integration/test_api_support.py` holds the shared fixtures for the API tests. Its `TABLES` tuple lists every table cleared before each test, children first, and `tests/unit/test_repo_hygiene.py` fails if a table exists in the schema and is missing from that list. Add a table to the model, add it to `TABLES`.

### Contract

These hit the live platform, and they exist to answer one question nothing else can: **did the platform change?** A replay test runs against a fixture frozen in the repository — hand-built today, a real capture later — so it keeps passing while production breaks.

```bash
DTK_CONTRACT_BASE_URL=http://127.0.0.1:8000 \
DTK_CONTRACT_API_KEY=dtk_xxx_yyy \
  uv run pytest tests/contract -m live -v
```

Without both variables every test skips, which is what happens in ordinary CI. `tests/contract/subjects.py` ships **empty** on purpose: a fresh clone has no test subjects, so the suite skips rather than failing at somebody else's deleted video.

They deliberately do not gate merges. A contract test fails for reasons that have nothing to do with the diff under review — the platform hiccups, a proxy drops, the pool is empty — and making a pull request wait on that teaches everyone to ignore a red check. In `.github/workflows/ci.yml` the job carries `continue-on-error: true` and is gated on `if: github.event_name == 'schedule'` — but that workflow declares only `push` and `pull_request` triggers, so the condition is never true and the job never runs in CI at all. Today it is a by-hand suite; wiring it up means adding a `schedule:` trigger to the workflow.

### Two suites that live outside `tests/`

`testpaths = ["tests"]` means a bare `uv run pytest` does not collect either of these. Name the path.

```bash
uv run pytest docker/browser_rpc/tests -q      # browser-rpc, against its fake backend; no browser needed
(cd docker/downloader && go test ./...)        # the Go media downloader
```

`docker/browser_rpc/tests/conftest.py` puts `docker/` on `sys.path`, so `import browser_rpc` works from a checkout even though that service ships as its own image with its own dependency set.

### Coverage

CI collects coverage on the unit job with `--cov=src/dtk --cov-report=term-missing`. There is **no minimum-coverage gate** — nothing fails on a percentage. `[tool.coverage.run]` measures `src/dtk` and omits `*/migrations/*`.

## Quality gates

These are the checks CI runs. All of them are runnable locally; only the image build needs the network, for the base images and the dependency downloads.

| Gate | Command | CI job |
| --- | --- | --- |
| Format | `uv run ruff format --check src tests` | `static` |
| Lint | `uv run ruff check src tests` | `static` |
| Types | `uv run mypy` | `static` |
| Unit + replay | `uv run pytest tests/unit tests/replay -q` | `unit` |
| Integration | `uv run pytest tests/integration -q -m integration` | `integration` |
| Console types | `(cd web && npm run typecheck)` | `console` |
| Console lint + i18n | `(cd web && npm run lint)` | `console` |
| Console build | `(cd web && npm run build)` | `console` |
| Compose files parse | `docker compose -f docker/compose.yml config -q`, then `docker compose -f docker/compose.test.yml config -q` | `image` |
| Image builds | `docker build -f docker/Dockerfile -t dtk:ci .` | `image` |

The Python side has short names:

```bash
make fmt     # ruff format, then ruff check --fix
make lint    # ruff check, then ruff format --check
make type    # mypy
make test    # unit + replay, then integration with the fixtures managed for you
```

`make fmt` is the one that writes; run it before `make lint` and the format gate will pass.

An end-to-end check of the whole stack, which brings the compose project up, walks the initialization flow, mints an API key, exercises the documented contract and always tears itself down:

```bash
make smoke        # ./scripts/smoke.sh
./scripts/smoke.sh --keep   # leave the stack running to poke at
```

It is not part of CI. It takes minutes and needs Docker, and it is worth running before a release or after a change to the compose files, the entrypoint or the setup flow.

### What the tools are configured to do

**ruff** (`[tool.ruff]`): line length 100, target `py312`, rule sets `E`, `F`, `W`, `I` (import sorting, `dtk` as first-party), `N`, `UP`, `B`, `C4`, `SIM`, `RUF`. Ignored: `E501`, `B008`, `N818`, `RUF001`, `RUF002`, `RUF003`.

Two of those are worth understanding rather than working around. `E501` is off because the *formatter* owns line length at 100 — a long URL or a long comment is not a lint error. `N818` wants an `Error` suffix on every exception class; the domain exceptions here are named after their stable wire error code (`UpstreamChanged`, `SecretKeyMissing`) so the class name matches the contract a caller branches on, and the base class carries the suffix.

**mypy** (`[tool.mypy]`): Python 3.12, `packages = ["dtk"]`, `mypy_path = "src"`, `ignore_missing_imports = true`, `warn_unused_ignores = true`. `uv run mypy` with no arguments checks the right thing.

It is not globally strict, and that is a deliberate gradient: three packages — `dtk.platforms.*`, `dtk.models.*`, `dtk.scheduler.*` — additionally set `disallow_untyped_defs`. Those are the ones where an untyped function is genuinely dangerous: the data contract both platforms have to satisfy, and the arithmetic that decides which identity gets used.

**ESLint** (`web/eslint.config.js`): the recommended JS and TypeScript sets, `react-hooks`, plus `@typescript-eslint/no-explicit-any` and `no-non-null-assertion` as errors and three rules written for this project — see the next section.

## Code style

Some of these are conventions and some are automated. Where a check exists it is named, because a rule with a check behind it is a rule you can rely on rather than one you have to remember.

### Source is English

No CJK anywhere in `src/dtk/**/*.py` or in `web/src/**/*.{ts,tsx}` — comments, identifiers and strings alike. Chinese lives in `src/dtk/i18n/locales/*.json` and `web/src/locales/zh/*.json`, and nowhere else.

Checked twice: `tests/unit/test_repo_hygiene.py::TestSourceLanguage` on the Python side and the `local/no-cjk-source` ESLint rule on the console side. Writing a comment in your own language is always the path of least resistance, so only a machine check holds this line.

Tests may contain CJK **only as input data** — real users paste Chinese share text and the URL extractor has to cope with it. Three files are allowlisted for that: `tests/unit/test_urls.py`, `test_i18n.py`, `test_identity_importing.py`. Everything that is code — names, comments, assertions — stays English even there, and `tests/unit/test_i18n.py` writes its Chinese expectations as `\uXXXX` escapes with a comment saying what each one reads as.

The one exemption is the project's mark, the ASCII cat that has sat at the top of this project's entry points since v1 and is drawn partly in katakana. It is exempted **by shape, not by filename**, in `tests/support/marks.py`: a line passes only if every character in it comes from the mark's own small alphabet, so a line that smuggles a real word in beside the cat is not exempt.

### Every user-visible string comes from a catalogue

On the console this is a lint rule, not a review note. `local/no-untranslated-text` reports literal prose in JSX text, in a set of translatable attributes (`label`, `placeholder`, `title`, `description`, `hint`, `summary`, `caption`, `confirmLabel`, …), in `{cond ? 'Yes' : 'No'}` expressions, and in object-literal properties with those names — because column and option tables are declared far from the JSX that renders them.

Identifiers are not prose and are exempt by pattern: `SCREAMING_CASE` codes, dotted setting keys and endpoint names, version numbers, IANA time zones, URLs, and anything inside `<code>`, `<pre>`, `<kbd>`, `<samp>` or an element with the `u-mono` / `mono` class. If your case is a genuine exception, add an `eslint-disable-next-line` with the reason. Never translate an error code, an enum value, a field name, an endpoint path or a config key — translate the *display label* instead.

### Colours come from tokens

No hex literal in `.ts` or `.tsx`. `local/no-raw-hex-color` fails the build on one there, in a string or in a template literal, and `tests/unit/test_repo_hygiene.py` sweeps the same files as a backstop. ESLint never parses CSS here, so a hex literal in a `.module.css` is held by convention rather than by a check — the one deliberate exception in the tree is the white tile behind the sponsor mark, which is commented where it sits. Use `var(--accent)`, `var(--danger)`, `var(--bg-raised)`.

Both themes are complete implementations rather than a base plus a filter, and `tests/unit/test_repo_hygiene.py` asserts that the dark and light blocks define exactly the same token names. A token defined in one and not the other breaks precisely one theme, quietly, in one colour.

### Comments explain why

The code says what it does. A comment earns its place by recording the reason a decision was made — the measurement behind a number, the failure that produced a guard, the alternative that was tried and did not work. Much of this codebase's commentary is of that shape, and it is what stops the next person "fixing" a deliberate trade-off. Where a number came from a measurement, say what was measured and when.

### No leftovers in shipped code

`tests/unit/test_repo_hygiene.py` fails on `TODO`, `FIXME` or `XXX` anywhere in `src/dtk/`, and on `NotImplementedError` outside the two legitimate uses (an abstract base method, which is a contract, and `contextlib.suppress(NotImplementedError, ...)`, which is how a caller copes with a platform that lacks a capability).

### Secrets are never committed

`.env` and `.env.*` are git-ignored, with `.env.example` the one exception, and `.env.example` must ship its three secret variables empty — a test asserts it. Another scans the Python tree for anything shaped like a live session cookie. This is the V4 lesson: a real Douyin session sat in a tracked `config.yaml` and was one command away from being published.

### Layering rules that are structural, not stylistic

- **`platforms/` performs no IO.** No HTTP, no configuration, no database. A platform package builds request descriptions and parses payloads; retries, signing, proxies, cookies, logging and caching all live above it. This is what makes the replay layer possible at all.
- **Parsers are pure functions.** `dict` in, model out. A missing required field raises `UpstreamChanged` carrying the dotted path that went missing; a payload that is a refusal raises `UpstreamRiskControl`. Returning a half-filled model is never allowed.
- **Nothing above `transport/` imports wreq.**
- **Nothing outside `urls/` parses a platform URL.** That package is also the SSRF chokepoint for caller-supplied URLs.
- **`core/` imports nothing else in the package.** Everything else may import it.

## Adding a platform endpoint

The example below adds a capability to an existing platform. Adding a whole platform is the same work plus a new `src/dtk/platforms/<name>/` directory — and only that: adapters are discovered at import time by scanning for subpackages that expose `adapter.ADAPTER`, so there is no registry to edit. `tests/replay/test_platform_contract.py` asserts that property directly.

1. **Declare the endpoint.** In `src/dtk/platforms/<platform>/endpoints.py`, add the URL constant, a stable name constant (`douyin.author_followers` — `<platform>.<capability>`), and an `EndpointSpec` entry in the `ENDPOINTS` table. The spec carries `required`, the `build` callable, a `risk_weight`, and a `summary`.

   The name is part of the **operational contract**: the scheduler uses it verbatim as its Redis token-bucket and circuit-breaker key, the console shows it on the endpoint health board, and every row in `request_log` carries it. Do not rename one casually.

2. **Build the query string.** Add the builder to `src/dtk/platforms/<platform>/params.py`. Values are left un-encoded — percent-encoding happens once, in the transport layer, because the signature is computed over the encoded query string and encoding twice was a recurring source of V4 signature failures. Browser-shaped values (screen size, browser version, language) arrive as a `ClientProfile` rather than being hardcoded, so that the query agrees with the identity's User-Agent and TLS emulation.

3. **Parse the response.** Add or extend a parser in `parser.py` and expose it on the adapter. Use the helpers in `src/dtk/platforms/common.py` so both platforms make the same decision about the same ambiguity: absent is `None` and never `0` or `""`, identifiers are `str`, a missing required field raises `UpstreamChanged` with its path.

4. **Give it a scheduling policy.** Add an `EndpointPolicy` to `src/dtk/scheduler/policies.py`. This is not optional in practice: `tests/unit/test_policy_coverage.py` fails if a registered endpoint has no explicit policy, because a mismatch does not raise — it silently falls through to `DEFAULT_POLICY` and every tuned quota stops applying with nothing to say so. The same test rejects a policy naming an endpoint that no longer exists, and asserts the shape of a sane policy: `max_concurrency == 1`, refill at most 1/s, burst between 1 and 10, `risk_weight >= 1.0`.

   List endpoints are throttled harder than detail lookups, and a test asserts that too. Enumerating an author's whole catalogue is the pattern platforms watch for; one detail lookup is the cheapest call a real user makes.

5. **Wire the caller-facing name.** In `src/dtk/worker/registry.py`, add the `Capability` value if it is new, then an entry in `_ARGUMENTS` mapping canonical parameters (`content_id`, `author_id`, `unique_id`, `mix_id`, `comment_id`, `cursor`, `count`) onto that platform's own argument names, and an entry in `_CACHE_TTL_KEYS`. The registry checks your mapping against the real builder signature at import time, so a typo is an import error rather than a runtime one.

   Capabilities in `P0_CAPABILITIES` are required of every platform. Everything else is optional and declared per platform, because the platforms genuinely differ — TikTok exposes playlists as their own list, Douyin serves an author's likes only to a signed-in identity. The endpoint health board, `GET /api/v1/admin/endpoints/health`, lists every declared endpoint per platform, so a console operator can see which capability exists where; an ordinary caller discovers it from the `UNSUPPORTED_CONTENT` error, whose `details.supported` names the platforms that do serve the operation.

6. **Expose it.** A REST route in `src/dtk/api/routes/content.py`, declared with `openapi_extra={I18N_KEY: "<key>", **ASYNC_RESPONSES}`, and catalogue prose at `openapi.op.<key>.summary` / `.description` in both `src/dtk/i18n/locales/en.json` and `zh.json`. `tests/unit/test_i18n.py` and `tests/unit/test_openapi_completeness.py` fail on an operation with no prose, a parameter with no `description=`, or a Chinese document that is still in English. The route function's docstring is what FastAPI uses as the English description.

   MCP is a separate decision. The tool set is capped at eight on purpose — every extra tool measurably lowers an agent's accuracy at choosing one — so a new capability should be a parameter of an existing tool rather than a ninth. See [MCP and AI agents](./12-mcp.md).

7. **Add fixtures and a replay test.** One JSON file per platform under `tests/fixtures/<platform>/`, redacted per that directory's README, and a test in `tests/replay/`. When an endpoint breaks in production the first step is to save that response here and write a failing replay test, then fix the parser — so every incident leaves a permanent regression guard behind instead of being forgotten.

## Adding a console page

1. `web/src/pages/<Name>.tsx`, default-exporting the component. Start with a `PageHeader` and build outwards; use the inventory in `@/components` rather than hand-rolling a table or a dialog.
2. A `lazy()` import and a `<Route>` in `web/src/App.tsx`. Every page is code-split.
3. An entry in `NAV_ITEMS` in `web/src/lib/nav.ts`: `path`, `labelKey`, a `group` from `NAV_GROUPS` (`monitor`, `pool`, `tools`, `access`, `operations`), and an `icon` from `NavIconName`. The icon name must exist in the `NAV_ICONS` map in `web/src/components/Sidebar.tsx`. A page that is real but has no place in a group goes in `UNLISTED` instead, so the breadcrumb can still name it.
4. Translation keys in **both** `web/src/locales/en/console.json` and `web/src/locales/zh/console.json`: `nav.<key>` for the sidebar label, and `page.<key>.title` and `page.<key>.description` for the header. Add page-specific keys in the same commit — `npm run lint` fails on a `t()` call naming a key no catalogue has, and on any literal prose you left in the JSX.

Conventions the design system expects, and which reviewers will hold you to:

- **Status is colour + icon + text.** Use `<StatusBadge kind="…" value={…} />` rather than colouring a cell. `business_error` renders muted, never red: a deleted video is not a system fault.
- **Ids and JSON are mono.** `<CopyableId>` for an id, `<CodeBlock json={…}>` for a payload.
- **Polled values never animate.** Pass `flashValue` to `DataTable` so a row highlights once when its *status* changes; do not add transitions that fire on every refresh.
- **Focus rings stay.** No `outline: none` without a visible replacement. Everything reachable and operable by keyboard, overlays close on Esc.
- **Give `DataTable` a `storageKey`** so the viewer's column and density choices persist.
- **Poll at `POLL.fast` / `POLL.normal` / `POLL.slow`.** Do not invent an interval.
- **It must stay usable on a phone.** Below 768px the sidebar becomes a drawer and tables become cards. That is where the 3am alert lands.

## Adding a setting

Runtime settings are seeded from the environment once at first init and then live in the `settings` table, editable from the console without a restart. There are currently 54 of them across 14 groups. Full reference: [Configuration reference](./03-configuration.md).

1. **Declare it** in `RUNTIME_SETTINGS` in `src/dtk/core/config.py` as a `SettingSpec`: key (`<group>.<name>`), default, `Scope`, type, and a developer-facing description. Anything not in this registry cannot be stored, which is what keeps a typo from silently becoming a config key.

   Pick the scope honestly. `RUNTIME` is the ordinary case. `SENSITIVE` means widening the value enlarges the attack surface — the CORS origins, the URL allowlist, the task webhook switch. `BOOTSTRAP` and `ENV_ONLY` are environment-only and are not part of this flow.

   Use `choices` for a constrained string, and `validate` for a constraint the type cannot express. A validator returns the value to store, so it may canonicalize as well as refuse — and it should refuse, by name, rather than normalize a nonsense value into a plausible one. `_percent` rejects anything outside 1–99 because 0 would pause an instance the moment it starts and 150 would mean the guard never fires; `_byte_ceiling` allows 0 for "no limit" but refuses anything under 1 MiB.

2. **Explain it in both languages.** Add `settings.description.<key>` to `src/dtk/i18n/locales/en.json` and `zh.json`. This is not optional: `tests/unit/test_i18n.py` lists every setting with no catalogue description and fails, and a second test fails if the description is the humanized key echoed back. The registry's own description is a note to developers; it is not the text a user reads.

3. **If the group is new**, add it to `GROUP_ORDER` in `web/src/pages/Settings.tsx` — or to `SECTIONS` in `web/src/pages/Scheduler.tsx` if it belongs to the scheduler, where `sched` and `pool` render beside the pool they act on. Then add `settings.group.<group>` and `settings.groupHint.<group>` to both console catalogues. Without this the setting renders under "Other" with no heading of its own, and `tests/unit/test_repo_hygiene.py` says so.

4. **If it has `choices`**, add `settings.choice.<key>.<choice>` for every choice in both console catalogues. A picker whose options are bare keys makes the reader guess what they do, and a test enforces it.

## Adding a translated string

Two catalogue systems, and they do not overlap.

**Backend**, in `src/dtk/i18n/locales/`:

| File | Holds |
| --- | --- |
| `en.json`, `zh.json` | Everything the API renders as prose: setting descriptions, OpenAPI operation summaries and descriptions, parameter and request-body field text, diagnose actions, state and outcome labels. |
| `errors.en.json`, `errors.zh.json` | One message template per `ErrorCode`. Keyed by the code name. |
| `format.en.json`, `format.zh.json` | Locale formatting: count scales (`K`/`M`/`B` versus 万/亿), relative-time wording, duration patterns. |

**Console**, in `web/src/locales/{en,zh}/`: four namespaces — `common`, `console`, `errors`, `setup`.

Rules that hold on both sides:

- **The key sets must match exactly** between `en` and `zh`. A missing key falls back to English silently and nobody notices, so it is a failure and not a warning.
- **Placeholders must be identical** across languages, and console messages must parse as ICU in both.
- **A translation must not be a copy of the English text.** A test looks for that.
- **Never translate** an error code, an enum value, a field name, an endpoint path or a config key. Translate the label that displays it.

`(cd web && npm run lint)` runs `web/scripts/check-i18n.mjs`, which checks all of the above plus two things worth knowing about:

- **Language names in the switcher are endonyms**, identical in every catalogue. A reader who cannot read the current language is scanning the picker for the name of their own; translating that name is exactly what hides it from them.
- **Every literal key a `t()` call names must exist.** Parity says the two files agree; it says nothing about whether the console asks for keys either of them has. A typo ships silently and renders as the raw key in the middle of a drawer — which this check exists because of. Keys built from a variable cannot be resolved and are skipped; a call site that passes `defaultValue` is saying the key may be absent and is allowed.

On the Python side, `tests/unit/test_i18n.py` enforces the same parity, checks that every `ErrorCode` is translated in every language, and asserts that the locale files stay well-formed JSON. Runtime policy is the opposite of CI policy on purpose: a gap logs a warning and falls back to English rather than refusing to boot, because these files ship as editable JSON in a self-hosted install and a translator's typo must cost one sentence, not the instance.

### Adding an error code

An error code is a wire contract, so it touches four places, and a check covers all but the first:

1. `ErrorCode` in `src/dtk/core/errors.py`, plus its entry in `HTTP_STATUS`, plus `NON_RETRYABLE` if retrying it can never help. Nothing tests `HTTP_STATUS` membership: a code missing from it raises `KeyError` the first time anything asks for its status, at runtime. Add the entry in the same edit as the enum member.
2. `src/dtk/i18n/locales/errors.en.json` and `errors.zh.json`.
3. `code.<CODE>` and `hint.<CODE>` in `web/src/locales/en/errors.json` and `zh/errors.json`.
4. `ERROR_CODES` in `web/src/lib/api.ts`. `isErrorCode()` gates on that array, so a code missing there is silently rewritten to `INTERNAL` — the console then shows "an unexpected internal error" for a condition the server explained precisely.

`check-i18n.mjs` parses the enum straight out of `errors.py` and fails on a gap in 3 or 4 — and in 4 in either direction: a code with no console copy, and a console code the API never sends. It reads only `web/src/locales/`, so the backend catalogue (2) is not its business; `tests/unit/test_i18n.py` covers that one.

## Commit messages

Conventional-commit prefixes, with an optional scope: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `perf`, `ci`. Scopes in use include `console`, `signing`, `scheduler`, `services`, `db`, `api`, `i18n`, `core`.

The subject line is lowercase after the prefix and says **what changed in terms of behaviour**, not which files moved:

```
fix: a post that does not exist is an answer, not risk control
feat: show what the refill job is doing, failures included
feat(console): add an MCP page to the console
```

The body is where this project's commit history earns its keep, and long bodies are normal here. Say what you measured, what you tried, what you deliberately did not change, and what the failure mode was if the change is a fix. If a number in the diff came from a measurement, put the measurement in the message: two identities, two responses, the sizes, the date. A reviewer reading a diff six months later cannot re-derive that, and a comment in the code cannot carry all of it.

## Opening a pull request

Pull requests target **`v5`**. CI runs on `push` and `pull_request` against that branch, and `main` is V4.

There is no pull-request template. Before you open one:

1. `make fmt` — then the format gate passes rather than failing on whitespace.
2. `make lint && make type` — ruff and mypy.
3. `make test` — unit, replay and integration, with the fixtures managed for you.
4. `(cd web && npm run verify)` — if you touched anything under `web/`.
5. Update the user documentation in `documents/en/` **and** `documents/zh/` if you changed something a reader would act on: a setting, a CLI command, an endpoint, a console page, a default. The two language versions are the same document and are expected to stay in step.
6. Check that nothing you added is CJK in source, a hex colour outside the tokens file, a literal string in JSX, a `TODO`, a `.env`, or a fixture with a real cookie in it. The hygiene tests will catch all six, and it is quicker to notice yourself.

In the description, say what the change does and why, and how you verified it. If you could not verify something — a code path that needs the live platform, a browser backend you do not have installed — say that too. An honest "not verified against a real browser" is worth more than a confident claim a reviewer has to disprove.

CI has five gating jobs: `static`, `unit`, `integration`, `console`, `image`. The sixth, `contract`, never blocks a merge — it is gated on a `schedule` trigger the workflow does not declare yet, so it does not run in CI at all and you run it by hand.

## Related pages

- [Installation and deployment](./02-installation.md) — the compose stack you will be testing against.
- [Configuration reference](./03-configuration.md) — every setting, its scope and its default.
- [Concepts](./04-concepts.md) — identities, the scheduler, outcomes, the circuit breaker.
- [REST API guide](./11-api.md) — the envelope, scopes, `?wait=`, `?refresh=`, `?explain=`.
- [MCP and AI agents](./12-mcp.md) — the eight tools and why there are only eight.
- [CLI reference](./13-cli.md) — every command, and which ones are safe against a live instance.
- [Troubleshooting](./14-troubleshooting.md) — reading a failure before you conclude it is a bug.
- [Security](./15-security.md) — the master key, the allowlists, what leaves the instance.
