"""Who may read the description of a request this instance made.

`?explain=true` hands back the call as it went out: the signed URL, the headers
and the identity's cookie jar. That is a credential, and it lands inside a task
result that an ordinary read key is otherwise entitled to fetch - reading a
result costs exactly the scope that created it, which is the rule that stops a
low-scope key becoming a way around every other scope.

So the explanation is stripped on the way out for anyone who could not have
revealed that jar directly. This file pins that, because the failure mode is
silent: the wrong answer here is a working feature that also hands a session
cookie to every key on the instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from dtk.api.deps import Principal
from dtk.api.routes.tasks import _PRIVILEGED_META, _view_payload, _visible_meta
from dtk.core.types import Language, Scope, TaskState, UserRole
from dtk.services.tasks import TaskView

META = {
    "endpoint": "douyin.content_detail",
    "cached": False,
    "explain": {"cookie_header": "sessionid=not-a-real-value", "url": "https://..."},
}


def principal(*scopes: Scope, role: UserRole = UserRole.OPERATOR) -> Principal:
    """An API key bounded by `scopes` - i.e. one that is genuinely scoped.

    `api_key_id` has to be set: a principal with none is a console session,
    which `permits` treats as bounded by its role rather than by scopes, and
    the whole point here is the key that IS bounded.
    """
    return Principal(
        user_id=uuid.uuid4(),
        role=role,
        scopes=frozenset(scopes),
        api_key_id=uuid.uuid4(),
        rate_limit_per_min=None,
    )


def test_a_read_key_does_not_see_the_explanation() -> None:
    """The case this exists for: douyin:read created the task, and may read the
    result, and must not be handed the jar that served it."""
    visible = _visible_meta(META, principal(Scope.DOUYIN_READ))
    assert "explain" not in visible
    assert visible["endpoint"] == "douyin.content_detail", "the rest must survive"


def test_identity_manage_sees_it() -> None:
    assert "explain" in _visible_meta(META, principal(Scope.IDENTITY_MANAGE))


def test_admin_scope_sees_it() -> None:
    assert "explain" in _visible_meta(META, principal(Scope.ADMIN))


def test_no_principal_strips_it() -> None:
    """The default has to be the safe one.

    Any caller that has not thought about the question gets nothing - the SSE
    stream takes this path deliberately.
    """
    assert "explain" not in _visible_meta(META, None)


def test_meta_without_an_explanation_is_untouched() -> None:
    plain = {"endpoint": "x", "cached": True}
    assert _visible_meta(plain, principal(Scope.DOUYIN_READ)) == plain


@pytest.mark.parametrize("key", sorted(_PRIVILEGED_META))
def test_every_privileged_key_is_gated_on_a_real_scope(key: str) -> None:
    """A typo in the table would silently disable the gate for that key."""
    assert isinstance(_PRIVILEGED_META[key], Scope)


# --------------------------------------------------------------------------
# The same gate on a failed task
# --------------------------------------------------------------------------
#
# A refused request is the case `explain` exists for, so the explanation is
# stored on a failed task too. That added a second path to the same credential,
# and it is the easier one to get wrong: nothing else about a failure is
# privileged, so a `result_meta` emitted beside the error without going through
# `_visible_meta` would look entirely unremarkable in review.


def _failed(result: dict[str, object] | None) -> TaskView:
    return TaskView(
        id=uuid.uuid4(),
        state=TaskState.FAILED,
        endpoint="douyin.content_detail",
        result=result,
        error={"code": "NOT_FOUND", "message": "gone"},
        created_at=datetime(2026, 9, 10, tzinfo=UTC),
        finished_at=datetime(2026, 9, 10, tzinfo=UTC),
    )


STORED = {"data": None, "meta": dict(META)}


def test_a_failed_task_carries_the_explanation_for_a_reader_who_may_see_it() -> None:
    payload = _view_payload(
        _failed(STORED), Language.EN, principal=principal(Scope.IDENTITY_MANAGE)
    )
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["result_meta"]["explain"]["cookie_header"]


def test_a_failed_task_hides_it_from_a_read_key() -> None:
    """The whole point: a refusal must not become the cheap way to a jar."""
    payload = _view_payload(_failed(STORED), Language.EN, principal=principal(Scope.DOUYIN_READ))
    assert payload["error"]["code"] == "NOT_FOUND"
    assert "explain" not in payload.get("result_meta", {})


def test_a_failed_task_hides_it_from_a_caller_nobody_vouched_for() -> None:
    """None strips everything. The SSE stream deliberately passes None."""
    payload = _view_payload(_failed(STORED), Language.EN)
    assert "explain" not in payload.get("result_meta", {})


def test_a_failure_nobody_asked_to_explain_gains_no_metadata_key() -> None:
    payload = _view_payload(_failed(None), Language.EN, principal=principal(Scope.ADMIN))
    assert "result_meta" not in payload
    assert payload["error"]["code"] == "NOT_FOUND"


def test_a_failed_task_still_has_no_data() -> None:
    """`data` stays absent so a reader cannot mistake a failure for an answer."""
    payload = _view_payload(_failed(STORED), Language.EN, principal=principal(Scope.ADMIN))
    assert "data" not in payload
