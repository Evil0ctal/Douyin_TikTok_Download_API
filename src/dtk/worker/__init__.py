"""The worker process: the task loop and the background loops around it.

``main`` runs queued tasks; ``pool_filler``, ``proxy_prober`` and
``maintenance`` keep the identity pool, the egresses and the stored data in
shape while it does. ``registry`` is the shared endpoint table - the one
definition of what ``douyin.content_detail`` means, used by the worker, the REST
layer, MCP and the CLI alike.

See docs/design/01-architecture.md.
"""

from dtk.worker.loop import PeriodicLoop
from dtk.worker.main import (
    DatabaseTaskStore,
    TaskRun,
    TaskStore,
    TaskWorker,
    WorkerOptions,
    serialize_error,
)
from dtk.worker.maintenance import Maintenance, MaintenanceConfig, MaintenanceReport
from dtk.worker.pool_filler import FillerConfig, FillResult, PoolFiller
from dtk.worker.proxy_prober import (
    ProbeClient,
    ProberConfig,
    ProbeResult,
    ProxyProber,
    SweepReport,
)
from dtk.worker.registry import (
    ENDPOINTS,
    Capability,
    EndpointDefinition,
    ResolvedCall,
    definition_for,
    resolve,
)
from dtk.worker.runtime import WorkerRuntime, build_runtime, run

__all__ = [
    "ENDPOINTS",
    "Capability",
    "DatabaseTaskStore",
    "EndpointDefinition",
    "FillResult",
    "FillerConfig",
    "Maintenance",
    "MaintenanceConfig",
    "MaintenanceReport",
    "PeriodicLoop",
    "PoolFiller",
    "ProbeClient",
    "ProbeResult",
    "ProberConfig",
    "ProxyProber",
    "ResolvedCall",
    "SweepReport",
    "TaskRun",
    "TaskStore",
    "TaskWorker",
    "WorkerOptions",
    "WorkerRuntime",
    "build_runtime",
    "definition_for",
    "resolve",
    "run",
    "serialize_error",
]
