"""Platform packages: endpoint tables, parameter builders and response parsers.

Each platform lives in its own subpackage and exposes ``adapter.ADAPTER``.
Nothing here performs IO; see :mod:`dtk.platforms.base` for the contract and
``docs/design/01-architecture.md`` for why the boundary sits where it does.
"""

from dtk.platforms.base import (
    ClientProfile,
    EndpointSpec,
    EndpointTable,
    PlatformAdapter,
    RequestSpec,
)
from dtk.platforms.registry import available_platforms, get_adapter

__all__ = [
    "ClientProfile",
    "EndpointSpec",
    "EndpointTable",
    "PlatformAdapter",
    "RequestSpec",
    "available_platforms",
    "get_adapter",
]
