"""Masking helpers for anything an operator will read.

The probe paths are where credentials are routinely within reach: they decrypt
proxy URLs to test them, load cookie jars to test an identity, and report
whatever the database holds. Every one of those values passes through here
before it reaches a terminal, a log file, a stored task result rendered in the
console, or a diagnostic report that a user will paste into an issue.

Pure functions, no IO: the unit tests cover the masking rules directly rather
than by scraping rendered tables.

:func:`redact_setting` and :func:`unredact_setting` are the pair that makes
masking survivable on an editable surface. A setting is read whole and written
back whole, so masking it on the way out means the mask comes back on the way
in; the second function is what turns that mask back into the stored value.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from dtk.core.errors import InvalidParam

#: What replaces a value that must never be shown at all.
MASK = "***"

#: Placeholder for an absent value. Absent is None everywhere in the codebase;
#: this is only how None is rendered.
ABSENT = "-"

#: Credentials embedded in a URL authority, for free text where the URL cannot
#: be parsed structurally (exception messages, driver errors, tracebacks).
_URL_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.\-]*://)(?P<user>[^/\s:@]+):[^/\s@]+@", re.IGNORECASE
)

#: Signature and session parameters that leak inside URLs and free text. Mirrors
#: the redaction list in dtk.core.logging so a value masked in the log is masked
#: on the terminal too.
_PARAM_RE = re.compile(
    r"\b(msToken|a_bogus|X-Bogus|_signature|sessionid|sid_guard|odin_tt|uid_tt|ttwid|passport_csrf_token)"
    r"=([^&\s;\"']{6,})",
    re.IGNORECASE,
)


#: Credentials that ride in a webhook URL. Separate from _PARAM_RE because the
#: providers, not the platforms, choose these names.
#:
#: ``sign`` is here for a reason that is easy to miss: DingTalk appends
#: ``&timestamp=<ms>&sign=<base64 HMAC>`` at request time, so scrubbing the
#: channel's stored URL removes the access token and leaves the signature. The
#: signature is the secret's check value with its plaintext structure known, so
#: it is disclosure, not noise.
_WEBHOOK_PARAM_RE = re.compile(
    r"\b(access_token|sign|signature|token|key|secret|api_key|apikey|webhook)"
    r"=([^&\s;\"']{4,})",
    re.IGNORECASE,
)

#: Telegram puts its bot token in the path rather than the query.
_BOT_TOKEN_RE = re.compile(r"/bot[0-9]{5,}:[A-Za-z0-9_-]{10,}", re.IGNORECASE)


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Show at most ``keep`` leading characters of a secret.

    A short value is masked whole: revealing three of four characters is not a
    mask. ``None`` renders as the absent marker rather than as an empty string.
    """
    if value is None:
        return ABSENT
    text = value.strip()
    if not text:
        return ABSENT
    if keep <= 0 or len(text) <= keep:
        return MASK
    return f"{text[:keep]}{MASK}"


def mask_url(url: str | None) -> str:
    """Render a proxy URL with its credentials removed.

    Host and port survive because they are what an operator needs to recognize
    the egress; the password never does, and the username keeps two characters
    at most so two accounts on the same host stay distinguishable.
    """
    if url is None:
        return ABSENT
    text = url.strip()
    if not text:
        return ABSENT
    try:
        parts = urlsplit(text)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.netloc:
        # Not a URL we can take apart safely; a bare "user:pass@host" would be
        # parsed as scheme "user", so refuse rather than half-mask it.
        return _URL_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:" + MASK + "@", text)
    if parts.username is None:
        return text
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    user = mask_secret(parts.username, keep=2)
    authority = f"{user}:{MASK}@{host}" if parts.password is not None else f"{user}@{host}"
    return urlunsplit((parts.scheme, authority, parts.path, parts.query, parts.fragment))


def mask_endpoint(url: str | None) -> str:
    """Render a webhook or bot URL as scheme, host and nothing else.

    A notification endpoint is itself a credential: the token sits in the path
    (``/bot<token>/sendMessage``) or the query, and anyone holding the URL can
    post to the channel. Only the host is kept, which is all an operator needs
    to recognize which channel a row describes.
    """
    if url is None:
        return ABSENT
    text = url.strip()
    if not text:
        return ABSENT
    try:
        parts = urlsplit(text)
    except ValueError:
        return MASK
    if not parts.scheme or not parts.hostname:
        return MASK
    host = parts.hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    tail = "/" + MASK if (parts.path.strip("/") or parts.query or parts.fragment) else ""
    return f"{parts.scheme}://{host}{tail}"


