"""Pure Python ports of the platform signature algorithms.

Salvaged from the ``main`` branch (V4, commit 8c98fb7); see the module docstrings
for the original paths and for what changed. These are pure functions with no
configuration and no IO, which is exactly why docs/design/13-testing.md holds
them to 90% coverage.
"""

from dtk.signing.native.abogus import ABogus
from dtk.signing.native.signer import NativeSigner, native_signers
from dtk.signing.native.tokens import (
    gen_false_ms_token,
    gen_ms_token,
    gen_odin_tt,
    gen_real_ms_token,
    gen_s_v_web_id,
    gen_ttwid,
    gen_verify_fp,
)
from dtk.signing.native.xbogus import XBogus

__all__ = [
    "ABogus",
    "NativeSigner",
    "XBogus",
    "gen_false_ms_token",
    "gen_ms_token",
    "gen_odin_tt",
    "gen_real_ms_token",
    "gen_s_v_web_id",
    "gen_ttwid",
    "gen_verify_fp",
    "native_signers",
]
