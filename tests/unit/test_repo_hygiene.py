"""Repository hygiene rules that CI enforces.

Each of these guards a rule that is easy to state and easy to forget, and where
forgetting is silent. They are cheap to run and they are the only thing standing
between a convention and its slow erosion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dtk.signing.native.abogus import structure_error

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "dtk"
TESTS = REPO / "tests"
WEB = REPO / "web" / "src"

CJK = re.compile("[\\u4e00-\\u9fff\\u3040-\\u30ff]")

#: Key names that would mean a fixture carries a real credential.
CREDENTIAL_KEYS = re.compile(
    r"^(cookie|cookies|set-cookie|sessionid|sessionid_ss|sid_tt|sid_guard|uid_tt|"
    r"odin_tt|passport_csrf_token|authorization|api_key|apikey|access_token|"
    r"refresh_token|password)$",
    re.IGNORECASE,
)

#: Names that are credentials in some APIs but ordinary fields in these ones.
#: Douyin's user object carries `secret` as a 0/1 private-account flag, and
#: several list responses use `token` as an opaque pagination cursor. Flagging
#: them by name alone produces noise that trains people to ignore this check, so
#: they are judged by their value instead.
AMBIGUOUS_KEYS = {"secret", "token"}


def _python_sources(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and "migrations" not in p.parts
    ]


class TestSourceLanguage:
    """docs/design/14-i18n.md rule 1: code is English, no exceptions.

    Writing a comment in one's own language is always the path of least
    resistance, so nothing but an automated check keeps this true over time.
    """

    def test_no_cjk_in_python_sources(self):
        offenders = []
        for path in _python_sources(SRC):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if CJK.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:80]}")
        assert not offenders, "CJK found in source:\n" + "\n".join(offenders[:20])

    def test_no_cjk_in_test_sources_except_declared_fixtures(self):
        """Tests may contain CJK only as INPUT data.

        Real users paste Chinese share text, and the URL extractor has to cope
        with it, so pretending otherwise would weaken the tests. What must stay
        English is anything that is code: names, comments, assertions.
        """
        allowed = {"test_urls.py", "test_i18n.py", "test_identity_importing.py"}
        offenders = []
        for path in _python_sources(TESTS):
            if path.name in allowed:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if CJK.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
        assert not offenders, "CJK in tests outside the allowlist:\n" + "\n".join(offenders[:20])


class TestFixtureSafety:
    """docs/design/13-testing.md: fixtures are captured responses, so they must
    be scrubbed before they enter the repository."""

    def _fixture_files(self) -> list[Path]:
        root = TESTS / "fixtures"
        return sorted(root.rglob("*.json")) if root.exists() else []

    def test_fixtures_exist(self):
        assert self._fixture_files(), "no parser fixtures; replay tests cannot be meaningful"

    def test_no_credential_shaped_keys(self):
        offenders = []

        def walk(node, path, file):
            if isinstance(node, dict):
                for key, value in node.items():
                    name = str(key)
                    if CREDENTIAL_KEYS.match(name):
                        offenders.append(f"{file}: {path}.{key}")
                    elif (
                        name.lower() in AMBIGUOUS_KEYS
                        and isinstance(value, str)
                        and len(value) > 16
                    ):
                        offenders.append(f"{file}: {path}.{key} (long string value)")
                    walk(value, f"{path}.{key}", file)
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{path}[{i}]", file)

        for path in self._fixture_files():
            walk(json.loads(path.read_text(encoding="utf-8")), "$", path.relative_to(REPO))
        assert not offenders, "credential-shaped keys in fixtures:\n" + "\n".join(offenders)

    def test_no_long_opaque_tokens(self):
        """A scrubber that renames the key but keeps the value is no scrubber."""
        suspicious = re.compile(r"\b[A-Za-z0-9_-]{80,}\b")
        offenders = []
        for path in self._fixture_files():
            text = path.read_text(encoding="utf-8")
            # Every string in the file that is provably an a_bogus signature.
            # Collected whole, because the pattern above breaks on the '/' and
            # '=' an a_bogus contains and would otherwise only ever see a
            # fragment of one.
            signatures = [
                token
                for token in re.findall(r'"([A-Za-z0-9_/+=-]{150,})"', text)
                if structure_error(token) is None
            ]
            for match in suspicious.findall(text):
                # sec_user_id values are legitimately long and are public ids.
                if match.startswith("MS4wLjAB"):
                    continue
                # An a_bogus signature is 192 opaque characters and carries no
                # session value - it is a function of the query, the clock and
                # the window geometry, and nothing else. Allowed on proof
                # rather than on filename: `structure_error` returns None only
                # for something that decodes under the real format, verifies
                # its own internal checksum, and therefore cannot be a cookie
                # that happened to land in a signing fixture.
                if any(match in signature for signature in signatures):
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {match[:40]}...")
        assert not offenders, "long opaque strings in fixtures:\n" + "\n".join(offenders[:10])


class TestNoLeftoverPlaceholders:
    def test_no_not_implemented_in_shipped_code(self):
        """Catch unfinished work, not the two legitimate uses.

        An abstract base method raising NotImplementedError is a contract, and
        `contextlib.suppress(NotImplementedError, ...)` is how one copes with a
        platform that lacks a capability - neither is an unfinished stub.
        """
        offenders = []
        for path in _python_sources(SRC):
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                if "NotImplementedError" not in line:
                    continue
                if "suppress(" in line or "abstract" in line.lower():
                    continue
                previous = lines[lineno - 2] if lineno >= 2 else ""
                if "abstract" in previous.lower() or "pragma: no cover" in previous:
                    continue
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:70]}")
        assert not offenders, "unfinished stubs:\n" + "\n".join(offenders)

    def test_no_todo_markers_in_source(self):
        marker = re.compile(r"\b(TODO|FIXME|XXX)\b")
        offenders = [
            f"{p.relative_to(REPO)}:{i}"
            for p in _python_sources(SRC)
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if marker.search(line)
        ]
        assert not offenders, "unfinished markers in source:\n" + "\n".join(offenders[:20])


class TestSecretsNeverCommitted:
    """The V4 lesson. A live Douyin session sat in a tracked config.yaml and was
    one command away from being published; see docs/design/08-security.md."""

    def test_no_session_cookies_anywhere_in_the_tree(self):
        pattern = re.compile(
            r"\b(sessionid|sid_guard|uid_tt|passport_auth_mix_state)\s*=\s*[A-Za-z0-9%]{16,}"
        )
        offenders = []
        for path in list(_python_sources(SRC)) + list(_python_sources(TESTS)):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and "SYNTHETIC" not in line:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
        assert not offenders, "possible real session cookie:\n" + "\n".join(offenders)

    def test_env_example_has_no_filled_secrets(self):
        example = REPO / ".env.example"
        if not example.exists():
            pytest.skip(".env.example not present")
        for line in example.read_text(encoding="utf-8").splitlines():
            if line.startswith(("DTK_SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD")):
                _key, _, value = line.partition("=")
                assert not value.strip(), f"{_key} must ship empty, found a value"


@pytest.mark.skipif(not (WEB).exists(), reason="console not built yet")
class TestConsoleHygiene:
    """docs/design/12-design-system.md: colours come from tokens, never literals."""

    def test_no_literal_hex_colours_in_components(self):
        hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
        offenders = []
        for path in list(WEB.rglob("*.tsx")) + list(WEB.rglob("*.ts")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if hex_re.search(line) and "tokens" not in path.name:
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()[:70]}")
        assert not offenders, "literal colours outside tokens:\n" + "\n".join(offenders[:20])

    def test_both_themes_define_the_same_tokens(self):
        """A token in one palette and not the other breaks exactly one theme.

        The failure is quiet: the missing side inherits whatever the dark
        default block set, so the theme that was not tested looks nearly right
        and is wrong in one colour. Doc 12 says both themes are complete
        implementations rather than a base plus a filter; this is that sentence
        as a check.
        """
        tokens = (WEB / "styles" / "tokens.css").read_text(encoding="utf-8")
        blocks = re.split(r"^:root[^{]*\{", tokens, flags=re.M)[1:]
        assert len(blocks) >= 2, "expected a dark block and a light block"
        names = [set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", block, flags=re.M)) for block in blocks]
        dark, light = names[0], names[1]
        assert dark == light, (
            "tokens defined in only one theme: "
            f"dark only {sorted(dark - light)}, light only {sorted(light - dark)}"
        )

    def test_selected_text_is_not_the_hover_wash(self):
        """`--accent-subtle` is a 10% tint: right for hover, invisible as a highlight.

        It was what ::selection used, measuring 1.17:1 against the page in dark
        and 1.11:1 in light - and lower still inside an input, which is where
        text actually gets selected. The fix is a token of its own, so this
        guards the regression of reaching for the wash again.
        """
        base = (WEB / "styles" / "base.css").read_text(encoding="utf-8")
        rule = re.search(r"::selection\s*\{([^}]*)\}", base)
        assert rule is not None, "no ::selection rule"
        body = rule.group(1)
        assert "--accent-subtle" not in body
        assert "--selection-bg" in body
        # Both halves, or the browser picks its own text colour on some
        # platforms and undoes the contrast the pair was measured for.
        assert "--selection-text" in body

    def test_the_signing_examples_carry_no_credential(self):
        """They were built from real browser requests, which is the whole risk.

        A cookie jar is a live login, and `msToken`, `uifid` and the per-visitor
        ids are the same thing by another name. The examples exist to show the
        SHAPE of a request - which parameters, which cookie names - so anything
        that looks like a real value is a mistake, and one that would be
        committed to a public repository.

        Checked by name and by shape rather than against a denylist of the
        values that happened to be pasted once: the next example will be pasted
        from a different browser.
        """
        source = (WEB / "lib" / "signingExamples.ts").read_text(encoding="utf-8")
        # Strip the comments, which legitimately name these parameters while
        # explaining why they are absent.
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

        secrets = ("a_bogus", "verifyFp", "X-Gnarly", "X-Bogus", "X-Dynosaur", "odinId")
        present = [name for name in secrets if name in code]
        assert not present, f"signature parameters in the examples: {present}"

        # Every cookie value is a placeholder. A real one is long and random;
        # these say what they are.
        for pair in re.findall(r"(\w+)=([A-Za-z0-9_%+./-]+)", code):
            name, value = pair
            if name in {"msToken", "ttwid", "odin_tt", "UIFID_TEMP", "tt_csrf_token", "uifid"}:
                assert value.startswith("REPLACE_"), f"{name} has a real-looking value"

    def test_opening_an_endpoint_always_goes_through_a_confirmation(self):
        """The one invariant on the endpoint-access page worth a test.

        Removing a credential check is the only irreversible-feeling thing that
        page does, and it is guarded by a confirm dialog. The guard is easy to
        lose while rearranging the switch: it was inverted so that ON means
        "requires a key" - which matches how people read a switch, all-on being
        all-safe - and inverting the control without inverting the handler would
        have moved the confirmation onto the harmless direction and let the
        dangerous one through silently.

        Stated as "no call site opens an endpoint except the dialog's own", so
        it survives the next rearrangement of the control.
        """
        source = (WEB / "pages" / "EndpointAccess.tsx").read_text(encoding="utf-8")
        opens = re.findall(r"apply\(\s*(\w+)\s*,\s*true\s*\)", source)
        assert opens == ["pending"], (
            "an endpoint is opened from somewhere other than the confirm dialog: "
            f"apply(..., true) called with {opens}"
        )
        # And the dialog is what holds `pending`, so the row being opened is the
        # one the operator was shown.
        assert "setPending(row)" in source
        assert "open={pending !== null}" in source

    def test_no_cjk_outside_the_chinese_locale(self):
        offenders = []
        for path in list(WEB.rglob("*.tsx")) + list(WEB.rglob("*.ts")):
            if "locales" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if CJK.search(line):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
        assert not offenders, "CJK outside locales/zh:\n" + "\n".join(offenders[:20])

    def test_locale_key_sets_match(self):
        en_dir, zh_dir = WEB / "locales" / "en", WEB / "locales" / "zh"
        if not en_dir.exists() or not zh_dir.exists():
            pytest.skip("locales not present yet")

        def flatten(obj, prefix=""):
            out = set()
            for key, value in obj.items():
                full = f"{prefix}{key}"
                if isinstance(value, dict):
                    out |= flatten(value, f"{full}.")
                else:
                    out.add(full)
            return out

        for en_file in sorted(en_dir.glob("*.json")):
            zh_file = zh_dir / en_file.name
            assert zh_file.exists(), f"missing Chinese catalogue: {en_file.name}"
            en_keys = flatten(json.loads(en_file.read_text(encoding="utf-8")))
            zh_keys = flatten(json.loads(zh_file.read_text(encoding="utf-8")))
            assert en_keys == zh_keys, (
                f"{en_file.name}: only in en {sorted(en_keys - zh_keys)[:8]}, "
                f"only in zh {sorted(zh_keys - en_keys)[:8]}"
            )


# --------------------------------------------------------------------------
# The settings registry and the console that renders it
#
# The console builds its groups from a hardcoded list while the server enumerates
# RUNTIME_SETTINGS, so a new setting appears in the UI on its own - silently
# filed under "Other" with an untranslated heading. That is the same silent
# fallthrough that made every tuned endpoint quota inert once (see
# tests/unit/test_policy_coverage.py); one prefix is a namespace shared by the
# registry, the console and both locale files, and nothing else joins them up.
# --------------------------------------------------------------------------

CONSOLE_SETTINGS = WEB / "pages" / "Settings.tsx"
#: Scheduler and pool settings render here instead, beside the pool they act on
#: and beside a description of the rotation they tune.
CONSOLE_SCHEDULER = WEB / "pages" / "Scheduler.tsx"


def _console_groups() -> list[str]:
    """Every group the console gives a heading of its own, on whichever page.

    Two pages now render runtime settings, so a group is "covered" if either one
    claims it. Reading only the settings page would call the scheduler's own
    settings homeless the moment they moved.
    """
    source = CONSOLE_SETTINGS.read_text(encoding="utf-8")
    block = re.search(r"const GROUP_ORDER = \[(.*?)\] as const", source, re.S)
    assert block is not None, "GROUP_ORDER is no longer declared the way this test reads it"
    groups = re.findall(r"'([^']+)'", block.group(1))

    scheduler = CONSOLE_SCHEDULER.read_text(encoding="utf-8")
    owned = re.search(r"const SECTIONS = \[(.*?)\] as const", scheduler, re.S)
    assert owned is not None, "SECTIONS is no longer declared the way this test reads it"
    return groups + re.findall(r"'([^']+)'", owned.group(1))


def _settings_page_groups() -> list[str]:
    """Only the ones the settings page itself lists, for the reverse check."""
    source = CONSOLE_SETTINGS.read_text(encoding="utf-8")
    block = re.search(r"const GROUP_ORDER = \[(.*?)\] as const", source, re.S)
    assert block is not None
    return re.findall(r"'([^']+)'", block.group(1))


def _setting_groups() -> set[str]:
    from dtk.core.config import RUNTIME_SETTINGS

    return {key.split(".")[0] for key in RUNTIME_SETTINGS}


def test_every_setting_group_has_a_console_group() -> None:
    missing = sorted(_setting_groups() - set(_console_groups()))
    assert not missing, (
        f"settings groups with no console group: {missing}. They will render under "
        f"'Other' with no heading of their own; add them to GROUP_ORDER in "
        f"{CONSOLE_SETTINGS.relative_to(REPO)}, or to SECTIONS in "
        f"{CONSOLE_SCHEDULER.relative_to(REPO)} if they belong to the scheduler."
    )


def test_no_console_group_is_left_without_settings() -> None:
    """The other direction: a group whose settings were renamed away."""
    stale = sorted(set(_console_groups()) - _setting_groups())
    assert not stale, f"console groups no setting uses any more: {stale}"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_console_group_is_named_in_both_languages(language: str) -> None:
    console = json.loads(
        (REPO / "web" / "src" / "locales" / language / "console.json").read_text(encoding="utf-8")
    )
    settings = console["settings"]
    for group in _settings_page_groups():
        assert group in settings["group"], f"{language}: settings.group.{group} is missing"
        assert group in settings["groupHint"], f"{language}: settings.groupHint.{group} is missing"


@pytest.mark.parametrize("language", ["en", "zh"])
def test_every_constrained_setting_has_its_choices_explained(language: str) -> None:
    """A picker whose options are bare keys makes the reader guess what they do."""
    from dtk.core.config import RUNTIME_SETTINGS

    console = json.loads(
        (REPO / "web" / "src" / "locales" / language / "console.json").read_text(encoding="utf-8")
    )
    explained = console["settings"].get("choice", {})
    for key, spec in sorted(RUNTIME_SETTINGS.items()):
        if not spec.choices:
            continue
        assert key in explained, f"{language}: settings.choice.{key} is missing"
        for choice in spec.choices:
            assert choice in explained[key], (
                f"{language}: settings.choice.{key}.{choice} is missing"
            )


# --------------------------------------------------------------------------
# The console's language seed
#
# src/dtk/api/console.py rewrites one marker in index.html to hand the SPA the
# language it negotiated for that request. If the marker is ever renamed in the
# frontend, the rewrite silently does nothing: the console still loads, still
# works, and just quietly ignores Accept-Language forever. That is a feature
# that would be lost without a single failing anything, so it is asserted here.
# --------------------------------------------------------------------------

INDEX_HTML = REPO / "web" / "index.html"


def test_index_html_carries_the_marker_the_server_rewrites() -> None:
    from dtk.api.console import LANGUAGE_MARKER

    html = INDEX_HTML.read_text(encoding="utf-8")
    assert LANGUAGE_MARKER in html, (
        f"web/index.html no longer contains {LANGUAGE_MARKER!r}, so "
        "console.localize() cannot seed the negotiated language and the console "
        "will silently fall back to navigator.language."
    )
    assert 'lang="en"' in html, "the document element must ship a lang the server can replace"


def test_the_console_only_trusts_a_lang_it_negotiated() -> None:
    """Both halves of the contract, asserted against the actual sources."""
    from dtk.api.console import LANGUAGE_MARKER

    language_ts = (WEB / "lib" / "language.ts").read_text(encoding="utf-8")
    assert "data-language-source" in language_ts, (
        "language.ts must gate on the source marker; reading lang unconditionally "
        "would pin every dev server to the value baked into index.html"
    )
    assert "'negotiated'" in language_ts

    console_py = (SRC / "api" / "console.py").read_text(encoding="utf-8")
    assert '"negotiated"' in console_py
    assert LANGUAGE_MARKER in console_py


# --------------------------------------------------------------------------
# Language reachability on the dead-end screens
#
# Every console surface is rendered inside the shell, which carries the language
# switcher in its top bar - except two, which replace the shell entirely: the
# gate's fatal-network panel and the root error boundary's fallback. Those are
# precisely the screens a user cannot navigate away from, so losing the switcher
# there means losing it for good. Neither renders TopBar, and nothing else would
# fail if the controls were dropped.
# --------------------------------------------------------------------------

#: Full-viewport surfaces that must offer the language switcher themselves.
DEAD_END_SCREENS = [
    ("App.tsx", "the setup gate's fatal network panel"),
    ("pages/Login.tsx", "the login page"),
    ("pages/Setup.tsx", "the first-run wizard"),
    ("components/ErrorBoundary.tsx", "the root error boundary's fallback"),
]


@pytest.mark.parametrize("relative, described", DEAD_END_SCREENS)
def test_shell_less_screens_carry_the_language_switcher(relative: str, described: str) -> None:
    source = (WEB / relative).read_text(encoding="utf-8")
    assert "<LanguageSwitcher />" in source, (
        f"{described} ({relative}) renders without the shell's top bar, so it must "
        "render LanguageSwitcher itself or the user is stranded in one language"
    )
    assert "<ThemeToggle />" in source, f"{described} ({relative}) is missing ThemeToggle"


def test_the_root_boundary_asks_for_the_full_screen_fallback() -> None:
    """The switcher only appears on the boundary's full-screen variant.

    The same component also guards the routed page inside the shell, where the
    top bar already offers both controls; only the root usage replaces it.
    """
    app = (WEB / "App.tsx").read_text(encoding="utf-8")
    assert "<ErrorBoundary fullScreen>" in app, (
        "the boundary wrapping <Gate /> must opt into the full-screen fallback; "
        "without it a render crash leaves no way to change language"
    )


# --------------------------------------------------------------------------
# How the console applies a language
#
# Four things here fail silently rather than loudly: a ?lang= link the console
# ignores, a <html lang> that stays English while the page is Chinese, a title
# frozen at build time, and a cache still holding text the server rendered in
# the previous language. None of them break a render, so none of them break a
# test unless one is written for them.
# --------------------------------------------------------------------------

CONSOLE_LANGUAGE_TS = WEB / "lib" / "language.ts"


def test_the_console_resolves_the_lang_query_parameter() -> None:
    source = CONSOLE_LANGUAGE_TS.read_text(encoding="utf-8")
    assert "QUERY_PARAM = 'lang'" in source and "URLSearchParams" in source, (
        "language.ts must resolve ?lang=. The API and /docs honour it "
        "(dtk/i18n/negotiate.py), and a link that re-languages two of the three "
        "surfaces is more confusing than one that re-languages none."
    )


def test_a_lang_link_does_not_overwrite_the_stored_preference() -> None:
    """A link says what this visit is, not what the reader prefers from now on.

    So the value from the URL is kept for the tab that opened it - long enough to
    survive the router dropping the query string - and never written to the
    persistent preference, which belongs to the switcher alone.
    """
    source = CONSOLE_LANGUAGE_TS.read_text(encoding="utf-8")
    assert "sessionStorage" in source, "the ?lang= override must be scoped to the tab"
    writes = source.count("writeStored(")
    assert writes == 1, (
        f"language.ts writes the persistent preference {writes} times; only the "
        "explicit switch in setLanguage() may do that, or one shared link "
        "re-languages every later session on that machine"
    )


def test_the_resolved_language_reaches_the_document_element() -> None:
    """<html lang> is what screen readers and :lang() rules read."""
    source = CONSOLE_LANGUAGE_TS.read_text(encoding="utf-8")
    assert "export function initLanguage" in source, (
        "the detected language must be stamped onto <html>; index.html ships "
        'lang="en" and the server rewrites it only for documents it served, so '
        "an auto-detected Chinese console would announce itself as English"
    )
    assert (WEB / "main.tsx").read_text(encoding="utf-8").count("initLanguage()") == 1


def test_the_document_title_follows_the_route_and_the_language() -> None:
    """index.html's <title> is a build-time constant; the SPA owns it after boot."""
    main_tsx = (WEB / "main.tsx").read_text(encoding="utf-8")
    assert "document.title" in main_tsx
    assert "languageChanged" in main_tsx, "the title must be rebuilt on a language switch"
    assert "popstate" in main_tsx, "and on a navigation, which is client-side here"


