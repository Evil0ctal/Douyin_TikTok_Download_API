"""The Bilibili platform outside its parsers: ids, parameters, links, the rest.

Parsers are in ``tests/replay/test_bilibili_parser.py``, the follow-up call in
``tests/unit/test_fetch_follow_up.py`` and WBI in ``test_signing_wbi.py``.
"""

from __future__ import annotations

import base64
import json

import pytest

from dtk.core.errors import InvalidParam
from dtk.core.types import LoginNeed, Outcome, Platform
from dtk.identity.importing import REQUIRED, SESSION_MARKERS
from dtk.media.domains import allowed_mirrors
from dtk.ops.probes import SESSION_CHECKS, SMOKE_URLS, read_session
from dtk.platforms import get_adapter
from dtk.platforms.base import ClientProfile
from dtk.platforms.bilibili import endpoints, params
from dtk.platforms.bilibili.ids import av_to_bv, bv_to_av, content_bvid, numeric_id
from dtk.transport.base import Fingerprint, RawResponse
from dtk.transport.classify import Classifier
from dtk.urls import identify, require_content_id
from dtk.urls.parse import _clean_query


class TestIds:
    @pytest.mark.parametrize(
        ("aid", "bvid"),
        [
            (2, "BV1xx411c7mD"),
            (170001, "BV17x411w7KC"),
            # A current upload: 15 digits, past what the 32-bit scheme encoded.
            (117427861985029, "BV1VVpo6gEYg"),
        ],
    )
    def test_av_and_bv_are_one_number(self, aid: int, bvid: str) -> None:
        assert av_to_bv(aid) == bvid
        assert bv_to_av(bvid) == aid

    @pytest.mark.parametrize("value", ["BV1xx411c7mD", "av2", "AV2", "2", " av2 "])
    def test_a_caller_may_name_a_video_any_way_a_link_does(self, value: str) -> None:
        assert content_bvid(value) == "BV1xx411c7mD"

    @pytest.mark.parametrize(
        "value", ["bv1xx411c7mD", "BV1xx411c7m", "BV0xx411c7mD", "x", "", "av"]
    )
    def test_anything_else_is_refused_naming_the_field(self, value: str) -> None:
        with pytest.raises(InvalidParam) as caught:
            content_bvid(value, field="aweme_id")
        assert caught.value.details["field"] == "aweme_id"

    @pytest.mark.parametrize("value", ["0", "-1", "12a", ""])
    def test_numeric_ids_are_digits_and_not_zero(self, value: str) -> None:
        with pytest.raises(InvalidParam):
            numeric_id(value, field="author_id")

    def test_the_boundary_check_gives_back_the_bvid(self) -> None:
        """Every store keys a Bilibili post by its bvid; an av number left as
        typed would make a download nothing else can find."""
        assert require_content_id("av170001", platform=Platform.BILIBILI) == "BV17x411w7KC"


class TestParameters:
    def test_comments_are_asked_for_by_aid(self) -> None:
        built = params.comments_params(bvid="BV17x411w7KC")
        assert built["oid"] == "170001"
        assert json.loads(built["pagination_str"]) == {"offset": ""}

    @pytest.mark.parametrize("cursor", [None, "", "0"])
    def test_a_first_page_sends_an_empty_offset(self, cursor: str | None) -> None:
        built = params.comments_params(bvid="BV1xx411c7mD", cursor=cursor)
        assert json.loads(built["pagination_str"]) == {"offset": ""}

    def test_a_later_page_sends_the_offset_back_unchanged(self) -> None:
        offset = '{"type":1,"direction":1,"Data":{"cursor":3}}'
        built = params.comments_params(bvid="BV1xx411c7mD", cursor=offset)
        assert json.loads(built["pagination_str"]) == {"offset": offset}

    @pytest.mark.parametrize(("sort", "mode"), [(None, "3"), ("hot", "3"), ("new", "2")])
    def test_the_comment_order_picks_the_mode(self, sort: str | None, mode: str) -> None:
        assert params.comments_params(bvid="BV1xx411c7mD", sort=sort)["mode"] == mode

    def test_an_unknown_comment_order_is_refused_by_name(self) -> None:
        with pytest.raises(InvalidParam) as caught:
            params.comments_params(bvid="BV1xx411c7mD", sort="old")
        assert caught.value.details["field"] == "sort"

    @pytest.mark.parametrize(("cursor", "page"), [(None, "1"), ("0", "1"), ("3", "3")])
    def test_numbered_endpoints_take_the_page_as_the_cursor(
        self, cursor: str | None, page: str
    ) -> None:
        assert params.author_posts_params(mid="2", cursor=cursor)["pn"] == page

    def test_a_cursor_that_is_not_a_page_is_refused(self) -> None:
        with pytest.raises(InvalidParam):
            params.author_posts_params(mid="2", cursor="abc")

    def test_the_space_report_follows_the_identity_s_system(self) -> None:
        """A macOS User-Agent with a Direct3D renderer is a contradiction."""
        mac = params.dm_params(ClientProfile(os_name="Mac OS X"))
        windows = params.dm_params(ClientProfile(os_name="Windows"))
        decoded = base64.b64decode(mac["dm_cover_img_str"] + "==").decode()
        assert "Apple" in decoded
        assert mac["dm_cover_img_str"] != windows["dm_cover_img_str"]
        assert set(mac) == {"dm_img_list", "dm_img_str", "dm_cover_img_str", "dm_img_inter"}

    def test_the_stream_asked_for_is_one_progressive_mp4(self) -> None:
        built = params.content_streams_params(bvid="BV1xx411c7mD", cid="62131")
        assert (built["platform"], built["fnval"], built["high_quality"]) == ("html5", "1", "1")

    def test_every_builder_accepts_the_profile_the_fetch_layer_passes(self) -> None:
        adapter = get_adapter("bilibili")
        profile = adapter.profile_for(Fingerprint())
        for name, kwargs in {
            endpoints.CONTENT_DETAIL: {"bvid": "BV1xx411c7mD"},
            endpoints.CONTENT_STREAMS: {"bvid": "BV1xx411c7mD", "cid": "62131"},
            endpoints.AUTHOR_PROFILE: {"mid": "2"},
            endpoints.AUTHOR_POSTS: {"mid": "2"},
            endpoints.COMMENTS: {"bvid": "BV1xx411c7mD"},
            endpoints.COMMENT_REPLIES: {"bvid": "BV1xx411c7mD", "root": "1"},
            endpoints.AUTHOR_COLLECTIONS: {"mid": "2"},
            endpoints.COLLECTION_POSTS: {"media_id": "1"},
            endpoints.SESSION_CHECK: {},
        }.items():
            adapter.build_request(name, profile=profile, **kwargs)


