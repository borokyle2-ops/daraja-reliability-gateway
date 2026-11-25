from enum import Enum


class PaymentState(str, Enum):
    """All possible statuses a payment can have in the gateway."""

    CREATED = "created"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


# States that cannot change once reached.
TERMINAL_STATES: set[PaymentState] = {
    PaymentState.SUCCEEDED,
    PaymentState.FAILED,
    PaymentState.TIMED_OUT,
}

# Explicit allow-list of legal moves
ALLOWED_TRANSITIONS: dict[PaymentState, set[PaymentState]] = {
    PaymentState.CREATED: {
        PaymentState.PENDING,
        PaymentState.FAILED,
    },
    PaymentState.PENDING: {
        PaymentState.SUCCEEDED,
        PaymentState.FAILED,
        PaymentState.TIMED_OUT,
    },
    # Terminal states have empty sets
    PaymentState.SUCCEEDED: set(),
    PaymentState.FAILED: set(),
    PaymentState.TIMED_OUT: set(),
}


class InvalidStateTransitionError(ValueError):
    """Raised when code attempts a state move that violates ADR-001."""

    def __init__(self, current_state: PaymentState, target_state: PaymentState):
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(
            f"Illegal payment state transition: {current_state.value} -> {target_state.value}"
        )


def is_terminal_state(state: PaymentState) -> bool:
    """Return True if no further state changes are allowed."""
    return state in TERMINAL_STATES


def can_transition(current_state: PaymentState, target_state: PaymentState) -> bool:
    """Return True if moving from current_state to target_state is allowed."""
    return target_state in ALLOWED_TRANSITIONS[current_state]


def transition_payment_state(
    current_state: PaymentState,
    target_state: PaymentState,
) -> PaymentState:
    """
    Validate and return the next payment state.

    Raises InvalidStateTransitionError if the move is not allowed.
    """
    if not can_transition(current_state, target_state):
        raise InvalidStateTransitionError(current_state, target_state)
    return target_state