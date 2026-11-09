"""docker/package-mirrors.sh: where a locally built image installs packages from.

Run here against a scratch root rather than inside a build, because the cases
that matter are the ones a build hides: a mirror that is accepted and changes
nothing, and a value that is not the address it looks like (issue 817).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "package-mirrors.sh"

#: What python:3.12.8-slim-bookworm ships, copied out of the image.
BOOKWORM_SOURCES = """\
Types: deb
URIs: http://deb.debian.org/debian
Suites: bookworm bookworm-updates
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://deb.debian.org/debian-security
Suites: bookworm-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
"""

#: The one-line format older Debian images used, security host and all.
BULLSEYE_SOURCES_LIST = """\
deb http://deb.debian.org/debian bullseye main
deb http://security.debian.org/debian-security bullseye-security main
"""

TUNA = "https://mirrors.tuna.tsinghua.edu.cn"
TUNA_PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _bookworm_root(root: Path) -> Path:
    sources = root / "etc" / "apt" / "sources.list.d"
    sources.mkdir(parents=True)
    (sources / "debian.sources").write_text(BOOKWORM_SOURCES, encoding="utf-8")
    return root


def _sources(root: Path) -> str:
    return (root / "etc" / "apt" / "sources.list.d" / "debian.sources").read_text(encoding="utf-8")


def _run(root: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), str(root)],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_nothing_changes_without_a_mirror(tmp_path: Path) -> None:
    root = _bookworm_root(tmp_path)

    result = _run(root)

    assert result.returncode == 0, result.stderr
    assert _sources(root) == BOOKWORM_SOURCES
    assert not (root / "etc" / "pip.conf").exists()


def test_empty_values_are_the_same_as_unset(tmp_path: Path) -> None:
    """compose passes `${APT_MIRROR:-}`, so an unset mirror arrives as ""."""
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR="", PYPI_MIRROR="")

    assert result.returncode == 0, result.stderr
    assert _sources(root) == BOOKWORM_SOURCES
    assert not (root / "etc" / "pip.conf").exists()


def test_an_apt_mirror_replaces_both_debian_archives(tmp_path: Path) -> None:
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR=TUNA)

    assert result.returncode == 0, result.stderr
    text = _sources(root)
    assert f"URIs: {TUNA}/debian\n" in text
    assert f"URIs: {TUNA}/debian-security\n" in text
    assert "deb.debian.org" not in text
    # The keyring is what makes any mirror safe to use for apt: it checks
    # Debian's signature whoever serves the files. It must survive untouched.
    assert text.count("Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg") == 2
    assert TUNA in result.stdout


def test_a_trailing_slash_does_not_double_up(tmp_path: Path) -> None:
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR=f"{TUNA}/")

    assert result.returncode == 0, result.stderr
    assert f"URIs: {TUNA}/debian\n" in _sources(root)
    assert "//debian" not in _sources(root).replace("https://", "")


def test_the_older_sources_list_is_rewritten_too(tmp_path: Path) -> None:
    """A BROWSER_BASE_IMAGE override may still carry the one-line format."""
    apt = tmp_path / "etc" / "apt"
    apt.mkdir(parents=True)
    (apt / "sources.list").write_text(BULLSEYE_SOURCES_LIST, encoding="utf-8")

    result = _run(tmp_path, APT_MIRROR=TUNA)

    assert result.returncode == 0, result.stderr
    assert (apt / "sources.list").read_text(encoding="utf-8") == (
        f"deb {TUNA}/debian bullseye main\ndeb {TUNA}/debian-security bullseye-security main\n"
    )


def test_an_apt_mirror_with_nothing_to_replace_fails(tmp_path: Path) -> None:
    """Silently changing nothing is the failure the mainland guide warns about.

    Editing the wrong sources file on Ubuntu 24.04 neither errors nor helps; a
    build that did the same would go on to time out against deb.debian.org with
    the mirror apparently set.
    """
    sources = tmp_path / "etc" / "apt" / "sources.list.d"
    sources.mkdir(parents=True)
    ubuntu = "Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n"
    (sources / "ubuntu.sources").write_text(ubuntu, encoding="utf-8")

    result = _run(tmp_path, APT_MIRROR=TUNA)

    assert result.returncode != 0
    assert "APT_MIRROR" in result.stderr
    assert (sources / "ubuntu.sources").read_text(encoding="utf-8") == ubuntu


@pytest.mark.parametrize(
    "value",
    [
        "mirrors.tuna.tsinghua.edu.cn",
        "ftp://mirrors.tuna.tsinghua.edu.cn",
        # Characters that would end the substitution early or be read as part
        # of its syntax, so the address written would not be the one given.
        "https://mirror.example|evil",
        "https://mirror.example&evil",
        "https://mirror.example/a b",
    ],
)
def test_an_apt_mirror_that_is_not_a_plain_address_is_refused(tmp_path: Path, value: str) -> None:
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR=value)

    assert result.returncode != 0
    assert "APT_MIRROR" in result.stderr
    assert _sources(root) == BOOKWORM_SOURCES


def test_a_plain_http_apt_mirror_is_accepted(tmp_path: Path) -> None:
    """apt checks the signature, not the transport, and Debian's own default is http."""
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR="http://mirrors.ustc.edu.cn")

    assert result.returncode == 0, result.stderr
    assert "URIs: http://mirrors.ustc.edu.cn/debian\n" in _sources(root)


def test_a_pypi_mirror_becomes_the_index_for_every_later_pip(tmp_path: Path) -> None:
    root = _bookworm_root(tmp_path)

    result = _run(root, PYPI_MIRROR=TUNA_PYPI)

    assert result.returncode == 0, result.stderr
    assert (root / "etc" / "pip.conf").read_text(encoding="utf-8") == (
        f"[global]\nindex-url = {TUNA_PYPI}\n"
    )
    assert _sources(root) == BOOKWORM_SOURCES
    assert TUNA_PYPI in result.stdout


def test_a_pypi_mirror_needs_no_apt_sources(tmp_path: Path) -> None:
    """The transcriber image takes only a PyPI mirror."""
    result = _run(tmp_path, PYPI_MIRROR=TUNA_PYPI)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "etc" / "pip.conf").exists()


@pytest.mark.parametrize(
    "value",
    [
        # The requirements pin versions, not hashes, so whoever can rewrite a
        # plaintext response could hand pip a different package.
        "http://pypi.tuna.tsinghua.edu.cn/simple",
        "pypi.tuna.tsinghua.edu.cn/simple",
        "https://pypi.example/simple extra",
        "https://pypi.example/simple\n[global]",
    ],
)
def test_a_pypi_mirror_must_be_a_plain_https_address(tmp_path: Path, value: str) -> None:
    result = _run(tmp_path, PYPI_MIRROR=value)

    assert result.returncode != 0
    assert "PYPI_MIRROR" in result.stderr
    assert not (tmp_path / "etc" / "pip.conf").exists()


def test_one_bad_mirror_leaves_the_other_unapplied(tmp_path: Path) -> None:
    """Both are checked before either is written: half a mirror set is a confusing build."""
    root = _bookworm_root(tmp_path)

    result = _run(root, APT_MIRROR=TUNA, PYPI_MIRROR="http://pypi.example/simple")

    assert result.returncode != 0
    assert _sources(root) == BOOKWORM_SOURCES
    assert not (root / "etc" / "pip.conf").exists()