def test_a_language_switch_expires_the_cache_it_invalidated() -> None:
    query_ts = (WEB / "lib" / "query.ts").read_text(encoding="utf-8")
    assert "subscribeLanguage" in query_ts, (
        "nothing else expires the cache on a language switch, so server-rendered "
        "text stays on screen in the old language until the next poll"
    )
    assert ".clear()" not in query_ts, (
        "clearing the whole cache on every switch throws away rows that read the "
        "same in both languages; invalidate what the server actually renders"
    )


#: Route modules whose GET body the console caches and the server writes in the
#: caller's language. Each one is a prefix in LANGUAGE_SENSITIVE_PATHS
#: (web/src/lib/query.ts).
LOCALIZED_ROUTES_THE_CONSOLE_CACHES = {
    "api/routes/admin/health.py",  # the circuit breaker's reason sentence
    "api/routes/admin/settings.py",  # setting descriptions
    "api/routes/openapi.py",  # the document the reference pages render
    "api/routes/tasks.py",  # a stored task error, re-rendered per reader
}

#: Handlers that localize something the console never holds in its query cache:
#: a mutation's response, the failure envelope every route shares, and a feed the
#: console does not call at all.
LOCALIZED_ROUTES_OUTSIDE_THE_CACHE = {
    "api/routes/admin/identities.py",
    "api/routes/operations.py",
    "api/routes/ios.py",
    # POST /tasks/batch renders each rejected item's reason inline. A mutation
    # response, so nothing holds it across a language switch.
    "api/routes/content.py",
}


