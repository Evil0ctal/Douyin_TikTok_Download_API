"""Process wiring: everything one worker container owns.

Kept apart from the loop in :mod:`dtk.worker.main` because it is the only part
of the package that knows how the whole system is assembled - scheduler,
identity pool, transport, signing and the notifier - and that assembly is what
changes when another module's constructor does, not the loop.

The entry point stays importable as ``dtk.worker.main:main`` so the container
command does not depend on this split.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx

from dtk.core.config import BootstrapSettings, Config
from dtk.core.crypto import Cipher
from dtk.core.db import dispose_engine, init_engine, session_scope
from dtk.core.logging import configure, get_logger
from dtk.core.redis import close_redis, get_redis, init_redis
from dtk.core.types import Platform
from dtk.db.models import Identity as IdentityRow
from dtk.db.models import Proxy as ProxyRow
from dtk.identity.pool import IdentityPool
from dtk.services.fetch import FetchService
from dtk.worker.loop import PeriodicLoop
from dtk.worker.main import DatabaseTaskStore, SessionFactory, TaskWorker, WorkerOptions

log = get_logger(__name__)

#: Ceiling for a browser-rpc call. Minting is the slow one; the signing
#: fallback has its own, shorter per-request timeout.
if TYPE_CHECKING:  # imported lazily at runtime to keep worker startup light
    from dtk.signing.registry import RegistryPolicy

RPC_TIMEOUT_SECONDS = 90.0


class PoolCandidates:
    """Adapts the identity pool to the scheduler's candidate source."""

    __slots__ = ("_pool", "_session_factory")

    def __init__(self, pool: IdentityPool, *, session_factory: SessionFactory = session_scope):
        self._pool = pool
        self._session_factory = session_factory

    async def candidates(self, platform: Platform, state: Any) -> Any:
        async with self._session_factory() as session:
            return await self._pool.candidates(session, platform, state)


class ProxyResolver:
    """Decrypts the egress bound to an identity, once per request.

    The URL holds credentials, so it is never cached in a log-visible place and
    never carried on the identity record in plaintext.
    """

    __slots__ = ("_cipher", "_session_factory")

    def __init__(self, cipher: Cipher, *, session_factory: SessionFactory = session_scope) -> None:
        self._cipher = cipher
        self._session_factory = session_factory

    async def __call__(self, identity_id: str) -> str | None:
        try:
            key = uuid.UUID(identity_id)
        except ValueError:
            return None
        async with self._session_factory() as session:
            row = await session.get(IdentityRow, key)
            if row is None or row.proxy_id is None:
                return None
            proxy = await session.get(ProxyRow, row.proxy_id)
            if proxy is None:
                return None
            return self._cipher.decrypt(proxy.url_encrypted, aad=str(proxy.id))


@dataclass(slots=True)
class WorkerRuntime:
    """Everything one worker process owns, so shutdown can undo it in order."""

    worker: TaskWorker
    loops: list[PeriodicLoop] = field(default_factory=list)
    closers: list[Callable[[], Any]] = field(default_factory=list)

    async def aclose(self) -> None:
        for loop in self.loops:
            loop.request_stop()
        for close in self.closers:
            with contextlib.suppress(Exception):
                await close()


