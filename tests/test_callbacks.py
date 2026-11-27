import pytest

from app.callbacks import (
    CallbackOutcomeStatus,
    CallbackProcessor,
    PaymentRecord,
)
from app.state_machine import PaymentState


def _make_processor(initial_state: PaymentState = PaymentState.PENDING) -> CallbackProcessor:
    processor = CallbackProcessor()
    processor.register_payment(
        PaymentRecord(
            payment_id="pay_test_001",
            checkout_request_id="ws_CO_27112026_001",
            state=initial_state,
        )
    )
    return processor


def test_successful_callback_transitions_pending_to_succeeded_and_stores_receipt():
    processor = _make_processor(PaymentState.PENDING)
    payload = {
        "Body": {
            "stkCallback": {
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 250},
                        {"Name": "MpesaReceiptNumber", "Value": "SWJ88ABC12"},
                    ]
                },
            }
        }
    }

    result = processor.ingest_callback(
        callback_id="cb_001",
        checkout_request_id="ws_CO_27112026_001",
        result_code=0,
        result_desc="The service request is processed successfully.",
        raw_payload=payload,
    )

    assert result.status == CallbackOutcomeStatus.APPLIED
    assert result.previous_state == PaymentState.PENDING
    assert result.current_state == PaymentState.SUCCEEDED
    assert len(processor.raw_events) == 1
    assert processor._payments_by_checkout_id["ws_CO_27112026_001"].mpesa_receipt_number == "SWJ88ABC12"


def test_duplicate_callback_is_recorded_as_raw_event_but_not_reapplied():
    processor = _make_processor(PaymentState.PENDING)

    first = processor.ingest_callback(
        callback_id="cb_dup_1",
        checkout_request_id="ws_CO_27112026_001",
        result_code=0,
        result_desc="Success",
        raw_payload={"result_code": 0},
    )
    second = processor.ingest_callback(
        callback_id="cb_dup_1",
        checkout_request_id="ws_CO_27112026_001",
        result_code=0,
        result_desc="Success",
        raw_payload={"result_code": 0},
    )

    assert first.status == CallbackOutcomeStatus.APPLIED
    assert second.status == CallbackOutcomeStatus.DUPLICATE
    assert second.current_state == PaymentState.SUCCEEDED
    assert len(processor.raw_events) == 2


def test_late_callback_after_timeout_is_ignored_without_mutating_state():
    processor = _make_processor(PaymentState.TIMED_OUT)

    result = processor.ingest_callback(
        callback_id="cb_late_1",
        checkout_request_id="ws_CO_27112026_001",
        result_code=0,
        result_desc="The service request is processed successfully.",
        raw_payload={"result_code": 0},
    )

    assert result.status == CallbackOutcomeStatus.IGNORED_TRANSITION
    assert result.previous_state == PaymentState.TIMED_OUT
    assert result.current_state == PaymentState.TIMED_OUT


def test_cancelled_callback_transitions_pending_to_failed():
    processor = _make_processor(PaymentState.PENDING)

    result = processor.ingest_callback(
        callback_id="cb_cancel_1",
        checkout_request_id="ws_CO_27112026_001",
        result_code=1032,
        result_desc="Request cancelled by user",
        raw_payload={"result_code": 1032},
    )

    assert result.status == CallbackOutcomeStatus.APPLIED
    assert result.current_state == PaymentState.FAILED


def test_unknown_checkout_request_raises_key_error():
    processor = _make_processor(PaymentState.PENDING)

    with pytest.raises(KeyError):
        processor.ingest_callback(
            callback_id="cb_unknown_1",
            checkout_request_id="ws_CO_DOES_NOT_EXIST",
            result_code=0,
            result_desc="Success",
            raw_payload={},
        )