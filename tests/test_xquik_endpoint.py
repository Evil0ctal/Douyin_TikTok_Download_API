import unittest

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app.api.endpoints import xquik as xquik_endpoint
from app.main import app
from crawlers.xquik.xquik_crawler import XquikCrawler, XquikError

TWEET_ID = "1893456789012345678"
TWEET_PAYLOAD = {"tweet": {"id": TWEET_ID, "text": "Example"}}


def make_request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 80),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
            "path": f"/api/x/tweets/{TWEET_ID}",
            "raw_path": f"/api/x/tweets/{TWEET_ID}".encode(),
            "query_string": b"",
            "headers": [],
        }
    )


class SuccessfulCrawler:
    async def fetch_tweet(self, tweet_id, api_key=None):
        if api_key != "caller-key":
            raise AssertionError("The endpoint must forward the caller's key.")
        return TWEET_PAYLOAD


class FailingCrawler:
    async def fetch_tweet(self, tweet_id, api_key=None):
        raise XquikError(429, "X post lookup is rate limited. Try again later.")


class XquikEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_asgi_route_forwards_header_and_wraps_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-api-key"], "caller-key")
            return httpx.Response(200, json=TWEET_PAYLOAD)

        original = xquik_endpoint.xquik_crawler
        xquik_endpoint.xquik_crawler = XquikCrawler(
            transport=httpx.MockTransport(handler)
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                response = await client.get(
                    f"/api/x/tweets/{TWEET_ID}",
                    headers={"x-api-key": "caller-key"},
                )
        finally:
            xquik_endpoint.xquik_crawler = original

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], TWEET_PAYLOAD)

    async def test_asgi_route_requires_caller_key(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(f"/api/x/tweets/{TWEET_ID}")

        self.assertEqual(response.status_code, 422)

    async def test_returns_repository_response_model(self):
        original = xquik_endpoint.xquik_crawler
        xquik_endpoint.xquik_crawler = SuccessfulCrawler()
        try:
            response = await xquik_endpoint.get_x_post(
                make_request(),
                TWEET_ID,
                "caller-key",
            )
        finally:
            xquik_endpoint.xquik_crawler = original

        self.assertEqual(response.code, 200)
        self.assertEqual(response.router, f"/api/x/tweets/{TWEET_ID}")
        self.assertEqual(response.data, TWEET_PAYLOAD)

    async def test_returns_safe_http_error(self):
        original = xquik_endpoint.xquik_crawler
        xquik_endpoint.xquik_crawler = FailingCrawler()
        try:
            with self.assertRaises(HTTPException) as context:
                await xquik_endpoint.get_x_post(
                    make_request(),
                    TWEET_ID,
                    "caller-key",
                )
        finally:
            xquik_endpoint.xquik_crawler = original

        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(
            context.exception.detail,
            {
                "code": 429,
                "router": f"/api/x/tweets/{TWEET_ID}",
                "message": "X post lookup is rate limited. Try again later.",
            },
        )


if __name__ == "__main__":
    unittest.main()
