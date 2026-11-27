from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.state_machine import (
    InvalidStateTransitionError,
    PaymentState,
    transition_payment_state,
)


class CallbackOutcomeStatus(str, Enum):
    """How the gateway handled an incoming callback event."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    IGNORED_TRANSITION = "ignored_transition"


@dataclass(frozen=True)
class RawCallbackEvent:
    """Immutable audit capture of an incoming callback before state evaluation."""

    callback_id: str
    checkout_request_id: str
    result_code: int
    result_desc: str
    raw_payload: dict[str, Any]
    received_at: datetime


@dataclass
class PaymentRecord:
    """Minimal payment record tracked by the callback processor."""

    payment_id: str
    checkout_request_id: str
    state: PaymentState
    result_code: int | None = None
    result_desc: str | None = None
    mpesa_receipt_number: str | None = None


@dataclass(frozen=True)
class CallbackProcessingResult:
    """Outcome returned after ingesting and evaluating a callback."""

    status: CallbackOutcomeStatus
    payment_id: str
    previous_state: PaymentState
    current_state: PaymentState
    reason: str


def map_daraja_result_to_state(result_code: int, result_desc: str = "") -> PaymentState:
    """
    Map a Daraja STK/callback result code to a target PaymentState.

    - ResultCode == 0 -> succeeded
    - Timeout-related descriptions -> timed_out
    - Any other non-zero code -> failed
    """
    if result_code == 0:
        return PaymentState.SUCCEEDED

    normalized_desc = result_desc.lower()
    if "timeout" in normalized_desc or "timed out" in normalized_desc:
        return PaymentState.TIMED_OUT

    return PaymentState.FAILED


def extract_mpesa_receipt(raw_payload: dict[str, Any]) -> str | None:
    """
    Safely extract MpesaReceiptNumber from a Daraja-style callback payload if present.

    Supports both nested STK CallbackMetadata structures and flat test dicts.
    """
    if isinstance(raw_payload.get("mpesa_receipt_number"), str):
        return raw_payload["mpesa_receipt_number"]

    stk_callback = (
        raw_payload.get("Body", {}).get("stkCallback")
        if isinstance(raw_payload.get("Body"), dict)
        else None
    )
    if not isinstance(stk_callback, dict):
        return None

    items = stk_callback.get("CallbackMetadata", {}).get("Item", [])
    if not isinstance(items, list):
        return None

    for item in items:
        if isinstance(item, dict) and item.get("Name") == "MpesaReceiptNumber":
            value = item.get("Value")
            return str(value) if value is not None else None

    return None


class CallbackProcessor:
    """
    Captures raw callbacks, deduplicates by callback_id, and applies safe transitions.
    """

    def __init__(self) -> None:
        self.raw_events: list[RawCallbackEvent] = []
        self._seen_callback_ids: set[str] = set()
        self._payments_by_checkout_id: dict[str, PaymentRecord] = {}

    def register_payment(self, payment: PaymentRecord) -> None:
        """Register a payment so callbacks matching its checkout_request_id can find it."""
        self._payments_by_checkout_id[payment.checkout_request_id] = payment

    def ingest_callback(
        self,
        *,
        callback_id: str,
        checkout_request_id: str,
        result_code: int,
        result_desc: str,
        raw_payload: dict[str, Any],
    ) -> CallbackProcessingResult:
        """Ingest a callback safely with raw capture, deduplication, and state checks."""
        if not callback_id or not callback_id.strip():
            raise ValueError("callback_id must be a non-empty string.")
        if checkout_request_id not in self._payments_by_checkout_id:
            raise KeyError(f"Unknown checkout_request_id: {checkout_request_id}")

        payment = self._payments_by_checkout_id[checkout_request_id]
        previous_state = payment.state

        # 1. Always capture the raw callback first for auditability.
        event = RawCallbackEvent(
            callback_id=callback_id.strip(),
            checkout_request_id=checkout_request_id,
            result_code=result_code,
            result_desc=result_desc,
            raw_payload=dict(raw_payload),
            received_at=datetime.now(timezone.utc),
        )
        self.raw_events.append(event)

        # 2. Deduplicate repeated deliveries of the same callback_id.
        if event.callback_id in self._seen_callback_ids:
            return CallbackProcessingResult(
                status=CallbackOutcomeStatus.DUPLICATE,
                payment_id=payment.payment_id,
                previous_state=previous_state,
                current_state=payment.state,
                reason="Duplicate callback_id ignored.",
            )

        self._seen_callback_ids.add(event.callback_id)

        # 3. Map Daraja result code to target state and apply via state machine.
        target_state = map_daraja_result_to_state(result_code, result_desc)

        try:
            next_state = transition_payment_state(payment.state, target_state)
        except InvalidStateTransitionError as exc:
            return CallbackProcessingResult(
                status=CallbackOutcomeStatus.IGNORED_TRANSITION,
                payment_id=payment.payment_id,
                previous_state=previous_state,
                current_state=payment.state,
                reason=str(exc),
            )

        payment.state = next_state
        payment.result_code = result_code
        payment.result_desc = result_desc

        receipt = extract_mpesa_receipt(raw_payload)
        if receipt and next_state == PaymentState.SUCCEEDED:
            payment.mpesa_receipt_number = receipt

        return CallbackProcessingResult(
            status=CallbackOutcomeStatus.APPLIED,
            payment_id=payment.payment_id,
            previous_state=previous_state,
            current_state=payment.state,
            reason=f"Transitioned {previous_state.value} -> {next_state.value}.",
        )