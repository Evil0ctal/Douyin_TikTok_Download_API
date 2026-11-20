"""What a platform's Set-Cookie may change in an identity's jar.

Each refusal here is a way a response could turn a working identity into a
broken one; see the module docstring of `dtk.identity.rotation` for the
measurements behind them.
"""

from __future__ import annotations

import pytest

from dtk.core.types import Platform
from dtk.identity.importing import to_cookie_header
from dtk.identity.rotation import MAX_JAR_BYTES, MAX_VALUE_LENGTH, SEALED_MINIMUMS, rotate

USABLE = "u" * 152
BOOTSTRAP = "b" * 128


class TestWhatIsKept:
    def test_a_rotated_value_replaces_the_old_one(self) -> None:
        rotation = rotate(Platform.TIKTOK, {"odin_tt": "old", "ttwid": "t"}, {"odin_tt": "new"})

        assert rotation.jar == {"odin_tt": "new", "ttwid": "t"}
        assert rotation.changed == ("odin_tt",)
        assert rotation.refused == ()

    def test_a_new_cookie_is_added(self) -> None:
        rotation = rotate(Platform.DOUYIN, {"ttwid": "t"}, {"odin_tt": "o"})

        assert rotation.jar == {"ttwid": "t", "odin_tt": "o"}
        assert rotation.changed == ("odin_tt",)

    def test_the_same_value_again_is_no_change(self) -> None:
        """The common case, and it must not cost a write."""
        rotation = rotate(Platform.TIKTOK, {"odin_tt": "o"}, {"odin_tt": "o"})

        assert rotation.changed == ()
        assert rotation.refused == ()

    def test_the_jar_passed_in_is_not_modified(self) -> None:
        jar = {"odin_tt": "old"}
        rotate(Platform.TIKTOK, jar, {"odin_tt": "new"})

        assert jar == {"odin_tt": "old"}


class TestTheSealedToken:
    def test_the_bootstrap_token_never_replaces_a_usable_one(self) -> None:
        """TikTok's HTML sets the 128-character bootstrap msToken, and dtk reads
        that page as the identity to learn its device id."""
        rotation = rotate(Platform.TIKTOK, {"msToken": USABLE}, {"msToken": BOOTSTRAP})

        assert rotation.jar["msToken"] == USABLE
        assert rotation.refused == ("msToken",)

    def test_nor_fills_an_empty_slot(self) -> None:
        """TikTok lets a request with no token through and refuses one with the
        wrong token, so a bootstrap value is worse than none."""
        rotation = rotate(Platform.TIKTOK, {}, {"msToken": BOOTSTRAP})

        assert "msToken" not in rotation.jar

    def test_a_usable_token_rotates(self) -> None:
        rotation = rotate(Platform.TIKTOK, {"msToken": USABLE}, {"msToken": "n" * 148})

        assert rotation.jar["msToken"] == "n" * 148

    def test_douyin_does_not_check_its_ms_token_and_neither_do_we(self) -> None:
        rotation = rotate(Platform.DOUYIN, {"msToken": USABLE}, {"msToken": BOOTSTRAP})

        assert rotation.jar["msToken"] == BOOTSTRAP

    def test_the_minimum_is_the_one_browser_rpc_mints_against(self) -> None:
        assert SEALED_MINIMUMS[Platform.TIKTOK]["msToken"] == 144


class TestWhatWouldCorruptTheJar:
    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("odin_tt", "a;b"),
            ("odin_tt", "a b"),
            ("odin_tt", 'a"b'),
            ("odin_tt", "a,b"),
            ("odin_tt", "a\\b"),
            ("odin_tt", "a\x7fb"),
            ("odin_tt", "x" * (MAX_VALUE_LENGTH + 1)),
            ("bad name", "v"),
            ("bad;name", "v"),
            ("", "v"),
        ],
    )
    def test_it_is_refused_and_the_old_value_kept(self, name: str, value: str) -> None:
        jar = {"odin_tt": "old"}
        rotation = rotate(Platform.TIKTOK, jar, {name: value})

        assert rotation.jar == jar
        assert rotation.changed == ()
        assert rotation.refused == (name,)

    def test_a_refused_cookie_does_not_stop_the_others(self) -> None:
        rotation = rotate(Platform.TIKTOK, {}, {"odin_tt": "a;b", "ttwid": "fine"})

        assert rotation.jar == {"ttwid": "fine"}
        assert rotation.refused == ("odin_tt",)


class TestAJarThatWouldNotStopGrowing:
    def full_jar(self) -> dict[str, str]:
        jar: dict[str, str] = {}
        index = 0
        while len(to_cookie_header(jar)) < MAX_JAR_BYTES - 100:
            jar[f"c{index}"] = "v" * 200
            index += 1
        return jar

    def test_no_new_names_past_the_limit(self) -> None:
        jar = self.full_jar()
        rotation = rotate(Platform.TIKTOK, jar, {"per_request_1": "v" * 200})

        assert "per_request_1" not in rotation.jar
        assert rotation.refused == ("per_request_1",)

    def test_existing_names_still_rotate(self) -> None:
        jar = self.full_jar()
        rotation = rotate(Platform.TIKTOK, jar, {"c0": "w" * 200})

        assert rotation.jar["c0"] == "w" * 200

    def test_the_size_counted_is_the_header_that_is_stored(self) -> None:
        jar = {"a": "1"}
        offered = {f"n{index}": "x" * 50 for index in range(400)}
        rotation = rotate(Platform.DOUYIN, jar, offered)

        assert len(to_cookie_header(rotation.jar)) <= MAX_JAR_BYTES
        assert rotation.refused
