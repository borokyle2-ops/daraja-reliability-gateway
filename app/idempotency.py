from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")


class IdempotencyError(ValueError):
    """Base error for invalid idempotency inputs."""


class IdempotencyConflictError(Exception):
    """Raised when an Idempotency-Key is reused with a different payload."""

    def __init__(self, idempotency_key: str, message: str | None = None) -> None:
        self.idempotency_key = idempotency_key
        self.message = message or (
            f"Idempotency-Key '{idempotency_key}' was already used with a different payload."
        )
        super().__init__(self.message)


@dataclass(frozen=True)
class IdempotencyOutcome(Generic[T]):
    """Result returned by executing an idempotent operation."""

    value: T
    is_replay: bool
    payload_hash: str


@dataclass
class _IdempotencyRecord(Generic[T]):
    """Stored record for a single idempotency key."""

    payload_hash: str
    value: T | None = None
    in_flight: bool = False


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hash for a JSON-serializable payload."""
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def validate_idempotency_key(key: str) -> str:
    """Validate that an Idempotency-Key is non-empty and within safe bounds."""
    if not isinstance(key, str) or not key.strip():
        raise IdempotencyError("Idempotency-Key must be a non-empty string.")
    cleaned = key.strip()
    if len(cleaned) < 8 or len(cleaned) > 255:
        raise IdempotencyError("Idempotency-Key must be between 8 and 255 characters.")
    return cleaned


class InMemoryIdempotencyStore(Generic[T]):
    """
    Beginner-friendly idempotency store for unit testing and early development.

    Tracks payload hashes and saved results per idempotency key.
    """

    def __init__(self) -> None:
        self._records: dict[str, _IdempotencyRecord[T]] = {}

    def execute(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
        operation: Callable[[], T],
    ) -> IdempotencyOutcome[T]:
        """
        Execute `operation` once per `(idempotency_key, payload)` pair.

        - First call with a new key: runs `operation`, saves result, returns `is_replay=False`.
        - Retry with same key & same payload: skips `operation`, returns saved result (`is_replay=True`).
        - Reuse with same key & different payload: raises `IdempotencyConflictError`.
        """
        key = validate_idempotency_key(idempotency_key)
        payload_hash = compute_payload_hash(payload)

        existing = self._records.get(key)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflictError(key)
            if existing.in_flight:
                raise IdempotencyConflictError(
                    key,
                    message=f"Request with Idempotency-Key '{key}' is currently in flight.",
                )
            return IdempotencyOutcome(
                value=existing.value,  # type: ignore[arg-type]
                is_replay=True,
                payload_hash=payload_hash,
            )

        record = _IdempotencyRecord[T](payload_hash=payload_hash, in_flight=True)
        self._records[key] = record

        try:
            result = operation()
        except Exception:
            # Remove unfinished reservation if the operation failed before completing
            self._records.pop(key, None)
            raise

        record.value = result
        record.in_flight = False
        return IdempotencyOutcome(
            value=result,
            is_replay=False,
            payload_hash=payload_hash,
        )