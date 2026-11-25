# Architectural Decisions — Daraja Reliability Gateway

> **Scope Notice:** Sandbox only. No real money, no real customer PII, no production funds.

## ADR-001: Strict Payment & Payout State Machine
- **Status:** Accepted
- **Context:** Safaricom Daraja callbacks can arrive late, twice, out of order, or after a timeout sweeper has already marked a transaction `timed_out`. Allowing arbitrary status updates corrupts accounting and triggers conflicting merchant webhooks.
- **Decision:** Enforce explicit allowed transitions across states:
  - Allowed transitions:
    - `created -> pending` (upstream STK Push / B2C accepted)
    - `created -> failed` (upstream rejected/errored before pending)
    - `pending -> succeeded` (terminal success callback or status query confirmation)
    - `pending -> failed` (terminal failure/cancellation callback or status query confirmation)
    - `pending -> timed_out` (timeout sweeper after deadline)
  - Terminal states (`succeeded`, `failed`, `timed_out`) are immutable.
  - Any duplicate or post-terminal event is logged to an audit/event trail as `IGNORED_TRANSITION` and returns a safe ack without mutating state or emitting duplicate terminal webhooks.
- **Consequences:** State transitions are deterministic and auditable; out-of-order or late callbacks cannot flip settled outcomes.

## ADR-002: Payload-Bound Idempotency (`201` Replay vs. `409 Conflict`)
- **Status:** Accepted
- **Context:** Merchants and SDKs retry network timeouts on `POST /payments` and `POST /payouts`. A naive retry could trigger duplicate STK prompts or duplicate B2C payouts.
- **Decision:** Require `Idempotency-Key` on mutating endpoints (`POST /payments`, `POST /payouts`) and store a canonical SHA-256 hash of the normalized request body alongside the stored response:
  - Same `Idempotency-Key` + **identical** payload hash -> return stored response (`201`).
  - Same `Idempotency-Key` + **different** payload hash -> reject with `409 Conflict` (`IDEMPOTENCY_CONFLICT`).
  - Concurrent in-flight requests with the same key coordinate via database locking/uniqueness so only one upstream call is made.
- **Consequences:** Safe automatic retries on transient errors while catching programmer bugs where a key is accidentally reused for a different payment or payout.