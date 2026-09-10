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

#: The header TikTok stamps on a request whose signature it did not accept. The
#: response is 200 with an empty or hollow body, which is the most expensive
#: shape a refusal can take: nothing about it says "signature", so it reads as a
#: dead endpoint.
#:
#: This was originally recorded here as a per-PATH decision that no client could
#: influence - "invariant to TLS profile, cookies, signature". That was wrong,
#: and the way it was wrong is worth keeping. `/api/post/item_list/` is the only
#: endpoint that verifies the X-Dynosaur environment report; the others answer a
#: mis-signed request normally. So a port with two wrong constants
#: (:data:`dtk.signing.native.tiktok_sign.ENV_CODE`) failed on exactly one path
#: and looked like a platform block, and every experiment that varied cookies,
#: identities or regions correctly changed nothing - because the one variable
#: that mattered was never varied.
#:
#: Proven on 2026-09-08 by holding a captured request byte-identical and changing
#: only the signature: the browser's own seal returned 82KB, ours returned 0 bytes
#: and this header. Correcting the constants turned the same call into 495KB.
#: A Firefox TLS profile carrying a Chrome-signed payload also earns it, so the
#: header means "this request did not verify", not "this path is closed".
PLATFORM_GATE_HEADER: Final = "tt_orcas_res"

#: Fragments of Douyin's refusal when the signature it wanted is missing or does
#: not cover the request. Observed live on 2026-09-08, all as 403 bodies of the
#: form "Blocked by ArgusSecurityPlugin <reason>".
#:
#: These are OUR fault, not the identity's, and telling them apart matters. A
#: 403 is otherwise read as risk control, which cools the identity that sent it
#: and counts toward the endpoint's risk rate - so a signer that stopped
#: producing a required parameter would quietly cool the entire pool and trip
#: the circuit breaker, while the actual cause (a signature we did not send) went
#: unnamed. Douyin sign-protects only some endpoints
#: (:mod:`dtk.signing.protection`), and that list is copied from its SDK; when the
#: SDK's list grows, this rule is how the operator finds out.
SIGNATURE_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "uifid not found",
    "signature not found",
    "sign invalid",
    "sign expired",
)

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
        # Measured 2026-09-10 against `tiktok.content_detail`: both a post that
        # was deleted and an id that never existed answer HTTP 200, 205 bytes,
        # `statusCode: 10204` and no `itemInfo`. TikTok does not distinguish the
        # two, so neither does this - what matters is that it is an answer about
        # the content and not a refusal of the caller.
        10204: "item not found or not viewable",
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