def test_every_endpoint_that_renders_text_is_known_to_the_console_cache() -> None:
    """The list the console invalidates against has to keep up with the API.

    A route that starts writing prose in the caller's language is invisible from
    the console side - the response keeps its shape, so a switch simply leaves
    the old sentences on screen until something else refetches.
    """
    localizing = {
        p.relative_to(SRC).as_posix()
        for p in _python_sources(SRC / "api" / "routes")
        if any(
            marker in p.read_text(encoding="utf-8")
            for marker in ("language(request)", "state.language", "resolve_language")
        )
    }
    unaccounted = sorted(
        localizing - LOCALIZED_ROUTES_THE_CONSOLE_CACHES - LOCALIZED_ROUTES_OUTSIDE_THE_CACHE
    )
    assert not unaccounted, (
        f"these routes now render text in the caller's language: {unaccounted}. If the "
        "console caches that GET, add its path to LANGUAGE_SENSITIVE_PATHS in "
        "web/src/lib/query.ts and list it in LOCALIZED_ROUTES_THE_CONSOLE_CACHES; "
        "otherwise list it in LOCALIZED_ROUTES_OUTSIDE_THE_CACHE and say why."
    )
    gone = sorted(LOCALIZED_ROUTES_THE_CONSOLE_CACHES - localizing)
    assert not gone, (
        f"{gone} no longer localize anything, so the console is expiring their "
        "responses on every language switch for nothing"
    )


