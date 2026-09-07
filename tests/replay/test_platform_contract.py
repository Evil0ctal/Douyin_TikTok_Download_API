"""Cross-platform contract checks and fixture hygiene.

Two things nothing else can check on its own:

* **Normalization parity.** ``docs/design/11-data-contracts.md``, acceptance
  criterion 1: for the same logical object both platforms must produce the
  *same field set*, differing only in which fields are ``None``. A per-platform
  test suite cannot notice when the two drift apart.
* **Fixture hygiene.** ``docs/design/13-testing.md`` asks CI to reject a fixture
  containing a credential-shaped key. Fixtures come from captured traffic, and
  one echoed cookie in a committed file is a leak that outlives the commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from dtk.core.types import Platform
from dtk.models import Author, Comment, Content
from dtk.platforms import available_platforms, get_adapter
from dtk.platforms.base import PlatformAdapter
from tests.replay.support import (
    FETCHED_AT,
    FIXTURE_NAMES,
    FIXTURES,
    FORBIDDEN_KEY_PATTERN,
    keys_in,
    load,
)

PLATFORMS = ("douyin", "tiktok")

CONTENT_FIXTURES = (
    "video_normal",
    "video_image_album",
    "video_deleted",
    "video_private",
    "video_long_desc",
)


# --------------------------------------------------------------------------
# Adapter discovery
# --------------------------------------------------------------------------


def test_every_platform_is_discovered_without_a_registry_edit() -> None:
    """Adding a platform must need only a new platforms/<name>/ directory."""
    assert available_platforms() == PLATFORMS


@pytest.mark.parametrize("name", PLATFORMS)
def test_adapter_satisfies_the_protocol(name: str) -> None:
    adapter = get_adapter(name)
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform == Platform(name)
    assert len(adapter.endpoints) == 5


def test_unknown_platform_is_rejected() -> None:
    from dtk.core.errors import DtkError

    with pytest.raises(DtkError):
        get_adapter("bilibili")


def test_discovery_ignores_a_subpackage_that_is_not_a_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A helper subpackage must not take the whole registry down with it."""
    import sys

    import dtk.platforms as platforms_package
    from dtk.platforms import registry

    helper = tmp_path / "helpers"
    helper.mkdir()
    (helper / "__init__.py").write_text("", encoding="utf-8")

    monkeypatch.setattr(platforms_package, "__path__", [*platforms_package.__path__, str(tmp_path)])
    registry._discover.cache_clear()
    try:
        assert available_platforms() == PLATFORMS
    finally:
        registry._discover.cache_clear()
        sys.modules.pop("dtk.platforms.helpers", None)


@pytest.mark.parametrize("name", PLATFORMS)
def test_the_endpoint_table_cannot_be_mutated_through_an_adapter(name: str) -> None:
    """One table instance is shared process-wide; it has to be read only."""
    adapter = get_adapter(name)
    with pytest.raises(TypeError):
        adapter.endpoints.specs[f"{name}.injected"] = (  # type: ignore[index]
            adapter.endpoints[f"{name}.comments"]
        )


def test_a_bug_inside_a_builder_is_not_relabelled_as_a_bad_parameter() -> None:
    """INVALID_PARAM tells the caller to fix their input; a builder bug is ours."""
    from dtk.core.errors import DtkError
    from dtk.platforms.base import EndpointSpec

    def broken(**_: Any) -> dict[str, str]:
        return {"count": "x" + 1}  # type: ignore[operator]

    spec = EndpointSpec(name="test.broken", path="https://example.invalid/", build=broken)
    with pytest.raises(TypeError):
        spec.build_params()

    def picky(*, needed: str) -> dict[str, str]:
        return {"needed": needed}

    unbindable = EndpointSpec(name="test.picky", path="https://example.invalid/", build=picky)
    with pytest.raises(DtkError) as excinfo:
        unbindable.build_params(unexpected="1")
    assert excinfo.value.code.value == "INVALID_PARAM"


@pytest.mark.parametrize("name", PLATFORMS)
def test_endpoint_names_are_namespaced_and_carry_a_risk_weight(name: str) -> None:
    """Endpoint names are Redis keys for the scheduler, so they must not collide."""
    adapter = get_adapter(name)
    for key in adapter.endpoints.names():
        spec = adapter.endpoints[key]
        assert key.startswith(f"{name}.")
        assert spec.name == key
        assert spec.risk_weight >= 1.0
        assert spec.path.startswith("https://")
        assert spec.summary


def test_listing_endpoints_carry_more_risk_than_detail_lookups() -> None:
    """Paging a timeline is the pattern platforms watch; a detail hit is not."""
    for name in PLATFORMS:
        adapter = get_adapter(name)
        detail = adapter.endpoints[f"{name}.content_detail"].risk_weight
        posts = adapter.endpoints[f"{name}.author_posts"].risk_weight
        assert posts > detail


# --------------------------------------------------------------------------
# Normalization parity
# --------------------------------------------------------------------------


def _model_shape(model: Any) -> set[str]:
    return set(type(model).model_fields)


