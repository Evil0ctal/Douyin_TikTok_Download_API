# Contract tests

These hit the live platform. They exist to answer one question that nothing else
can: **did the platform change?**

V4's endpoints died one at a time, silently, and were discovered only when a user
opened an issue. Unit tests cannot notice - they run against fixtures captured
when the endpoint still worked, so they keep passing while production breaks.

## They deliberately do not gate merges

A contract test fails for reasons that have nothing to do with the diff under
review: the platform hiccups, a proxy drops, the identity pool is empty, the
network is slow. Making a pull request wait on that teaches everyone to ignore a
red check, and once that habit forms the signal is gone even when it is real.

So: scheduled runs, results on a dashboard, an alert after two consecutive
failures. Never a required check.

## Running them

```bash
# Requires a running deployment with a populated identity pool.
DTK_CONTRACT_BASE_URL=http://127.0.0.1:8000 \
DTK_CONTRACT_API_KEY=dtk_xxx_yyy \
uv run pytest tests/contract -m live -v
```

Without those variables every test skips, which is what happens in ordinary CI.

## Two assertion levels

**Structural, strict.** The response parses and every required field is present.
A failure here means the platform changed shape and the parser needs updating.

**Numeric, loose.** Invariants only - a public video has a non-negative like
count, a profile has a nickname. Never exact values: those change by the second
and would make the suite flap for no reason.

## Choosing test subjects

Use long-lived public content: an official account, a pinned video. Anything that
can be deleted turns a platform-change alarm into a false one, and a false alarm
in this suite is expensive precisely because the suite is meant to be trusted.

Subjects live in `subjects.py` so replacing a deleted one is a one-line change.