# --------------------------------------------------------------------------
# Container resource limits
#
# "Overload cannot destabilise the main API" was an assumption until these
# existed: without cgroup limits a saturated container slows every Postgres
# write, which slows the api, which fails /readyz - and api is
# `restart: unless-stopped`, so it restarts in a loop while the cause carries on.
# --------------------------------------------------------------------------


def _compose_source() -> str:
    return (REPO / "docker" / "compose.yml").read_text(encoding="utf-8")


def test_every_service_has_a_memory_and_process_ceiling() -> None:
    """A leak should hit a wall, not consume an 8 GiB host."""
    import yaml

    compose = yaml.safe_load(_compose_source())
    anchors = _compose_source()
    for name, service in (compose.get("services") or {}).items():
        # The app services inherit theirs from the x-app-limits anchor, which
        # yaml.safe_load has already merged.
        assert service.get("mem_limit"), f"{name} has no mem_limit"
        assert service.get("pids_limit"), f"{name} has no pids_limit"
    assert "x-app-limits" in anchors


def test_the_heaviest_service_is_bounded_below_the_host() -> None:
    """browser-rpc measured 2.57 GiB and 629% CPU; unbounded it starves the rest."""
    import yaml

    compose = yaml.safe_load(_compose_source())
    browser = compose["services"]["browser-rpc"]

    assert browser["mem_limit"] == "4g"
    # Below the six cores observed: minting and signing are off the request
    # path, so this is the service that should yield when the machine is busy.
    assert float(browser["cpus"]) <= 4.0


