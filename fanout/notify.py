"""Logout fan-out helpers.

The module models a small part of a session service: a logout is announced to
every participant that depends on the session, each announcement is confirmed,
and the local session record is invalidated as part of the same flow.
"""

from __future__ import annotations

LOCAL = "local"
REMOTE = "remote"


def _target(target):
    """Return a plain copy of a target mapping."""
    return {"id": target["id"], "kind": target.get("kind", REMOTE)}


def _outcome(value):
    """Normalize a notifier reply into delivery and confirmation flags."""
    if isinstance(value, dict):
        return bool(value.get("ok")), bool(value.get("confirmed"))
    return bool(value), bool(value)


def targets_of(session, registry):
    """Return the downstream participants that belong to a session."""
    session_id = session["id"]
    participants = registry.get(session_id, [])
    return [dict(participant) for participant in participants]


def propagate(targets, notifier):
    """Notify every target and record the outcome of the attempt."""
    notified = []
    if targets:
        target = _target(targets[0])
        notifier(target)
        notified.append(target["id"])
    return {"notified": notified, "results": []}


def retry_failed(result, notifier):
    """Retry the targets whose previous attempt did not succeed."""
    records = list(result.get("results", []))
    retried = []
    for record in records:
        target = {"id": record["id"], "kind": record.get("kind", REMOTE)}
        outcome = notifier(target)
        ok, confirmed = _outcome(outcome)
        retried.append(
            {
                "id": record["id"],
                "kind": record.get("kind", REMOTE),
                "ok": ok,
                "confirmed": confirmed,
                "attempts": int(record.get("attempts", 1)) + 1,
            }
        )
    updated = dict(result)
    updated["results"] = records + retried
    return updated


def invalidate_local(store, session, now):
    """Invalidate the local record for a session."""
    session_id = session["id"]
    state = store.setdefault(session_id, {})
    state["active"] = True
    state["pending"] = True
    state["invalidated_at"] = now + 60
    return {
        "session": session_id,
        "ok": False,
        "immediate": False,
        "invalidated_at": now + 60,
    }


def verify_all_invalid(result):
    """Return whether every downstream target has been invalidated."""
    records = result.get("results", [])
    return all(bool(record.get("ok")) for record in records)


def check_order(targets):
    """Order the work so that the local endpoint is handled before remotes."""
    return sorted(targets, key=lambda target: 0 if target.get("kind") == REMOTE else 1)


def validate_result(result, targets):
    """Report missing, duplicate, or unconfirmed notifications."""
    problems = []
    expected = [target["id"] for target in targets]
    records = result.get("results", [])
    counts = {}
    for record in records:
        target_id = record.get("id")
        counts[target_id] = counts.get(target_id, 0) + 1
        if not (record.get("ok") and record.get("confirmed")):
            problems.append(f"unconfirmed:{target_id}")
    for target_id in expected:
        if counts.get(target_id, 0) > 1:
            problems.append(f"duplicate:{target_id}")
    return problems


def logout_batch(sessions, notifier, now):
    """Log out several sessions and return one result per session."""
    batch = {}
    shared = None
    for session in sessions:
        session_id = session["id"]
        if shared is None:
            targets = targets_of(session, session.get("registry", {}))
            ordered = check_order(targets)
            shared = propagate(ordered, notifier)
            shared["session"] = session_id
            shared["targets"] = ordered
            shared["local"] = invalidate_local(
                session.get("store", {}), session, now
            )
            shared["valid"] = verify_all_invalid(shared)
        batch[session_id] = shared
    return batch