def test_both_platforms_produce_the_same_content_shape() -> None:
    parsed = {
        name: get_adapter(name).parse_content(load(name, "video_normal"), fetched_at=FETCHED_AT)
        for name in PLATFORMS
    }
    shapes = {name: _model_shape(content) for name, content in parsed.items()}
    assert shapes["douyin"] == shapes["tiktok"] == set(Content.model_fields)

    for content in parsed.values():
        assert isinstance(content.content_id, str)
        assert isinstance(content.author.uid, str)
        assert content.fetched_at == FETCHED_AT
        assert content.web_url.startswith("https://")
        assert content.media.video is not None


def test_both_platforms_produce_the_same_author_shape() -> None:
    authors = {
        name: get_adapter(name).parse_author(load(name, "user_profile")) for name in PLATFORMS
    }
    for name, author in authors.items():
        assert _model_shape(author) == set(Author.model_fields)
        assert author.platform == Platform(name)
        assert isinstance(author.uid, str)
        assert author.stats is not None
        assert author.stats.follower_count == 128400
        assert author.stats.content_count == 213


def test_both_platforms_produce_the_same_comment_shape() -> None:
    pages = {
        name: get_adapter(name).parse_comments(load(name, "comments_with_replies"))
        for name in PLATFORMS
    }
    for name, page in pages.items():
        assert page.has_more is True
        assert page.cursor == "20"
        assert len(page.items) == 2
        first = page.items[0]
        assert _model_shape(first) == set(Comment.model_fields)
        assert first.platform == Platform(name)
        assert first.parent_id is None
        assert first.is_pinned is True
        assert first.reply_count == 2


@pytest.mark.parametrize("name", PLATFORMS)
@pytest.mark.parametrize("fixture_name", CONTENT_FIXTURES)
def test_identifiers_are_always_strings(name: str, fixture_name: str) -> None:
    """A 19 digit id parsed as int silently loses precision in the browser."""
    content = get_adapter(name).parse_content(load(name, fixture_name), fetched_at=FETCHED_AT)
    assert isinstance(content.content_id, str)
    assert isinstance(content.author.uid, str)
    if content.music is not None and content.music.music_id is not None:
        assert isinstance(content.music.music_id, str)


@pytest.mark.parametrize("name", PLATFORMS)
def test_albums_have_images_and_no_duration(name: str) -> None:
    content = get_adapter(name).parse_content(
        load(name, "video_image_album"), fetched_at=FETCHED_AT
    )
    assert content.kind.value == "image_album"
    assert content.media.images
    assert content.media.video is None
    assert content.duration_ms is None


@pytest.mark.parametrize("name", PLATFORMS)
def test_every_media_url_keeps_its_mirrors(name: str) -> None:
    """Downloads run browser-to-CDN, so one dead link must not be fatal."""
    content = get_adapter(name).parse_content(load(name, "video_normal"), fetched_at=FETCHED_AT)
    assert content.media.video is not None
    assert content.media.video.url == content.media.video.urls[0]
    assert len(content.media.video.urls) >= 2
    for cover in content.media.covers:
        assert cover.url == cover.urls[0]


# --------------------------------------------------------------------------
# Fixture hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", PLATFORMS)
def test_every_platform_ships_the_full_fixture_set(name: str) -> None:
    present = {path.stem for path in (FIXTURES / name).glob("*.json")}
    assert present == set(FIXTURE_NAMES)


@pytest.mark.parametrize("name", PLATFORMS)
def test_fixtures_contain_no_credential_shaped_keys(name: str) -> None:
    for fixture_name in FIXTURE_NAMES:
        payload = load(name, fixture_name)
        offenders = sorted(key for key in keys_in(payload) if FORBIDDEN_KEY_PATTERN.search(key))
        assert offenders == [], f"{name}/{fixture_name}.json leaks: {offenders}"


#: Public page domains a fixture may legitimately name. Everything else - every
#: media and avatar host - must sit under ``.invalid`` (reserved by RFC 2606),
#: so no fixture can carry a still-valid signed CDN link and no test can reach
#: the network by accident.
ALLOWED_PAGE_HOSTS = frozenset({"www.douyin.com", "www.iesdouyin.com", "www.tiktok.com"})


def _urls_in(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload] if payload.startswith(("http://", "https://")) else []
    if isinstance(payload, dict):
        return [url for value in payload.values() for url in _urls_in(value)]
    if isinstance(payload, list):
        return [url for item in payload for url in _urls_in(item)]
    return []


@pytest.mark.parametrize("name", PLATFORMS)
def test_fixture_media_hosts_can_never_resolve(name: str) -> None:
    for fixture_name in FIXTURE_NAMES:
        payload = load(name, fixture_name)
        for url in _urls_in(payload):
            host = urlsplit(url).netloc
            assert host.endswith(".invalid") or host in ALLOWED_PAGE_HOSTS, (
                f"{name}/{fixture_name}.json points at a live host: {host}"
            )


def test_fixture_readme_states_they_are_hand_built() -> None:
    """The provenance warning must not quietly disappear on a later edit."""
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert "hand-built" in readme.lower()
    assert "16-salvage-and-debug.md" in readme