class TestEndpointTable:
    def test_only_the_paths_the_page_signs_are_signed(self) -> None:
        signed = {name for name, spec in endpoints.ENDPOINTS.specs.items() if spec.signed}
        assert signed == {
            endpoints.CONTENT_DETAIL,
            endpoints.CONTENT_STREAMS,
            endpoints.AUTHOR_POSTS,
            endpoints.COMMENTS,
        }
        for name in signed:
            assert "/wbi/" in endpoints.ENDPOINTS[name].path, name

    def test_comments_prefer_a_login_and_page_only_with_one(self) -> None:
        spec = endpoints.ENDPOINTS[endpoints.COMMENTS]
        assert spec.login_for({}) is LoginNeed.PREFERRED
        assert spec.login_for({"cursor": "x"}) is LoginNeed.REQUIRED

    def test_a_guest_reads_the_first_page_of_replies_and_no_further(self) -> None:
        """Page two came back empty for a guest, looking like the thread's end."""
        spec = endpoints.ENDPOINTS[endpoints.COMMENT_REPLIES]
        assert spec.login_for({}) is LoginNeed.NONE
        assert spec.login_for({"cursor": "2"}) is LoginNeed.REQUIRED

    def test_the_space_list_is_sent_from_a_space_page(self) -> None:
        request = get_adapter("bilibili").build_request(endpoints.AUTHOR_POSTS, mid="2")
        assert request["headers"]["Referer"] == "https://space.bilibili.com/"
        assert request["headers"]["Origin"] == "https://space.bilibili.com"


class TestLinks:
    @pytest.mark.parametrize(
        ("url", "resource", "resource_id"),
        [
            ("https://www.bilibili.com/video/BV1xx411c7mD/", "video", "BV1xx411c7mD"),
            ("https://m.bilibili.com/video/BV1xx411c7mD", "video", "BV1xx411c7mD"),
            ("https://www.bilibili.com/video/av170001", "video", "BV17x411w7KC"),
            (
                "https://player.bilibili.com/player.html?bvid=BV1xx411c7mD&cid=1",
                "video",
                "BV1xx411c7mD",
            ),
            ("https://space.bilibili.com/2", "user", "2"),
            ("https://space.bilibili.com/2/video", "user", "2"),
            ("https://m.bilibili.com/space/2", "user", "2"),
            (
                "https://space.bilibili.com/2/favlist?fid=58074805&ftype=create",
                "collection",
                "58074805",
            ),
            ("https://www.bilibili.com/list/ml58074805", "collection", "58074805"),
            ("https://b23.tv/AbC123x", "short_link", "AbC123x"),
            ("bilibili.com/video/BV1xx411c7mD", "video", "BV1xx411c7mD"),
        ],
    )
    def test_the_links_people_paste(self, url: str, resource: str, resource_id: str) -> None:
        kind = identify(url)
        assert kind.platform is Platform.BILIBILI
        assert (kind.resource.value, kind.resource_id) == (resource, resource_id)

    def test_a_short_link_is_followed_before_anything_is_fetched(self) -> None:
        assert identify("https://b23.tv/AbC123x").needs_expansion is True

    def test_the_international_site_is_not_this_one(self) -> None:
        """bilibili.tv is a different service with a different API."""
        assert identify("https://www.bilibili.tv/en/video/4789").platform is None

    def test_who_shared_it_is_dropped_and_the_part_is_kept(self) -> None:
        cleaned = _clean_query("p=2&vd_source=abc&spm_id_from=333&t=30&buvid=XY")
        assert (
            "vd_source" not in cleaned and "spm_id_from" not in cleaned and "buvid" not in cleaned
        )
        assert "p=2" in cleaned and "t=30" in cleaned


