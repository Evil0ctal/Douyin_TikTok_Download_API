import os
import re
from typing import Any

import httpx

XQUIK_TWEET_URL = "https://xquik.com/api/v1/x/tweets/{tweet_id}"
TWEET_ID_PATTERN = re.compile(r"^[0-9]{15,20}$")
TWEET_URL_PATTERN = re.compile(
    r"^https?://(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)/"
    r"(?:i/web|[^/?#]+)/status/([0-9]{15,20})(?:[/?#].*)?$",
    re.IGNORECASE,
)


class XquikError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class XquikCrawler:
    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.transport = transport

    @staticmethod
    def extract_tweet_id(tweet_input: str) -> str:
        value = tweet_input.strip()
        if TWEET_ID_PATTERN.fullmatch(value):
            return value

        match = TWEET_URL_PATTERN.fullmatch(value)
        if match:
            return match.group(1)

        raise XquikError(400, "Use a numeric post ID or an x.com status URL.")

    @staticmethod
    def supports(tweet_input: str) -> bool:
        try:
            XquikCrawler.extract_tweet_id(tweet_input)
            return True
        except XquikError:
            return False

    def get_api_key(self, api_key: str | None = None) -> str:
        configured_key = (
            api_key
            if api_key is not None
            else (
                self.api_key
                if self.api_key is not None
                else os.getenv("XQUIK_API_KEY", "")
            )
        )
        api_key = configured_key.strip()
        if not api_key:
            raise XquikError(
                503,
                "X post lookup is not configured. Set XQUIK_API_KEY.",
            )
        return api_key

    async def fetch_tweet(
        self,
        tweet_input: str,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        tweet_id = self.extract_tweet_id(tweet_input)
        headers = {
            "accept": "application/json",
            "user-agent": "Douyin_TikTok_Download_API/Xquik",
            "x-api-key": self.get_api_key(api_key),
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                transport=self.transport,
            ) as client:
                response = await client.get(
                    XQUIK_TWEET_URL.format(tweet_id=tweet_id),
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise XquikError(504, "X post lookup timed out. Try again.") from exc
        except httpx.RequestError as exc:
            raise XquikError(502, "X post lookup failed. Try again.") from exc

        if response.status_code == 404:
            raise XquikError(404, "X post not found.")
        if response.status_code == 429:
            raise XquikError(429, "X post lookup is rate limited. Try again later.")
        if response.status_code in (401, 402):
            raise XquikError(
                503,
                "X post lookup is unavailable. Check the server subscription.",
            )
        if response.status_code >= 400:
            raise XquikError(502, "X post lookup failed. Try again.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise XquikError(502, "X post lookup returned invalid data.") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("tweet"), dict):
            raise XquikError(502, "X post lookup returned invalid data.")

        return payload

    @staticmethod
    def to_minimal(payload: dict[str, Any]) -> dict[str, Any]:
        tweet = payload["tweet"]
        media = tweet.get("media")
        if not isinstance(media, list):
            media = []
        media = [item for item in media if isinstance(item, dict)]

        media_types = {item.get("type") for item in media}
        if media_types.intersection({"video", "animated_gif"}):
            post_type = "video"
        elif "photo" in media_types:
            post_type = "image"
        else:
            post_type = "post"

        author = payload.get("author")
        if not isinstance(author, dict):
            author = tweet.get("author")
        if not isinstance(author, dict):
            author = {}

        entities = tweet.get("entities")
        if not isinstance(entities, dict):
            entities = {}
        hashtags = entities.get("hashtags")
        if not isinstance(hashtags, list):
            hashtags = []

        first_media_url = next(
            (
                item.get("mediaUrl")
                for item in media
                if isinstance(item.get("mediaUrl"), str)
            ),
            None,
        )

        return {
            "type": post_type,
            "platform": "x",
            "post_id": tweet.get("id"),
            "desc": tweet.get("text"),
            "create_time": tweet.get("createdAt"),
            "author": author,
            "music": None,
            "statistics": {
                "retweet_count": tweet.get("retweetCount", 0),
                "reply_count": tweet.get("replyCount", 0),
                "like_count": tweet.get("likeCount", 0),
                "quote_count": tweet.get("quoteCount", 0),
                "view_count": tweet.get("viewCount", 0),
                "bookmark_count": tweet.get("bookmarkCount", 0),
            },
            "cover_data": {
                "cover": first_media_url,
                "origin_cover": first_media_url,
                "dynamic_cover": None,
            },
            "hashtags": hashtags,
            "media_data": media,
        }
