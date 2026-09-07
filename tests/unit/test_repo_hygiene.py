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
            for match in suspicious.findall(text):
                # sec_user_id values are legitimately long and are public ids.
                if match.startswith("MS4wLjAB"):
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
