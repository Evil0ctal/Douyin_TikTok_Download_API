"""Client for the optional, internal FunASR ONNX sidecar."""

from __future__ import annotations

from typing import Any

import httpx


class TranscriberUnavailable(RuntimeError):
    pass


class TranscriberBusy(TranscriberUnavailable):
    pass


class TranscriberClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout_seconds: float = 3600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self.timeout_seconds = max(1.0, timeout_seconds)
        self._client = client
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    async def _call(self, method: str, path: str, *, payload: Any = None) -> dict[str, Any]:
        if not self.configured:
            raise TranscriberUnavailable("the transcriber is not configured")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        try:
            response = await self._client.request(
                method,
                f"{self._base}{path}",
                json=payload,
                headers={"X-Transcriber-Token": self._token} if self._token else {},
            )
        except httpx.HTTPError as exc:
            raise TranscriberUnavailable(f"transcriber {path} failed: {exc}") from exc
        if response.status_code == 429:
            raise TranscriberBusy("the transcriber queue is full")
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = str(body.get("error") or body.get("detail") or "")
            except (ValueError, AttributeError):
                detail = ""
            raise TranscriberUnavailable(
                f"transcriber {path} refused the request: {detail or response.status_code}"
            )
        try:
            body = response.json()
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    async def health(self) -> dict[str, Any]:
        return await self._call("GET", "/health")

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/jobs", payload=payload)

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/jobs/{job_id}")

    async def cancel(self, job_id: str) -> bool:
        """Cancel a live job or acknowledge and discard a terminal one."""
        try:
            await self._call("DELETE", f"/jobs/{job_id}")
        except TranscriberUnavailable:
            return False
        return True


__all__ = [
    "TranscriberBusy",
    "TranscriberClient",
    "TranscriberUnavailable",
]