async def build_runtime(
    settings: BootstrapSettings, config: Config | Callable[[], Config]
) -> WorkerRuntime:  # pragma: no cover - wiring, exercised by running the process
    """Assemble the worker process: fetch pipeline plus the background loops.

    ``config`` may be a snapshot or a live source. :func:`run` passes a live one
    so that a console edit reaches this process without a restart; a test may
    pass a plain :class:`Config` and get the old fixed behaviour.

    Not everything below is live even so. The values read through ``current``
    are frozen into objects that take them once at construction - the
    scheduler's wait and health prior, the cooldown bounds, the notifier's
    channels - and those still need a restart. The ones passed as
    ``config_source`` follow the database.
    """
    from dtk.identity.minting import BrowserRpcClient
    from dtk.ops.notify import notifier_from_config
    from dtk.scheduler.scheduler import Scheduler, SchedulerConfig
    from dtk.signing import NativeSigner, RpcSigner, SignerRegistry, native_signers
    from dtk.signing.base import RequestSpec as SigningRequestSpec
    from dtk.signing.base import StaticFingerprint
    from dtk.transport import Fingerprint, WreqTransport
    from dtk.worker.maintenance import Maintenance, MaintenanceConfig
    from dtk.worker.pool_filler import FillerConfig, PoolFiller
    from dtk.worker.proxy_prober import HttpxProbeClient, ProberConfig, ProxyProber

    config_source: Callable[[], Config] = config if callable(config) else (lambda: config)
    current = config_source()

    cipher = Cipher(settings.secret_key)
    pool = IdentityPool(cipher)
    scheduler = Scheduler(
        PoolCandidates(pool),
        SchedulerConfig(
            max_wait_seconds=float(current.get("sched.max_wait_seconds")),
            health_prior=float(current.get("pool.health_prior")),
        ),
    )
    transport = WreqTransport()

    # One HTTP client for both browser-rpc users: minting and the fallback
    # signer talk to the same service, and a second pool would only add a second
    # set of idle connections to it.
    rpc_http: httpx.AsyncClient | None = None
    rpc_client: BrowserRpcClient | None = None
    rpc_signer: RpcSigner | None = None
    if settings.browser_rpc_url:
        rpc_http = httpx.AsyncClient(timeout=RPC_TIMEOUT_SECONDS)
        rpc_client = BrowserRpcClient(settings.browser_rpc_url, client=rpc_http)
        rpc_signer = RpcSigner(
            rpc_http,
            settings.browser_rpc_url,
            timeout=lambda: float(config_source().get("signing.rpc_timeout_seconds")),
        )

    signers: dict[Platform, NativeSigner] = native_signers()
    # The policy is a callable, not a value: signing.mode is the setting an
    # operator reaches for when a signer goes stale, which is exactly the moment
    # a restart-to-apply would cost the most.
    signer_registry = SignerRegistry(signers, rpc_signer, policy=_signing_policy(config_source))

    async def sign(
        platform: Platform, url: str, params: dict[str, Any], fingerprint: Fingerprint
    ) -> dict[str, str]:
        signed = await signer_registry.sign(
            SigningRequestSpec.get(url, {k: str(v) for k, v in params.items()}),
            StaticFingerprint(user_agent=fingerprint.user_agent or ""),
            platform=platform,
        )
        return dict(signed.params)

    fetch = FetchService(
        scheduler=scheduler,
        pool=pool,
        transport=transport,
        sign=sign,
        proxy_resolver=ProxyResolver(cipher),
        cooldown_base=int(current.get("sched.cooldown_base_seconds")),
        cooldown_max=int(current.get("sched.cooldown_max_seconds")),
    )

    # One options object for both halves: the attempt counter's lifetime is a
    # worker setting, and a store built with its own default would quietly
    # ignore whatever the worker was configured with.
    worker_options = WorkerOptions()
    worker = TaskWorker(
        fetch=fetch,
        store=DatabaseTaskStore(attempt_ttl_seconds=worker_options.attempt_ttl_seconds),
        config=config_source,
        options=worker_options,
    )

    # One notifier for every background job, so doc 15's deduplication windows
    # are shared rather than re-implemented per job.
    notifier = notifier_from_config(current, redis=get_redis())

    filler_options = FillerConfig()
    prober_options = ProberConfig()
    maintenance_options = MaintenanceConfig()
    filler = PoolFiller(
        pool=pool,
        rpc=rpc_client,
        cipher=cipher,
        config=config_source,
        options=filler_options,
        alerter=notifier,
    )
    prober = ProxyProber(
        cipher=cipher,
        pool=pool,
        client=HttpxProbeClient(),
        options=prober_options,
        alerter=notifier,
    )
    maintenance = Maintenance(
        cipher=cipher,
        config=config_source,
        options=maintenance_options,
        alerter=notifier,
    )

    loops = [
        PeriodicLoop("pool_filler", filler_options.interval_seconds, filler.tick),
        PeriodicLoop("proxy_prober", prober_options.interval_seconds, prober.tick),
        PeriodicLoop("maintenance", maintenance_options.interval_seconds, maintenance.tick),
    ]
    closers: list[Callable[[], Any]] = [transport.close, prober.aclose, notifier.aclose]
    if rpc_http is not None:
        # The client was injected, so browser-rpc will not close it for us.
        closers.append(rpc_http.aclose)
    return WorkerRuntime(worker=worker, loops=loops, closers=closers)


def _signing_policy(config_source: Callable[[], Config]) -> Callable[[], RegistryPolicy]:
    """Read signing.* out of the live configuration on every use."""
    from dtk.signing.registry import RegistryPolicy, SigningMode

    def policy() -> RegistryPolicy:
        config = config_source()
        return RegistryPolicy(
            mode=cast("SigningMode", config.get("signing.mode")),
            fallback_enabled=bool(config.get("signing.fallback_enabled")),
        )

    return policy


@contextlib.asynccontextmanager
async def _process_scope(
    settings: BootstrapSettings,
) -> AsyncIterator[Callable[[], Config]]:  # pragma: no cover - process wiring
    configure(level=settings.log_level, json_output=settings.log_json)
    init_engine(settings.database_url)
    init_redis(settings.redis_url)
    from dtk.services.settings_store import load_config, start_watcher, stop_watcher

    # The API and the MCP server both watch for configuration changes; the
    # worker did not, so every setting the console exposes was inert in the one
    # process that actually issues the requests until it was restarted. The
    # watcher wants an object with ``.state.config`` and nothing more.
    holder = SimpleNamespace(state=SimpleNamespace(config=Config.defaults()))
    try:
        holder.state.config = await load_config()
        await start_watcher(holder)
        try:
            yield lambda: cast("Config", holder.state.config)
        finally:
            await stop_watcher(holder)
    finally:
        await close_redis()
        await dispose_engine()


async def run(settings: BootstrapSettings | None = None) -> None:  # pragma: no cover - entry point
    """Run one worker process until SIGTERM or SIGINT."""
    settings = settings or BootstrapSettings()
    async with _process_scope(settings) as config_source:
        runtime = await build_runtime(settings, config_source)
        _install_signal_handlers(runtime)
        background = [asyncio.create_task(loop.run(), name=loop.name) for loop in runtime.loops]
        try:
            await runtime.worker.run()
        finally:
            # The loops are stopped and joined *before* the shared clients are
            # closed. Closing first would pull the HTTP client out from under a
            # mint or a probe that is still in flight, turning an orderly
            # shutdown into a stack of "client has been closed" errors.
            for loop in runtime.loops:
                loop.request_stop()
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await runtime.aclose()


def _install_signal_handlers(runtime: WorkerRuntime) -> None:  # pragma: no cover - process wiring
    loop = asyncio.get_running_loop()

    def _handle(name: str) -> None:
        log.info("worker.signal", signal=name)
        runtime.worker.request_stop()
        for background in runtime.loops:
            background.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, _handle, sig.name)


def main() -> None:  # pragma: no cover - console entry point
    asyncio.run(run())


__all__ = [
    "RPC_TIMEOUT_SECONDS",
    "PoolCandidates",
    "ProxyResolver",
    "WorkerRuntime",
    "build_runtime",
    "main",
    "run",
]