#: Where the platform puts its reason for an empty payload. Douyin answers a
#: post it will not return with 200 and::
#:
#:     {"status_code": 0,
#:      "aweme_detail": null,
#:      "filter_detail": {"aweme_id": "...", "filter_reason": "status_self_see",
#:                        "detail_msg": "<why, in Chinese>"}}
#:
#: The empty `aweme_detail` alone is exactly the risk signature, and reading it
#: that way is expensive: every such post cooled the identity that asked for it
#: and counted toward the endpoint's risk rate, so a caller walking a list of
#: older posts could trip the circuit breaker and take the endpoint down for
#: every identity.
#:
#: A NAMED REASON is what makes it an answer - not a populated message.
#:
#: This rule first required a non-empty `detail_msg`, on the reasoning that
#: `{"filter_reason": "core_dep", "detail_msg": "", "notice": ""}` was "a
#: refusal with nothing said" and might be withholding in disguise. Measured
#: against the live API on 2026-09-09, that is simply what Douyin returns for an
#: aweme_id that does not exist:
#:
#:     GET /aweme/v1/web/aweme/detail/?aweme_id=7123456789012345678
#:     200, 211 bytes, {"status_code": 0, "aweme_detail": null,
#:                      "filter_detail": {"aweme_id": "7123456789012345678",
#:                                        "detail_msg": "", "filter_reason":
#:                                        "core_dep", "icon": "", "notice": ""}}
#:
#: So requiring the message turned every typo into UPSTREAM_RISK_CONTROL and
#: cooled a healthy identity for it. `filter_detail` naming the post by id and
#: giving a reason code IS the platform answering about that post; whether it
#: also wrote a sentence for a human is a UI decision on their side, not a
#: signal about ours.
#:
#: What still reads as withholding is `aweme_detail` empty with no
#: `filter_detail` at all - the payload gone with nothing said about it, which
#: is what :func:`_withheld_payload` fires on.
PAYLOAD_REASON_KEYS: Final[tuple[str, ...]] = ("filter_detail",)
#: The field naming why, as a machine code. Observed: "core_dep" (no such post)
#: and "status_self_see" (owner-only).
PAYLOAD_REASON_CODE_KEYS: Final[tuple[str, ...]] = ("filter_reason",)
#: The human sentence beside it, when the platform wrote one. Reported so an
#: operator sees what the platform said, never used to decide the outcome.
PAYLOAD_REASON_MESSAGE_KEYS: Final[tuple[str, ...]] = ("detail_msg", "notice", "msg")

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
        """The platform's own return code, preferring one that says something.

        A body may carry more than one of these keys, and taking the first
        present one is how the meaningful value gets hidden. Measured
        2026-09-10: TikTok answers a post that does not exist with HTTP 200 and
        ``{"statusCode": 10204, "status_code": 0, "status_msg": ""}`` - two
        status fields, and the one this used to read is the one set to zero.

        The response then reached ``payload.bare_envelope``, which saw an
        envelope of nothing but metadata and called it risk control. So looking
        up a deleted video cooled the identity that asked and counted toward the
        endpoint's risk rate - the exact failure this module's docstring opens
        by warning about.

        Zero means "nothing went wrong" in both platforms' vocabulary, so a
        non-zero sibling is the field that is speaking.
        """
        envelope = self.envelope
        if envelope is None:
            return None
        zero_seen = False
        for key in ENVELOPE_STATUS_KEYS:
            value = envelope.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, str) and value.lstrip("-").isdigit():
                value = int(value)
            if not isinstance(value, int):
                continue
            if value == 0:
                zero_seen = True
                continue
            return value
        return 0 if zero_seen else None

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


def _signature_rejected(view: ResponseView) -> RuleResult:
    """TikTok refusing a request whose signature did not verify.

    Ordered before the body rules because it explains what they would otherwise
    report as an empty body or an intact envelope, and it names the cause: the
    signer, not the identity that carried it.

    RISK_CONTROL rather than BUSINESS_ERROR, for the same reason as
    `_signature_refused` below - the request really was refused, and classifying
    a broken signer as a normal business answer is what let two wrong constants
    ship. Tripping the circuit here is the desired behaviour: it stops the pool
    hammering an endpoint with signatures it will never accept, and the rule name
    sends the operator to the signer instead of to the identities.
    """
    if not view.response.ok:
        return False
    marker = view.response.headers.get(PLATFORM_GATE_HEADER)
    if marker in (None, "", "0"):
        return False
    return f"{PLATFORM_GATE_HEADER}={marker}"


def _signature_refused(view: ResponseView) -> RuleResult:
    """The platform rejecting our signature, as opposed to the identity.

    Ordered before `http.risk_status` because both match the same 403 and only
    this one knows why. It is still RISK_CONTROL - the request really was
    refused, and pretending otherwise would let a broken signer look healthy -
    but the rule name says the cause, so it is one glance in the Logs page to
    tell "this identity is burnt" from "we stopped signing this endpoint".
    """
    if view.status not in RISK_STATUS_CODES:
        return False
    head = view.head
    for marker in SIGNATURE_REFUSAL_MARKERS:
        if marker in head:
            return marker
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