def test_the_integration_suite_clears_every_table() -> None:
    """A table missing from TABLES leaks rows between tests.

    Found the hard way: `collections` was added to the schema and not to this
    list, and the first test to create one passed while every later test in the
    file failed on a unique name it had never seen. The failure looks like a bug
    in the feature rather than in the fixture, which is what makes it worth a
    test of its own.
    """
    from dtk.db.models import TABLE_NAMES
    from tests.integration.test_api_support import TABLES

    assert set(TABLE_NAMES) - set(TABLES) == set(), (
        "tables in the schema that the API suite never clears: "
        f"{sorted(set(TABLE_NAMES) - set(TABLES))}"
    )


def test_the_mcp_guide_lists_the_tools_the_server_registers() -> None:
    """The console's MCP page names every tool, and only tools that exist.

    The page is documentation, so nothing it says is executed and nothing type
    checks it. A tool renamed in `TOOL_METHODS` leaves the page advertising a
    name the server answers `unknown tool` to, and a tool added leaves it off a
    list the reader has no reason to distrust. Both fail silently and both are
    read by somebody writing a prompt against this instance.
    """
    from dtk.mcp.tools import TOOL_METHODS

    source = (WEB / "pages" / "Mcp.tsx").read_text(encoding="utf-8")
    block = re.search(r"^const TOOLS = \[(.*?)\] as const$", source, re.M | re.S)
    assert block is not None, "Mcp.tsx no longer declares a `const TOOLS = [...] as const`"

    listed = tuple(re.findall(r"'([a-z_]+)'", block.group(1)))
    assert listed == TOOL_METHODS, (
        "the MCP guide and the server disagree about the tool set: "
        f"page has {listed}, server registers {TOOL_METHODS}"
    )

    # Every one of them also needs a description in both languages, or the row
    # renders as its own key.
    for language in ("en", "zh"):
        console = json.loads(
            (REPO / "web" / "src" / "locales" / language / "console.json").read_text(
                encoding="utf-8"
            )
        )
        described = console.get("mcp", {}).get("tool", {})
        assert set(described) == set(TOOL_METHODS), (
            f"{language} console.json describes {sorted(described)}, "
            f"expected {sorted(TOOL_METHODS)}"
        )