def mask_cookies(cookies: Mapping[str, str] | None) -> str:
    """Describe a cookie jar by its names only.

    The names answer the operational question - is ``sessionid`` present, did
    minting produce a ``ttwid`` - and the values are exactly the thing that
    must never be printed.
    """
    if not cookies:
        return ABSENT
    return ", ".join(sorted(cookies))


def mask_api_key(prefix: str | None) -> str:
    """Render an API key by its stored prefix; the key itself is never held."""
    if not prefix:
        return ABSENT
    return f"dtk_{prefix}_{MASK}"


def short_id(value: uuid.UUID | str | None, *, length: int = 8) -> str:
    """First ``length`` characters of an id, for tables that must stay narrow.

    Every command that accepts an id also accepts this short form, so what is
    printed can be pasted straight back in.
    """
    if value is None:
        return ABSENT
    text = str(value)
    return text[:length] if len(text) > length else text


def scrub(text: str) -> str:
    """Remove credentials from arbitrary text before it is printed.

    Applied to every error message the CLI shows and to every probe detail
    stored in a task result. A failed database connection reports its DSN, and
    that DSN carries the password.
    """
    without_urls = _URL_CREDENTIALS_RE.sub(r"\g<scheme>\g<user>:" + MASK + "@", text)
    without_bots = _BOT_TOKEN_RE.sub("/bot" + MASK, without_urls)
    # Webhook credentials are masked whole. The platform parameters below keep a
    # six-character prefix because correlating one request's msToken across two
    # log lines is a real debugging need; no such need exists for a bot token.
    without_webhooks = _WEBHOOK_PARAM_RE.sub(lambda m: f"{m.group(1)}={MASK}", without_bots)
    return _PARAM_RE.sub(lambda m: f"{m.group(1)}={m.group(2)[:6]}...", without_webhooks)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

#: Mapping keys whose value is a credential wherever it turns up in a setting.
#: A channel descriptor is free-form JSON with no schema behind it, so the field
#: name is the only thing that says what a value is.
SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {"token", "secret", "password", "key", "api_key", "webhook"}
)

#: Mapping keys holding a URL that is itself a credential: a bot token or a
#: webhook path is enough to post to the channel, so only the host survives.
URL_FIELDS: Final[frozenset[str]] = frozenset({"url", "endpoint", "webhook_url"})

#: Where the credentials in the same record are presented. A masked value is
#: only carried over while these stay put: an operator may edit notify.channels
#: but must never learn the SMTP password, and moving the channel to a host they
#: control would have the server type that password in for them.
_TARGET_FIELDS: Final[frozenset[str]] = URL_FIELDS | frozenset({"host", "port"})


def is_credential_field(name: str) -> bool:
    """Whether a mapping key names a credential."""
    lowered = name.lower()
    return lowered in SECRET_FIELDS or lowered in URL_FIELDS


def redact_setting(value: Any) -> Any:
    """Mask the credential-bearing parts of a setting value.

    Walks lists and mappings because the values that matter are nested: the
    channel descriptors in ``notify.channels`` are where a bot token lives. Every
    surface that shows a setting goes through here - the CLI's tables, the admin
    API's responses, and the audit row a settings write leaves behind.
    """
    if isinstance(value, Mapping):
        return {field: _redact_field(str(field), item) for field, item in value.items()}
    if isinstance(value, list):
        return [redact_setting(item) for item in value]
    return value


