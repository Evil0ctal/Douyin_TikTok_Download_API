"""iOS Shortcut release information.

One of the project's signature features and one with existing users, so v5
keeps the endpoint (doc 06). What changes is the shape: V4 returned both
languages at once in ``link`` / ``link_en`` / ``note`` / ``note_en``, which made
every caller read the half it could not use and left no room for a third
language. Here the response carries one language, chosen by ``?lang=`` or
``Accept-Language``.

The release text lives in ``data/ios_shortcut.json`` beside this module rather
than in the source, because Python source in this project is ASCII-only
(doc 14) and the notes are user-facing prose in two languages.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from dtk.api.routes.openapi import I18N_KEY
from dtk.api.routes.support import language, ok
from dtk.core.logging import get_logger
from dtk.core.types import DEFAULT_LANGUAGE, Language

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/ios", tags=["ios"])

RELEASE_FILE = Path(__file__).parent / "data" / "ios_shortcut.json"


@lru_cache(maxsize=1)
def _release() -> dict[str, Any]:
    """Read the release descriptor once per process."""
    try:
        return json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.error("ios.release_unreadable", error=str(exc)[:200])
        return {}


def _localized(field: Any, lang: Language) -> Any:
    """Pick one language out of a ``{"en": ..., "zh": ...}`` block.

    Falls back to English rather than to the key or to an empty value: a
    missing translation should read as one untranslated string, not as a broken
    page (doc 14).
    """
    if not isinstance(field, dict):
        return field
    if lang.value in field:
        return field[lang.value]
    return field.get(DEFAULT_LANGUAGE.value)


@router.get(
    "/shortcut",
    summary="iOS Shortcut release information",
    openapi_extra={I18N_KEY: "ios_shortcut"},
)
async def shortcut(request: Request) -> Any:
    """Version, link and notes in the requested language.

    Unauthenticated: the Shortcut asks for this before it has anywhere to put
    an API key, and the answer is public release metadata.
    """
    release = _release()
    lang = language(request)
    return ok(
        request,
        {
            "version": release.get("version"),
            "updated_at": release.get("updated_at"),
            "link": _localized(release.get("link"), lang),
            "notes": _localized(release.get("notes"), lang) or [],
            "language": lang.value,
        },
    )


__all__ = ["RELEASE_FILE", "router"]
