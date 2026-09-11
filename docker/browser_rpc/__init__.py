"""browser-rpc: the headless-browser side of dtk, as a small RPC service.

Two capabilities, both off the hot path (docs/design/04-transport-signing.md):

* **mint** - drive a real browser through an identity's own proxy until the
  platform hands out guest cookies, and report the fingerprint that browser
  presented. Cookies and fingerprint have to come from the same session, which
  is why this cannot be faked from Python.
* **sign** - run the platform's own JavaScript to sign a request when the native
  port of the algorithm has drifted. Slow but self-updating.

The service is resident rather than launched per call because starting a browser
costs seconds, and the signing path is already a degraded one.

It listens only on the internal compose network and does no authentication: the
network is the trust boundary there, and a second one would add operational
weight without adding safety. The URL and proxy arguments are still validated -
against this service's own mistakes, not against an attacker.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "5.0.1"
