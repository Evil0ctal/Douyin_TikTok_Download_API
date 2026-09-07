"""Adapter discovery.

The architecture's acceptance test is that adding a platform requires only a new
``src/dtk/platforms/<name>/`` directory. A hand-maintained ``{"douyin": ...}``
mapping would quietly break that, so packages are discovered instead: any
subpackage of :mod:`dtk.platforms` exposing ``adapter.ADAPTER`` is a platform.

Discovery is import-time only - no IO beyond importing Python modules - and the
result is cached, so the scan happens once per process.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache

import dtk.platforms as platforms_package
from dtk.core.errors import InvalidParam
from dtk.platforms.base import PlatformAdapter

_ADAPTER_ATTR = "ADAPTER"
_ADAPTER_MODULE = "adapter"


@cache
def _discover() -> dict[str, PlatformAdapter]:
    found: dict[str, PlatformAdapter] = {}
    for module in pkgutil.iter_modules(platforms_package.__path__):
        if not module.ispkg or module.name.startswith("_"):
            continue
        name = f"{platforms_package.__name__}.{module.name}.{_ADAPTER_MODULE}"
        try:
            imported = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                # A subpackage with no adapter module is a helper package, not
                # a platform. Anything else - a dependency the adapter imports
                # and cannot find - is a real failure and must not be hidden.
                continue
            raise
        # Deliberately strict: a package that ships an adapter module without
        # ADAPTER is a half-written platform, and a platform that silently
        # fails to register is worse than an import error at startup.
        adapter: PlatformAdapter = getattr(imported, _ADAPTER_ATTR)
        found[str(adapter.platform)] = adapter
    return found


def available_platforms() -> tuple[str, ...]:
    """Every platform that has an adapter, sorted for stable output."""
    return tuple(sorted(_discover()))


def get_adapter(platform: str) -> PlatformAdapter:
    """Return the adapter for ``platform``.

    Accepts the :class:`~dtk.core.types.Platform` enum too, since it is a
    ``StrEnum``.
    """
    key = str(platform)
    try:
        return _discover()[key]
    except KeyError:
        raise InvalidParam(
            f"unsupported platform: {key}",
            details={"platform": key, "supported": list(available_platforms())},
        ) from None


__all__ = ["available_platforms", "get_adapter"]