class TestTheRestOfTheSystem:
    def test_an_imported_jar_needs_a_visitor_id_and_a_session_cookie_means_a_login(self) -> None:
        assert REQUIRED[Platform.BILIBILI] == ("buvid3",)
        assert "SESSDATA" in SESSION_MARKERS

    def test_it_has_a_smoke_link_and_a_session_check(self) -> None:
        assert identify(SMOKE_URLS[Platform.BILIBILI]).resource_id == "BV1xx411c7mD"
        assert SESSION_CHECKS[Platform.BILIBILI] == endpoints.SESSION_CHECK

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            # A freshly minted guest, measured 2026-10-18.
            ({"code": -101, "data": {"isLogin": False}}, (False, None, "signed_out", None)),
            ({"code": 0, "data": {"isLogin": True, "mid": 2}}, (True, "2", "live", None)),
            (
                {"code": -412, "message": "intercepted", "data": {}},
                (False, None, "refused", "intercepted"),
            ),
            ("not json", (False, None, "refused", None)),
        ],
    )
    def test_nav_is_a_real_session_check(self, payload: object, expected: tuple) -> None:
        assert read_session(Platform.BILIBILI, payload) == expected

    def test_media_comes_from_bilibili_s_cdns_and_not_from_akamai_at_large(self) -> None:
        """Its overseas Akamai host is admitted by name; the rest of Akamai,
        shared by every Akamai customer, is not."""
        allowed, refused = allowed_mirrors(
            [
                "https://upos-sz-mirrorcosov.bilivideo.com/a.mp4",
                "https://i0.hdslb.com/bfs/archive/x.jpg",
                "https://upos-hz-mirrorakam.akamaized.net/a.mp4",
                "https://someone-else.akamaized.net/a.mp4",
            ],
            Platform.BILIBILI,
        )
        assert len(allowed) == 3
        assert len(refused) == 1 and "someone-else.akamaized.net" in refused[0]


def classified(payload: dict, *, status: int = 200) -> object:
    body = json.dumps(payload).encode()
    return Classifier().classify(
        RawResponse(status=status, headers={"content-type": "application/json"}, body=body)
    )


class TestAbsence:
    """What Bilibili answers when the thing asked for is not there, measured
    2026-10-18 - and which of those answers are refusals instead."""

    @pytest.mark.parametrize(
        "payload",
        [
            # A video, an author, comments, a playurl part: all of them.
            {"code": -404, "message": "nothing here", "ttl": 1},
            {"code": 12006, "message": "no such comment", "ttl": 1, "data": None},
            {"code": 62002, "message": "not visible", "ttl": 1},
        ],
    )
    def test_nothing_there_is_an_answer(self, payload: dict) -> None:
        assert classified(payload).outcome is Outcome.BUSINESS_ERROR

    @pytest.mark.parametrize("code", [-352, -412, -509, -799])
    def test_a_risk_code_is_a_refusal(self, code: int) -> None:
        verdict = classified({"code": code, "message": "x", "ttl": 1, "data": {"v_voucher": "v"}})
        assert verdict.outcome is Outcome.RISK_CONTROL

    def test_the_412_page_is_a_refusal(self) -> None:
        verdict = Classifier().classify(
            RawResponse(status=412, headers={"content-type": "text/html"}, body=b"<!DOCTYPE html>")
        )
        assert verdict.outcome is Outcome.RISK_CONTROL

    def test_a_folder_list_of_null_is_an_ordinary_answer(self) -> None:
        """An author with no folders: code 0, data null, measured."""
        assert classified({"code": 0, "message": "0", "ttl": 1, "data": None}).outcome is Outcome.OK

    def test_a_guest_session_check_is_not_a_refusal(self) -> None:
        """-101 is nav saying "not logged in"; the probe has to get to read it."""
        verdict = classified({"code": -101, "message": "not logged in", "data": {"isLogin": False}})
        assert verdict.outcome is Outcome.BUSINESS_ERROR


class TestAuthorIds:
    """A Bilibili author is a number. Read as an @handle - the rule on the
    platforms whose ids start with MS4wLjAB - it was refused before it was sent."""

    def test_rest_sends_the_mid_as_the_author(self) -> None:
        from dtk.worker.registry import definition_for

        built = definition_for("bilibili.author_posts").platform_params({"sec_user_id": "2"})
        assert built["mid"] == "2"

    def test_mcp_sends_it_too(self) -> None:
        from dtk.mcp.routing import build_task
        from dtk.worker.registry import AUTHOR_ID, Capability

        endpoint, task = build_task(Platform.BILIBILI, Capability.AUTHOR_POSTS, "2")
        assert (endpoint, task[AUTHOR_ID]) == ("bilibili.author_posts", "2")

    def test_douyin_still_tells_a_handle_from_an_id(self) -> None:
        from dtk.worker.registry import definition_for

        with pytest.raises(InvalidParam):
            definition_for("douyin.author_posts").platform_params({"sec_user_id": "someone"})
