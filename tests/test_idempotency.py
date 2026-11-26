import pytest

from app.idempotency import (
    IdempotencyConflictError,
    IdempotencyError,
    InMemoryIdempotencyStore,
    compute_payload_hash,
)


def test_payload_hash_is_order_independent_for_dict_keys():
    payload_a = {"amount": 250, "currency": "KES", "phone_number": "254708374149"}
    payload_b = {"phone_number": "254708374149", "amount": 250, "currency": "KES"}

    assert compute_payload_hash(payload_a) == compute_payload_hash(payload_b)


def test_first_call_executes_operation_and_retry_replays_without_re_running():
    store: InMemoryIdempotencyStore[dict] = InMemoryIdempotencyStore()
    calls = {"count": 0}
    payload = {"amount": 250, "phone_number": "254708374149"}

    def create_payment() -> dict:
        calls["count"] += 1
        return {"payment_id": "pay_001", "state": "pending", "call_num": calls["count"]}

    first = store.execute("idem-key-123456", payload, create_payment)
    second = store.execute("idem-key-123456", dict(payload), create_payment)

    assert calls["count"] == 1
    assert first.is_replay is False
    assert second.is_replay is True
    assert first.value == second.value


def test_same_key_with_different_payload_raises_conflict():
    store: InMemoryIdempotencyStore[dict] = InMemoryIdempotencyStore()
    original_payload = {"amount": 250, "phone_number": "254708374149"}
    changed_payload = {"amount": 500, "phone_number": "254708374149"}

    store.execute("idem-key-conflict-1", original_payload, lambda: {"payment_id": "pay_001"})

    with pytest.raises(IdempotencyConflictError):
        store.execute("idem-key-conflict-1", changed_payload, lambda: {"payment_id": "pay_002"})


@pytest.mark.parametrize("bad_key", ["", "   ", "short"])
def test_invalid_idempotency_key_raises_error(bad_key):
    store: InMemoryIdempotencyStore[dict] = InMemoryIdempotencyStore()

    with pytest.raises(IdempotencyError):
        store.execute(bad_key, {"amount": 100}, lambda: {"ok": True})


def test_failed_operation_releases_key_so_retry_can_succeed():
    store: InMemoryIdempotencyStore[dict] = InMemoryIdempotencyStore()
    attempts = {"count": 0}

    def flaky_op() -> dict:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("Transient upstream timeout")
        return {"payment_id": "pay_recovered", "state": "pending"}

    with pytest.raises(RuntimeError, match="Transient upstream timeout"):
        store.execute("idem-key-retry-after-err", {"amount": 250}, flaky_op)

    outcome = store.execute("idem-key-retry-after-err", {"amount": 250}, flaky_op)
    assert attempts["count"] == 2
    assert outcome.is_replay is False
    assert outcome.value["payment_id"] == "pay_recovered"