"""Client for the Go downloader sidecar.

Shaped after :mod:`dtk.identity.minting.client`, for the same reason: an
optional container is a degraded mode, not an error. A deployment that never
starts the ``downloader`` profile has no media downloads and everything else
works exactly as before, so an unreachable service raises one specific
exception that callers turn into a clear message rather than a 500.

Nothing sensitive crosses this boundary. The sidecar receives mirrors, a
per-platform host allowlist and a byte ceiling; it never receives a cookie, the
master key, a database address or a caller's ``?proxy=``. That is a property of
what this module sends, so it is worth checking here rather than trusting the
other side to ignore what it is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import httpx

from dtk.core.logging import get_logger

log = get_logger(__name__)

SUBMIT_TIMEOUT_SECONDS: Final = 15.0
POLL_TIMEOUT_SECONDS: Final = 10.0
#: Listing or deleting walks a volume that may hold thousands of files.
FILES_TIMEOUT_SECONDS: Final = 60.0


class DownloaderUnavailable(RuntimeError):
    """The downloader is not configured, not running, or refused the job.

    Never fatal to the instance. Media storage is an opt-in container behind a
    compose profile; without it the archive still records everything and only
    the byte-fetching half is missing.
    """


class DownloaderBusy(DownloaderUnavailable):
    """The sidecar's queue is full. The caller should retry later, not harder."""


@dataclass(frozen=True, slots=True)
class DownloaderHealth:
    available: bool
    version: str = ""
    workers: int = 0
    queued: int = 0
    running: int = 0
    total_bytes: int = 0
    root: str = ""
    detail: str = ""


class DownloaderClient:
    """Talks to one downloader container."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=SUBMIT_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    def _headers(self) -> dict[str, str]:
        return {"X-Downloader-Token": self._token} if self._token else {}

    async def _call(
        self, method: str, path: str, *, json: Any = None, timeout: float
    ) -> dict[str, Any]:
        if not self.configured:
            raise DownloaderUnavailable("the media downloader is not configured")
        client = await self._http()
        try:
            response = await client.request(
                method,
                f"{self._base}{path}",
                json=json,
                timeout=timeout,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise DownloaderUnavailable(f"downloader {path} failed: {exc}") from exc
        if response.status_code == 429:
            raise DownloaderBusy("the downloader queue is full")
        if response.status_code >= 400:
            detail = _error_of(response)
            raise DownloaderUnavailable(f"downloader {path} refused the request: {detail}")
        return _body_of(response)

    async def health(self) -> DownloaderHealth:
        """Ask whether the sidecar is there, without raising when it is not."""
        if not self.configured:
            return DownloaderHealth(available=False, detail="not configured")
        try:
            body = await self._call("GET", "/health", timeout=POLL_TIMEOUT_SECONDS)
        except DownloaderUnavailable as exc:
            return DownloaderHealth(available=False, detail=str(exc)[:200])
        return DownloaderHealth(
            available=body.get("status") == "ok",
            version=str(body.get("version") or ""),
            workers=int(body.get("workers") or 0),
            queued=int(body.get("queued") or 0),
            running=int(body.get("running") or 0),
            total_bytes=int(body.get("total_bytes") or 0),
            root=str(body.get("root") or ""),
            detail=str(body.get("detail") or ""),
        )

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Hand over one job. Idempotent on ``id``, so a retry is safe."""
        return await self._call("POST", "/jobs", json=payload, timeout=SUBMIT_TIMEOUT_SECONDS)

    async def job(self, job_id: str) -> dict[str, Any] | None:
        """One job's state, or ``None`` once the sidecar has forgotten it.

        Forgetting is expected: the sidecar keeps a bounded history and loses
        everything on restart, because the durable record lives in Postgres.
        A caller that reads ``None`` should trust its own row.
        """
        try:
            return await self._call("GET", f"/jobs/{job_id}", timeout=POLL_TIMEOUT_SECONDS)
        except DownloaderUnavailable as exc:
            if "refused the request" in str(exc):
                return None
            raise

    async def cancel(self, job_id: str) -> bool:
        try:
            await self._call("DELETE", f"/jobs/{job_id}", timeout=POLL_TIMEOUT_SECONDS)
        except DownloaderUnavailable:
            return False
        return True

    async def files(self) -> dict[str, Any]:
        """What is on the media volume, per content directory."""
        return await self._call("GET", "/files", timeout=FILES_TIMEOUT_SECONDS)

    async def delete(self, paths: list[str]) -> dict[str, Any]:
        """Remove the named content directories. Policy is decided by the caller."""
        if not paths:
            return {"freed_bytes": 0, "removed": []}
        return await self._call(
            "POST", "/files/delete", json={"paths": paths}, timeout=FILES_TIMEOUT_SECONDS
        )


def _body_of(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _error_of(response: httpx.Response) -> str:
    """The sidecar's own message, trimmed.

    Worth surfacing rather than replacing: "host p42.example.com is not an
    allowed media domain" is the single most useful sentence this system can
    print when a download fails, and a generic "downloader error" throws it away.
    """
    detail = str(_body_of(response).get("error") or "").strip()
    return detail[:300] if detail else f"HTTP {response.status_code}"


__all__ = [
    "DownloaderBusy",
    "DownloaderClient",
    "DownloaderHealth",
    "DownloaderUnavailable",
]
