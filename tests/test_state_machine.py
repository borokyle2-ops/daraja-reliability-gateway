import pytest

from app.state_machine import (
    InvalidStateTransitionError,
    PaymentState,
    can_transition,
    is_terminal_state,
    transition_payment_state,
)


def test_happy_path_created_to_pending_to_succeeded():
    state = PaymentState.CREATED
    state = transition_payment_state(state, PaymentState.PENDING)
    assert state == PaymentState.PENDING

    state = transition_payment_state(state, PaymentState.SUCCEEDED)
    assert state == PaymentState.SUCCEEDED
    assert is_terminal_state(state) is True


@pytest.mark.parametrize(
    "target",
    [PaymentState.FAILED, PaymentState.TIMED_OUT],
)
def test_pending_can_move_to_failed_or_timed_out(target):
    next_state = transition_payment_state(PaymentState.PENDING, target)
    assert next_state == target


@pytest.mark.parametrize(
    "terminal",
    [PaymentState.SUCCEEDED, PaymentState.FAILED, PaymentState.TIMED_OUT],
)
@pytest.mark.parametrize(
    "next_attempt",
    [PaymentState.CREATED, PaymentState.PENDING, PaymentState.SUCCEEDED, PaymentState.FAILED, PaymentState.TIMED_OUT],
)
def test_terminal_states_cannot_transition_anywhere(terminal, next_attempt):
    assert can_transition(terminal, next_attempt) is False
    with pytest.raises(InvalidStateTransitionError):
        transition_payment_state(terminal, next_attempt)


def test_created_cannot_jump_straight_to_succeeded():
    with pytest.raises(InvalidStateTransitionError):
        transition_payment_state(PaymentState.CREATED, PaymentState.SUCCEEDED)