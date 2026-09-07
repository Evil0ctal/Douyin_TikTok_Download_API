"""Structured logging with mandatory redaction.

Log messages are English only and event names are dotted identifiers, never
sentences: that keeps grep stable, keeps aggregation by event type possible, and
sidesteps the question of translating logs entirely. See docs/design/14-i18n.md.

Redaction happens in the processor chain rather than at call sites, because a
call site that forgets is exactly how V4 leaked a live session cookie.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

#: Keys whose values are replaced wholesale.
SENSITIVE_KEYS = frozenset(
    {
        "cookie",
        "cookies",
        "set-cookie",
        "set_cookie",
        "authorization",
        "x-api-key",
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "setup_token",
        "proxy_url",
        "dtk_secret_key",
    }
)

#: Signature and session parameters truncated inside URLs and free text.
_PARAM_RE = re.compile(
    r"\b(msToken|a_bogus|X-Bogus|_signature|sessionid|sid_guard|odin_tt|uid_tt|ttwid)"
    r"=([^&\s;\"']{6,})",
    re.IGNORECASE,
)
_MASK = "[REDACTED]"


def _truncate_params(text: str) -> str:
    return _PARAM_RE.sub(lambda m: f"{m.group(1)}={m.group(2)[:6]}...", text)


def redact(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    for key in list(event.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event[key] = _MASK
        elif isinstance(event[key], str):
            event[key] = _truncate_params(event[key])
    return event


def configure(level: str = "info", json_output: bool = True) -> None:
    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), 20)
    )
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), 20)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


__all__ = ["SENSITIVE_KEYS", "configure", "get_logger", "redact"]
