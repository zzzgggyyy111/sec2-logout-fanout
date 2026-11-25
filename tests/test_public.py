from fanout.notify import (
    check_order,
    invalidate_local,
    logout_batch,
    propagate,
    retry_failed,
    targets_of,
    validate_result,
    verify_all_invalid,
)


def _record(target_id, ok, confirmed, attempts=1):
    return {
        "id": target_id,
        "kind": "remote",
        "ok": ok,
        "confirmed": confirmed,
        "attempts": attempts,
    }


def test_downstream_targets_are_deduplicated():
    registry = {
        "s1": [
            {"id": "laptop-1", "kind": "local"},
            {"id": "phone-1", "kind": "remote"},
            {"id": "tablet-1", "kind": "remote"},
            {"id": "phone-1", "kind": "remote"},
        ]
    }

    targets = targets_of({"id": "s1"}, registry)

    assert [target["id"] for target in targets] == [
        "laptop-1",
        "phone-1",
        "tablet-1",
    ]


def test_propagate_notifies_each_target_and_records_outcome():
    targets = [
        {"id": "laptop-1", "kind": "local"},
        {"id": "phone-1", "kind": "remote"},
        {"id": "tablet-1", "kind": "remote"},
    ]
    calls = []

    def notifier(target):
        calls.append(target["id"])
        return {"ok": True, "confirmed": True}

    result = propagate(targets, notifier)

    assert calls == ["laptop-1", "phone-1", "tablet-1"]
    records = result["results"]
    assert [record["id"] for record in records] == [
        "laptop-1",
        "phone-1",
        "tablet-1",
    ]
    assert all(record["ok"] and record["confirmed"] for record in records)


def test_local_session_invalidated_immediately():
    store = {}

    info = invalidate_local(store, {"id": "s1"}, now=1000)

    state = store["s1"]
    assert state["active"] is False
    assert state["pending"] is False
    assert state["invalidated_at"] == 1000
    assert info["immediate"] is True
    assert info["ok"] is True


def test_retry_sends_again_only_for_failures():
    result = {
        "results": [
            _record("remote-a", True, True, attempts=1),
            _record("remote-b", False, False, attempts=1),
        ]
    }
    calls = []

    def notifier(target):
        calls.append(target["id"])
        return {"ok": True, "confirmed": True}

    retried = retry_failed(result, notifier)

    assert calls == ["remote-b"]
    assert len(retried["results"]) == 2
    by_id = {record["id"]: record for record in retried["results"]}
    assert by_id["remote-a"]["attempts"] == 1
    assert by_id["remote-b"]["attempts"] == 2
    assert by_id["remote-b"]["ok"] is True
    assert by_id["remote-b"]["confirmed"] is True


def test_batch_logout_keeps_sessions_independent():
    store_one = {}
    store_two = {}
    sessions = [
        {
            "id": "s1",
            "registry": {
                "s1": [
                    {"id": "s1-local", "kind": "local"},
                    {"id": "s1-phone", "kind": "remote"},
                ]
            },
            "store": store_one,
        },
        {
            "id": "s2",
            "registry": {
                "s2": [
                    {"id": "s2-local", "kind": "local"},
                    {"id": "s2-phone", "kind": "remote"},
                ]
            },
            "store": store_two,
        },
    ]
    calls = []

    def notifier(target):
        calls.append(target["id"])
        return {"ok": True, "confirmed": True}

    batch = logout_batch(sessions, notifier, now=500)

    assert set(batch) == {"s1", "s2"}
    assert batch["s1"]["session"] == "s1"
    assert batch["s2"]["session"] == "s2"
    assert [target["id"] for target in batch["s1"]["targets"]] == [
        "s1-local",
        "s1-phone",
    ]
    assert [target["id"] for target in batch["s2"]["targets"]] == [
        "s2-local",
        "s2-phone",
    ]
    assert set(calls) == {"s1-local", "s1-phone", "s2-local", "s2-phone"}
    assert store_one["s1"]["active"] is False
    assert store_two["s2"]["active"] is False