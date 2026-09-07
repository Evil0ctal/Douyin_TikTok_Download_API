"""Outcome classification for upstream responses.

Four categories, from docs/design/02-identity-pool.md:

    OK              the request worked
    BUSINESS_ERROR  the platform answered, the content is gone or private
    RISK_CONTROL    the platform refused this identity
    NETWORK_ERROR   no answer at all: proxy, DNS, TLS, timeout

Getting the BUSINESS / RISK split right is the whole point. V4 treated every
non-200 the same, so looking up one deleted video was enough to condemn a
perfectly good cookie. The inverse mistake costs less but is still real: a
mis-read risk response leaves a burnt identity in rotation.

The ruleset is an ordered tuple of predicates over tables, so a new signal is a
table entry rather than a new branch. That matters because the real signatures
have to come from live samples (docs/design/16-salvage-and-debug.md item 5);
the tables below carry what can be asserted without them and are the documented
seam for the rest.

Two ordering decisions are load bearing:

* A well-formed platform envelope is read before the HTTP status, because these
  APIs answer business errors with HTTP 200 and a body-level status code.
* Body markers are scanned only when the envelope is absent or non-zero. A
  captcha interstitial is HTML or a non-zero envelope; a video description that
  happens to contain the word "captcha" is neither, and scanning it blindly
  would cool a healthy identity over user-generated text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from dtk.core.types import Outcome
from dtk.transport.base import RawResponse, TransportFailure

#: How much of the body is scanned for challenge markers. A captcha page is
#: small and declares itself immediately; a real payload is large and its user
#: text sits far deeper than this.
MARKER_SCAN_CHARS: Final[int] = 4096

#: Body keys that carry the platform's own return code.
ENVELOPE_STATUS_KEYS: Final[tuple[str, ...]] = ("status_code", "statusCode", "error_code")

#: Body keys that carry the platform's own message.
ENVELOPE_MESSAGE_KEYS: Final[tuple[str, ...]] = ("status_msg", "statusMsg", "message")

#: HTTP statuses that mean the request never reached the platform, or the
#: platform's own edge failed. Counted as network so proxy health is probed.
#:
#: 407 is emitted by the proxy itself and never by the platform: the exit is
#: misconfigured or its subscription lapsed. 408 is a timeout wearing a status
#: code. Both have to reach docs/design/02-identity-pool.md's proxy health probe
#: instead of being filed as "the content is missing", which would leave every
#: identity behind that proxy hammering it while looking healthy.
NETWORK_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {407, 408, 502, 503, 504, 520, 521, 522, 523, 524}
)

#: HTTP statuses that mean the platform refused this caller. 444 is nginx
#: closing the connection with no response, which is what Douyin's edge does to
#: a request it does not like.
RISK_STATUS_CODES: Final[frozenset[int]] = frozenset({401, 403, 405, 412, 429, 444})

#: HTTP statuses that describe the content rather than the caller. 451 is a
#: takedown: legitimate, permanent, and nothing to do with the identity.
BUSINESS_STATUS_CODES: Final[frozenset[int]] = frozenset({400, 404, 410, 451})

#: Body-level status codes that mean "prove you are human". Provisional; extend
#: from captured samples. 10000 is TikTok's verification envelope.
RISK_STATUS_VALUES: Final[MappingProxyType[int, str]] = MappingProxyType(
    {
        10000: "verification required",
    }
)

#: Body-level status codes that describe the content. Provisional; extend from
#: captured samples. 2053 is Douyin for an aweme that is no longer available.
BUSINESS_STATUS_VALUES: Final[MappingProxyType[int, str]] = MappingProxyType(
    {
        2053: "aweme unavailable",
    }
)

#: Lowercased substrings that identify a challenge or verification interstitial.
#: Kept tight on purpose: every false positive here cools a healthy identity.
RISK_BODY_MARKERS: Final[tuple[str, ...]] = (
    "verify_center",
    "verify_page",
    "verifycenter",
    "captcha",
    "secsdk",
    "byted_acrawler",
    "slide_verify",
    "tiktok-verify-page",
)

#: Single-object payload keys that must not be empty on a 200. This is the
#: canonical risk signature from docs/design/02-identity-pool.md: the envelope
#: is intact, the payload was withheld.
#:
#: List payloads are excluded deliberately. An empty `aweme_list` is the normal
#: end of pagination, and treating it as risk control would cool an identity
#: every time a caller reached the last page of a profile.
EMPTY_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    "aweme_detail",
    "aweme_info",
    "itemInfo",
    "userInfo",
    "user_info",
    "user",
)

#: Success statuses that are *defined* to carry no body. An empty body is the
#: correct answer for these, so the "withheld payload" reading does not apply:
#: treating a 204 as risk control would cool an identity for a request that
#: worked exactly as specified.
BODYLESS_STATUS_CODES: Final[frozenset[int]] = frozenset({204, 205})

#: Exception type names that mean the request never produced a response. The
#: match is by name so wreq can rename or reparent its exceptions without this
#: table importing them.
NETWORK_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "BodyError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectTimeout",
        "DecodingError",
        "OSError",
        "ProxyConnectionError",
        "ReadTimeout",
        "RedirectError",
        "RequestError",
        "TimeoutError",
        "TlsError",
        "UpgradeError",
    }
)


@dataclass(frozen=True, slots=True)
class Classification:
    """Why a response landed in an outcome.

    `rule` is a dotted identifier, stored on the request log so a shift in the
    mix of risk signals is visible without re-reading bodies.
    """

    outcome: Outcome
    rule: str
    detail: str | None = None


class ResponseView:
    """Parses a response once for the whole rule chain.

    Rules are predicates that each want the status, the decoded text or the JSON
    envelope; without this the chain would decode a large body several times.
    """

    __slots__ = ("_head", "_payload", "_payload_parsed", "_text", "response")

    def __init__(self, response: RawResponse) -> None:
        self.response = response
        self._text: str | None = None
        self._head: str | None = None
        self._payload: Any = None
        self._payload_parsed = False

    @property
    def status(self) -> int:
        return self.response.status

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = self.response.text
        return self._text

    @property
    def head(self) -> str:
        """Lowercased prefix of the body, for marker scanning."""
        if self._head is None:
            self._head = self.text[:MARKER_SCAN_CHARS].lower()
        return self._head

    @property
    def payload(self) -> Any:
        if not self._payload_parsed:
            self._payload = self.response.json_or_none()
            self._payload_parsed = True
        return self._payload

    @property
    def envelope(self) -> Mapping[str, Any] | None:
        payload = self.payload
        return payload if isinstance(payload, Mapping) else None

    @property
    def envelope_status(self) -> int | None:
        envelope = self.envelope
        if envelope is None:
            return None
        for key in ENVELOPE_STATUS_KEYS:
            value = envelope.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.lstrip("-").isdigit():
                return int(value)
        return None

    @property
    def envelope_message(self) -> str | None:
        envelope = self.envelope
        if envelope is None:
            return None
        for key in ENVELOPE_MESSAGE_KEYS:
            value = envelope.get(key)
            if isinstance(value, str) and value:
                return value
        return None


#: A rule returns False/None for no match, True for a match, or a string that
#: also becomes the classification detail.
RuleResult = bool | str | None
RuleTest = Callable[[ResponseView], RuleResult]


@dataclass(frozen=True, slots=True)
class ClassificationRule:
    name: str
    outcome: Outcome
    test: RuleTest


def _network_status(view: ResponseView) -> RuleResult:
    if view.status in NETWORK_STATUS_CODES:
        return f"http {view.status}"
    return False


def _risk_envelope_status(view: ResponseView) -> RuleResult:
    status = view.envelope_status
    if status is not None and status in RISK_STATUS_VALUES:
        return f"{status}: {RISK_STATUS_VALUES[status]}"
    return False


def _business_envelope_status(view: ResponseView) -> RuleResult:
    status = view.envelope_status
    if status is not None and status in BUSINESS_STATUS_VALUES:
        return f"{status}: {BUSINESS_STATUS_VALUES[status]}"
    return False


def _challenge_marker(view: ResponseView) -> RuleResult:
    envelope_status = view.envelope_status
    if view.envelope is not None and envelope_status == 0:
        # A well-formed success envelope. Any marker in here is user content.
        return False
    head = view.head
    for marker in RISK_BODY_MARKERS:
        if marker in head:
            return f"marker {marker}"
    return False


def _risk_status(view: ResponseView) -> RuleResult:
    if view.status in RISK_STATUS_CODES:
        return f"http {view.status}"
    return False


def _business_status(view: ResponseView) -> RuleResult:
    if view.status in BUSINESS_STATUS_CODES:
        return f"http {view.status}"
    return False


def _nonzero_envelope(view: ResponseView) -> RuleResult:
    status = view.envelope_status
    if status is None or status == 0:
        return False
    message = view.envelope_message
    return f"{status}: {message}" if message else str(status)


def _empty_body(view: ResponseView) -> RuleResult:
    if not view.response.ok or view.status in BODYLESS_STATUS_CODES:
        return False
    if not view.response.body:
        return "empty body"
    return False


def _withheld_payload(view: ResponseView) -> RuleResult:
    if not view.response.ok:
        return False
    envelope = view.envelope
    if envelope is None:
        return False
    for key in EMPTY_PAYLOAD_KEYS:
        if key in envelope and not envelope[key]:
            return f"empty {key}"
    return False


def _server_error(view: ResponseView) -> RuleResult:
    if view.status >= 500:
        return f"http {view.status}"
    return False


def _client_error(view: ResponseView) -> RuleResult:
    if view.status >= 400:
        return f"http {view.status}"
    return False


#: Order matters; see the module docstring.
DEFAULT_RULES: Final[tuple[ClassificationRule, ...]] = (
    ClassificationRule("http.network_status", Outcome.NETWORK_ERROR, _network_status),
    ClassificationRule("envelope.risk_code", Outcome.RISK_CONTROL, _risk_envelope_status),
    ClassificationRule("envelope.business_code", Outcome.BUSINESS_ERROR, _business_envelope_status),
    ClassificationRule("body.challenge_marker", Outcome.RISK_CONTROL, _challenge_marker),
    ClassificationRule("http.risk_status", Outcome.RISK_CONTROL, _risk_status),
    ClassificationRule("http.business_status", Outcome.BUSINESS_ERROR, _business_status),
    ClassificationRule("envelope.nonzero", Outcome.BUSINESS_ERROR, _nonzero_envelope),
    ClassificationRule("body.empty", Outcome.RISK_CONTROL, _empty_body),
    ClassificationRule("payload.withheld", Outcome.RISK_CONTROL, _withheld_payload),
    ClassificationRule("http.server_error", Outcome.NETWORK_ERROR, _server_error),
    ClassificationRule("http.client_error", Outcome.BUSINESS_ERROR, _client_error),
)


class Classifier:
    """An ordered ruleset. First match wins; no match means OK."""

    __slots__ = ("_rules",)

    def __init__(self, rules: Sequence[ClassificationRule] = DEFAULT_RULES) -> None:
        self._rules = tuple(rules)

    @property
    def rules(self) -> tuple[ClassificationRule, ...]:
        return self._rules

    def extend(
        self,
        rules: Sequence[ClassificationRule],
        *,
        first: bool = False,
    ) -> Classifier:
        """Return a new classifier with extra rules; this one is unchanged.

        `first=True` puts the new rules ahead of the built-ins, which is what a
        platform-specific signature needs when it has to pre-empt a general one.
        """
        extra = tuple(rules)
        return Classifier(extra + self._rules if first else self._rules + extra)

    def classify(
        self,
        response: RawResponse | None = None,
        exception: BaseException | None = None,
    ) -> Classification:
        if exception is not None:
            return classify_exception(exception)
        if response is None:
            raise ValueError("classify needs either a response or an exception")

        view = ResponseView(response)
        for rule in self._rules:
            result = rule.test(view)
            if result:
                detail = result if isinstance(result, str) else None
                return Classification(rule.outcome, rule.name, detail)
        return Classification(Outcome.OK, "default.ok")


def classify_exception(exception: BaseException) -> Classification:
    """Classify a failure that produced no response.

    Everything lands in NETWORK_ERROR: by definition the platform never
    answered, so nothing can be said about the identity's standing. The rule
    name still records which exception it was, so an unclassified type shows up
    in the logs instead of hiding behind the default.
    """
    cause = exception
    if isinstance(exception, TransportFailure) and exception.cause is not None:
        cause = exception.cause
    name = type(cause).__name__
    detail = str(cause) or None
    if name in NETWORK_EXCEPTION_NAMES:
        return Classification(Outcome.NETWORK_ERROR, f"exception.{name}", detail)
    if isinstance(cause, TimeoutError | OSError):
        return Classification(Outcome.NETWORK_ERROR, f"exception.{name}", detail)
    return Classification(Outcome.NETWORK_ERROR, "exception.unclassified", detail)


#: Shared instance. Rules are pure, so it is safe to use from any task.
DEFAULT_CLASSIFIER: Final[Classifier] = Classifier()


def classify_detailed(
    response: RawResponse | None = None,
    exception: BaseException | None = None,
) -> Classification:
    """Classify with the default ruleset, keeping the matched rule name."""
    return DEFAULT_CLASSIFIER.classify(response, exception)


def classify(
    response: RawResponse | None = None,
    exception: BaseException | None = None,
) -> Outcome:
    """Classify with the default ruleset."""
    return DEFAULT_CLASSIFIER.classify(response, exception).outcome


__all__ = [
    "BODYLESS_STATUS_CODES",
    "BUSINESS_STATUS_CODES",
    "BUSINESS_STATUS_VALUES",
    "DEFAULT_CLASSIFIER",
    "DEFAULT_RULES",
    "EMPTY_PAYLOAD_KEYS",
    "ENVELOPE_STATUS_KEYS",
    "MARKER_SCAN_CHARS",
    "NETWORK_EXCEPTION_NAMES",
    "NETWORK_STATUS_CODES",
    "RISK_BODY_MARKERS",
    "RISK_STATUS_CODES",
    "RISK_STATUS_VALUES",
    "Classification",
    "ClassificationRule",
    "Classifier",
    "ResponseView",
    "RuleResult",
    "RuleTest",
    "classify",
    "classify_detailed",
    "classify_exception",
]
