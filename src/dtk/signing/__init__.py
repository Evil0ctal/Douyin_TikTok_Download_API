"""Request signing: the algorithms, the fallback chain, and the contracts.

``registry.SignerRegistry`` is the entry point for callers; the individual
signers are exported for tests, the CLI diagnostics and the console.

See docs/design/04-transport-signing.md.
"""

from dtk.signing.base import (
    DEFAULT_ALGORITHMS,
    MS_TOKEN_PARAM,
    SIGNER_BROWSER,
    SIGNER_NATIVE,
    RequestSpec,
    SignatureAlgorithm,
    SignedParams,
    Signer,
    SignerHealth,
    SigningFingerprint,
    StaticFingerprint,
    encode_query,
    endpoint_of,
    platform_of,
)
from dtk.signing.native.signer import NativeSigner, native_signers
from dtk.signing.registry import (
    Comparison,
    RegistryPolicy,
    RiskRateSource,
    RiskSample,
    ShadowResult,
    SignerRegistry,
    SlidingRiskWindow,
)
from dtk.signing.rpc import RpcSigner

__all__ = [
    "DEFAULT_ALGORITHMS",
    "MS_TOKEN_PARAM",
    "SIGNER_BROWSER",
    "SIGNER_NATIVE",
    "Comparison",
    "NativeSigner",
    "RegistryPolicy",
    "RequestSpec",
    "RiskRateSource",
    "RiskSample",
    "RpcSigner",
    "ShadowResult",
    "SignatureAlgorithm",
    "SignedParams",
    "Signer",
    "SignerHealth",
    "SignerRegistry",
    "SigningFingerprint",
    "SlidingRiskWindow",
    "StaticFingerprint",
    "encode_query",
    "endpoint_of",
    "native_signers",
    "platform_of",
]
