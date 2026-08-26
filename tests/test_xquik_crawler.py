import unittest

import httpx

from crawlers.xquik.xquik_crawler import XquikCrawler, XquikError

TWEET_ID = "1893456789012345678"
TWEET_PAYLOAD = {
    "tweet": {
        "id": TWEET_ID,
        "text": "Release notes with a demo.",
        "createdAt": "2026-02-24T14:30:00.000Z",
        "retweetCount": 12,
        "replyCount": 3,
        "likeCount": 40,
        "quoteCount": 2,
        "viewCount": 5000,
        "bookmarkCount": 7,
        "entities": {"hashtags": [{"text": "release"}]},
        "media": [
            {
                "mediaUrl": "https://pbs.twimg.com/media/example.jpg",
                "type": "photo",
                "url": "https://t.co/example",
            }
        ],
    },
    "author": {
        "id": "987654321",
        "username": "example",
        "verified": False,
    },
}


class XquikCrawlerTest(unittest.IsolatedAsyncioTestCase):
    def test_extracts_supported_post_inputs(self):
        inputs = (
            TWEET_ID,
            f"https://x.com/example/status/{TWEET_ID}",
            f"https://www.x.com/example/status/{TWEET_ID}?s=20",
            f"https://twitter.com/example/status/{TWEET_ID}/photo/1",
            f"https://mobile.twitter.com/example/status/{TWEET_ID}",
            f"https://x.com/i/web/status/{TWEET_ID}",
        )

        for tweet_input in inputs:
            with self.subTest(tweet_input=tweet_input):
                self.assertEqual(
                    XquikCrawler.extract_tweet_id(tweet_input),
                    TWEET_ID,
                )

    def test_rejects_untrusted_hosts_and_invalid_ids(self):
        inputs = (
            "1234",
            f"https://example.com/user/status/{TWEET_ID}",
            f"https://x.com.example.com/user/status/{TWEET_ID}",
            "https://x.com/user/status/not-a-number",
        )

        for tweet_input in inputs:
            with (
                self.subTest(tweet_input=tweet_input),
                self.assertRaisesRegex(
                    XquikError,
                    "numeric post ID",
                ),
            ):
                XquikCrawler.extract_tweet_id(tweet_input)

    def test_supports_only_valid_post_inputs(self):
        self.assertTrue(
            XquikCrawler.supports(f"https://x.com/example/status/{TWEET_ID}")
        )
        self.assertFalse(XquikCrawler.supports("https://x.com/example"))

    def test_requires_api_key(self):
        crawler = XquikCrawler(api_key="")

        with self.assertRaisesRegex(XquikError, "Set XQUIK_API_KEY") as context:
            crawler.get_api_key()

        self.assertEqual(context.exception.status_code, 503)

    def test_caller_key_overrides_configured_key(self):
        crawler = XquikCrawler(api_key="server-key")

        self.assertEqual(crawler.get_api_key("caller-key"), "caller-key")

    async def test_fetches_post_from_fixed_xquik_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                f"https://xquik.com/api/v1/x/tweets/{TWEET_ID}",
            )
            self.assertEqual(request.headers["x-api-key"], "test-key")
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, json=TWEET_PAYLOAD)

        crawler = XquikCrawler(
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual(await crawler.fetch_tweet(TWEET_ID), TWEET_PAYLOAD)

    async def test_maps_expected_upstream_failures(self):
        cases = (
            (401, 503, "server subscription"),
            (402, 503, "server subscription"),
            (404, 404, "not found"),
            (429, 429, "rate limited"),
            (500, 502, "lookup failed"),
        )

        for upstream_status, expected_status, message in cases:
            with self.subTest(upstream_status=upstream_status):
                transport = httpx.MockTransport(
                    lambda request, status=upstream_status: httpx.Response(
                        status,
                        json={"error": "sensitive-upstream-detail"},
                    )
                )
                crawler = XquikCrawler(api_key="test-key", transport=transport)

                with self.assertRaisesRegex(XquikError, message) as context:
                    await crawler.fetch_tweet(TWEET_ID)

                self.assertEqual(context.exception.status_code, expected_status)
                self.assertNotIn("sensitive-upstream-detail", str(context.exception))

    async def test_rejects_invalid_success_payloads(self):
        payloads = (
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, json={"tweet": None}),
        )

        for response in payloads:
            with self.subTest(content=response.content):
                crawler = XquikCrawler(
                    api_key="test-key",
                    transport=httpx.MockTransport(
                        lambda request, current=response: current
                    ),
                )

                with self.assertRaisesRegex(XquikError, "invalid data") as context:
                    await crawler.fetch_tweet(TWEET_ID)

                self.assertEqual(context.exception.status_code, 502)

    async def test_maps_timeout_without_exposing_request_details(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("secret transport detail", request=request)

        crawler = XquikCrawler(
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaisesRegex(XquikError, "timed out") as context:
            await crawler.fetch_tweet(TWEET_ID)

        self.assertEqual(context.exception.status_code, 504)
        self.assertNotIn("secret transport detail", str(context.exception))

    def test_normalizes_post_for_hybrid_response(self):
        result = XquikCrawler.to_minimal(TWEET_PAYLOAD)

        self.assertEqual(result["type"], "image")
        self.assertEqual(result["platform"], "x")
        self.assertEqual(result["post_id"], TWEET_ID)
        self.assertEqual(result["author"]["username"], "example")
        self.assertEqual(result["statistics"]["like_count"], 40)
        self.assertEqual(
            result["cover_data"]["cover"],
            "https://pbs.twimg.com/media/example.jpg",
        )
        self.assertEqual(result["media_data"], TWEET_PAYLOAD["tweet"]["media"])

    def test_normalizes_text_only_post_with_safe_defaults(self):
        result = XquikCrawler.to_minimal(
            {
                "tweet": {
                    "id": TWEET_ID,
                    "text": "Text only",
                    "author": {"username": "fallback"},
                }
            }
        )

        self.assertEqual(result["type"], "post")
        self.assertEqual(result["author"]["username"], "fallback")
        self.assertEqual(result["media_data"], [])
        self.assertEqual(result["hashtags"], [])
        self.assertIsNone(result["cover_data"]["cover"])


if __name__ == "__main__":
    unittest.main()