def _explained_absence(view: ResponseView) -> RuleResult:
    """An empty payload the platform explained. See PAYLOAD_REASON_KEYS.

    Ordered before `payload.withheld` because it is the same shape read with
    more of the body: both see an empty `aweme_detail`, and only this one
    notices that the platform named the post and said why.

    The detail reports the reason code, plus the platform's own sentence when
    there is one. The code is what decides; the sentence is for whoever reads
    the log.
    """
    if not view.response.ok:
        return False
    envelope = view.envelope
    if envelope is None:
        return False
    if not any(key in EMPTY_PAYLOAD_KEYS and not envelope[key] for key in envelope):
        return False
    for key in PAYLOAD_REASON_KEYS:
        reason = envelope.get(key)
        if not isinstance(reason, dict):
            continue
        code = _first_text(reason, PAYLOAD_REASON_CODE_KEYS)
        message = _first_text(reason, PAYLOAD_REASON_MESSAGE_KEYS)
        # Either half is the platform speaking about this post. A code with no
        # sentence is the ordinary case for a post that does not exist; a
        # sentence with no code is what a withheld one carries. An empty
        # container says nothing and is left to `payload.withheld`.
        if code and message:
            return f"{code}: {message}"
        if code or message:
            return code or message or False
    return False


def _first_text(source: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    """The first of ``keys`` holding non-blank text."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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


#: Keys an envelope may carry that say nothing about the content: the status,
#: its message, and the tracing crumbs both platforms attach to everything.
_ENVELOPE_META_KEYS: Final[frozenset[str]] = frozenset(
    {*ENVELOPE_STATUS_KEYS, *ENVELOPE_MESSAGE_KEYS, "extra", "log_pb", "logid", "log_id"}
)


def _bare_envelope(view: ResponseView) -> RuleResult:
    """A 200 whose envelope carries a status and nothing else.

    Measured on 2026-09-08: Douyin answers ``douyin.author_posts`` with any
    non-zero ``max_cursor`` as HTTP 200 and exactly ``{"status_code":0}`` - 17
    bytes, no ``aweme_list``, no reason - while the same request with
    ``max_cursor=0`` returns 612 KB. It is not the end of the feed; older posts
    demonstrably exist.

    :func:`_withheld_payload` could not see it, because that rule fires on a
    payload key that is *present and empty*, and here the key is absent
    entirely. So the response was classified ``default.ok``, handed to the
    parser, and came back as ``UPSTREAM_CHANGED`` - a non-retryable error whose
    message asks the operator to report a parser bug that no parser change can
    fix. It also went into ``request_log`` as ``outcome=ok, http_status=200``,
    so a systematic refusal never reached pool health or the endpoint breaker.

    Keyed on "nothing but metadata" rather than on a list of payload names,
    because the payload key differs per endpoint - ``aweme_list``, ``comments``,
    ``user`` - and a rule that had to know them all would miss the next one.
    """
    if not view.response.ok:
        return False
    envelope = view.envelope
    if not isinstance(envelope, dict) or not envelope:
        return False
    if any(key not in _ENVELOPE_META_KEYS for key in envelope):
        return False
    return "envelope carries a status and no payload"


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
    ClassificationRule("signature.refused", Outcome.RISK_CONTROL, _signature_refused),
    ClassificationRule("http.risk_status", Outcome.RISK_CONTROL, _risk_status),
    ClassificationRule("http.business_status", Outcome.BUSINESS_ERROR, _business_status),
    ClassificationRule("envelope.nonzero", Outcome.BUSINESS_ERROR, _nonzero_envelope),
    ClassificationRule("signature.rejected", Outcome.RISK_CONTROL, _signature_rejected),
    ClassificationRule("body.empty", Outcome.RISK_CONTROL, _empty_body),
    ClassificationRule("payload.explained", Outcome.BUSINESS_ERROR, _explained_absence),
    ClassificationRule("payload.withheld", Outcome.RISK_CONTROL, _withheld_payload),
    # After payload.withheld and payload.explained, so a refusal that names its
    # reason is still reported as the business answer it is.
    ClassificationRule("payload.bare_envelope", Outcome.RISK_CONTROL, _bare_envelope),
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