def unredact_setting(value: Any, stored: Any) -> Any:
    """Put back the stored values that the masks in ``value`` stand in for.

    Masking on read breaks an editable setting, because the console reads the
    whole list and writes the whole list back: without this, saving a renamed
    channel would store ``1234***`` as another channel's bot token. A masked
    field means "keep what is stored", and it means only that:

    * the submitted string has to be the mask *of the stored value* for that
      field of that record, so a caller cannot invent one and pull a credential
      out of the database;
    * a record is paired with the stored one at its own index, and only when
      the two agree on type and name. Pairing on type and name alone was worse
      than it sounds: neither is unique and ``name`` is optional, so two
      channels of one type saved untouched swapped credentials silently, and a
      stored channel with no name could never be saved at all. Pairing on
      position alone would be worse still - it lets a caller who has read the
      listing place a mask wherever they like. Index plus agreement is neither;
    * a value that reads as a mask but matches nothing is refused rather than
      stored, so a half-edited field cannot become a credential of ``***``;
    * and nothing is carried into a record whose target moved, because a server
      presenting a kept credential to a newly named host is how a credential
      that cannot be read is read anyway.

    Whatever comes back still goes through ``coerce`` and, for a channel, through
    ``build_channel`` - a merged value is not a validated one.
    """
    if isinstance(value, list):
        pool = (
            [item for item in stored if isinstance(item, Mapping)]
            if isinstance(stored, list)
            else []
        )
        submitted = [item for item in value if isinstance(item, Mapping)]
        return [
            _merge_record(item, _counterpart(item, pool, index, submitted))
            if isinstance(item, Mapping)
            else item
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        return _merge_record(value, stored if isinstance(stored, Mapping) else None)
    return value


def _redact_field(field: str, value: Any) -> Any:
    if not is_credential_field(field):
        return redact_setting(value)
    if isinstance(value, str):
        return mask_endpoint(value) if field.lower() in URL_FIELDS else mask_secret(value)
    if isinstance(value, Mapping | list):
        return redact_setting(value)
    # A credential hand-written into the table as a number is still a credential,
    # and there is nothing about a bare scalar here worth showing.
    return MASK if value is not None else None


def _identity(record: Mapping[str, Any]) -> tuple[Any, Any]:
    return (record.get("type"), record.get("name"))


def _counterpart(
    record: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    index: int,
    submitted: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """The stored record a submitted one is an edit of, or None if it is new.

    First the record at the same index, accepted only if it agrees on type and
    name. The console reads the whole list and writes the whole list back in
    order, so the index is what identifies a row; requiring agreement is what
    stops the index from becoming a way to aim a mask.

    Then a match elsewhere, which deleting a channel needs: removing the first
    of three shifts the other two, and without this a save that only removed a
    row would wipe their credentials. Allowed only when the identity appears
    exactly once on BOTH sides. Unique in the stored list alone is not enough -
    renaming one submitted record to another's name is how a caller who can
    read the masked listing would move a credential onto a record of their
    choosing, and that rename always leaves a duplicate identity in what they
    submit.
    """
    identity = _identity(record)
    at_index = pool[index] if 0 <= index < len(pool) else None
    if at_index is not None and _identity(at_index) == identity:
        return at_index
    if sum(1 for item in submitted if _identity(item) == identity) != 1:
        return None
    matches = [item for item in pool if _identity(item) == identity]
    return matches[0] if len(matches) == 1 else None


def _merge_record(record: Mapping[str, Any], stored: Mapping[str, Any] | None) -> dict[str, Any]:
    previous: Mapping[str, Any] = stored if stored is not None else {}
    merged: dict[str, Any] = {}
    carried: list[str] = []
    for field, submitted in record.items():
        name = str(field)
        if not is_credential_field(name):
            nested = previous.get(field)
            merged[field] = (
                _merge_record(submitted, nested if isinstance(nested, Mapping) else None)
                if isinstance(submitted, Mapping)
                else submitted
            )
            continue
        kept = previous.get(field)
        if kept is not None and submitted == _redact_field(name, kept):
            merged[field] = kept
            carried.append(name.lower())
        elif _looks_masked(submitted):
            raise InvalidParam(
                f"{name} of '{_label(record)}' is a mask and not a value; enter it again",
                details={"field": "value", "record": _label(record), "credential": name.lower()},
            )
        else:
            # An absent field is a removal, not an omission: only a mask keeps
            # what is stored.
            merged[field] = submitted
    if carried and _target_moved(merged, previous):
        raise InvalidParam(
            f"'{_label(record)}' points somewhere new, so its stored credentials "
            "are not carried over; enter them again",
            details={"field": "value", "record": _label(record), "credential": carried[0]},
        )
    return merged


def _looks_masked(value: Any) -> bool:
    """Whether a submitted value is a rendering rather than a credential.

    Deliberately blunt. A credential that genuinely contains ``***`` is refused
    with a message saying so, which costs one person one puzzled minute; the
    alternative is storing a mask as a credential and finding out when an alert
    does not arrive.
    """
    return isinstance(value, str) and (MASK in value or value.strip() == ABSENT)


def _target_moved(merged: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    return any(merged.get(field) != previous.get(field) for field in _TARGET_FIELDS)


def _label(record: Mapping[str, Any]) -> str:
    """How a record names itself in an error the operator has to act on."""
    return str(record.get("name") or record.get("type") or "?")


__all__ = [
    "ABSENT",
    "MASK",
    "SECRET_FIELDS",
    "URL_FIELDS",
    "is_credential_field",
    "mask_api_key",
    "mask_cookies",
    "mask_endpoint",
    "mask_secret",
    "mask_url",
    "redact_setting",
    "scrub",
    "short_id",
    "unredact_setting",
]
