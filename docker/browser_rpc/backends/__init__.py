"""Backend registry.

Selection is by exact name and there is no fallback between backends. If the
configured one cannot start, the service reports itself unhealthy and the pool
degrades to manually imported cookies - which is a state the operator can see.
Quietly switching to the fake backend would instead fill the pool with
identities no platform accepts, and everything downstream would look fine.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from browser_rpc.backends import cloak, fake
from browser_rpc.backends.base import (
    BackendInfo,
    BrowserBackend,
    MintedProfile,
    MintPlan,
    SigningContext,
    SignPlan,
)
from browser_rpc.errors import ConfigError
from browser_rpc.settings import Settings

BACKENDS: Mapping[str, Callable[[Settings], BrowserBackend]] = {
    cloak.BACKEND_NAME: cloak.build,
    fake.BACKEND_NAME: fake.build,
}


def build_backend(settings: Settings) -> BrowserBackend:
    """Instantiate the configured backend, or refuse to start."""
    factory = BACKENDS.get(settings.backend)
    if factory is None:
        known = ", ".join(sorted(BACKENDS))
        raise ConfigError(
            f"unknown browser backend {settings.backend!r}; DTK_BROWSER_BACKEND must be one of: {known}"
        )
    return factory(settings)


__all__ = [
    "BACKENDS",
    "BackendInfo",
    "BrowserBackend",
    "MintPlan",
    "MintedProfile",
    "SignPlan",
    "SigningContext",
    "build_backend",
]
