import unittest

from crawlers.hybrid.hybrid_crawler import HybridCrawler

TWEET_ID = "1893456789012345678"
TWEET_URL = f"https://x.com/example/status/{TWEET_ID}"
TWEET_PAYLOAD = {"tweet": {"id": TWEET_ID, "text": "Example"}}


class FakeXquikCrawler:
    def __init__(self):
        self.inputs = []

    def supports(self, tweet_input):
        return tweet_input == TWEET_URL

    async def fetch_tweet(self, tweet_input, api_key=None):
        self.inputs.append((tweet_input, api_key))
        return TWEET_PAYLOAD

    def to_minimal(self, payload):
        return {"platform": "x", "post_id": payload["tweet"]["id"]}


class HybridXquikTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.xquik = FakeXquikCrawler()
        self.crawler = HybridCrawler.__new__(HybridCrawler)
        self.crawler.XquikCrawler = self.xquik

    async def test_returns_full_xquik_payload(self):
        result = await self.crawler.hybrid_parsing_single_video(TWEET_URL)

        self.assertEqual(result, TWEET_PAYLOAD)
        self.assertEqual(self.xquik.inputs, [(TWEET_URL, None)])

    async def test_returns_normalized_xquik_payload(self):
        result = await self.crawler.hybrid_parsing_single_video(
            TWEET_URL,
            minimal=True,
            xquik_api_key="caller-key",
        )

        self.assertEqual(result, {"platform": "x", "post_id": TWEET_ID})
        self.assertEqual(self.xquik.inputs, [(TWEET_URL, "caller-key")])


if __name__ == "__main__":
    unittest.main()
