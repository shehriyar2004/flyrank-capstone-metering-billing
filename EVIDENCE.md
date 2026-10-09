# EVIDENCE.md — one pasted proof per Requirements checkbox (§6)

Run the full suite yourself: `./.venv/bin/python -m pytest -q` (19 passed).

Pinned constants under test (from `src/config.py`):
- Plans — Free: 1,000 API calls + 100,000 tokens/month, $0.
  Pro: 100,000 API calls + 10,000,000 tokens/month, $20/month.
- Pricing (integer rationals, USD cents) — api_call 1/10 ($0.001);
  input_token 1/20000 ($0.50/1M); cached_input_token 1/80000 ($0.125/1M);
  output_token 3/20000 ($1.50/1M; reasoning tokens bill here).

## Metering — exactly-once under retries

**Proof:** `tests/test_idempotency.py::test_double_send_creates_one_event` — PASSED

The same `POST /generate` sent twice with idempotency key `probe1-key-abc` created
exactly one `usage_events` row; the second response carried
`X-Idempotent-Replayed: true` and a body identical to the first (status 201 both
times — the original response is mirrored verbatim, not recomputed).

**Proof:** `tests/test_idempotency.py::test_retry_of_rejected_request_mirrors_rejection` — PASSED

After driving a tenant to its 1,000-call quota, the rejected request (429) was
retried with the same key: the retry returned the identical 429 body, and the
event count stayed at exactly 1000 — rejections are never metered, and retries
never double-count.

The guarantee has two layers: a `BEGIN IMMEDIATE` transaction does
replay-check → quota-check → insert atomically, and `UNIQUE(tenant_id,
idempotency_key)` in `migrations/001_init.sql` is the schema-level backstop
(the losing racer re-reads the winner's stored response).

## Quotas — 429 / 402 with honest messages

**Proof:** `tests/test_quota_boundary.py::test_api_call_boundary` — PASSED

Documented rule: allowed iff `used + requested <= limit`. With 999/1000 used,
requesting 1 was allowed (usage became exactly 1000); the next request returned:

```json
{ "error": { "code": "quota_exceeded",
  "message": "api_calls quota exceeded for tenant '...': used 1000 of 1000 this month. Quota resets on 2026-11-01. Upgrade to Pro for higher limits.",
  "quota": "api_calls", "used": 1000, "limit": 1000, "resets_at": "2026-11-01" } }
```

**Proof:** `tests/test_quota_boundary.py::test_token_quota_boundary` — PASSED
(100,000 tokens exactly → allowed; 100,001st → 429, `quota: "ai_tokens"`).

**Proof:** `tests/test_quota_boundary.py::test_past_due_subscription_returns_402` — PASSED

A tenant with `subscription_status = past_due` got **402** `payment_required`
(not 429) and zero usage events were recorded. Rule: quota exhaustion → 429,
billing problems (`past_due`/`unpaid`/`canceled`) → 402.

**Proof:** `tests/test_quota_boundary.py::test_bad_input_is_422_not_500` — PASSED
(negative tokens → 422, missing idempotency key → 422, unknown tenant → 404).

## Cost calculation — token pricing rules

**Proof:** `tests/test_pricing.py` — all 6 PASSED

- `test_cached_input_is_cheaper_than_fresh_input`: 80,000 fresh input = 4¢ vs
  80,000 cached = 1¢.
- `test_reasoning_tokens_bill_as_output`: 20,000 reasoning = 20,000 output = 3¢.
- `test_mixed_total_rounds_half_up_once`: 100,000 input (5¢) + 40,000 cached
  (0.5¢) + 200,000 output + 20,000 reasoning (33¢) = 38.5¢ → **39¢**, rounded
  once, half-up, at the final total. No floats anywhere (`Fraction` math).
- `test_usage_endpoint_matches_pinned_pricing`: `GET /usage` returned
  `cost: {cents: 39, display: "$0.39"}`, matching the pinned constants exactly,
  with token categories reported separately (never merged).

## Stripe integration — Checkout, verified idempotent webhooks

**Proof:** `tests/test_webhooks.py` — all 4 PASSED

- `test_checkout_webhook_upgrades_tenant_to_pro` (Probe 3): a correctly-signed
  `checkout.session.completed` (HMAC-SHA256, same scheme the Stripe CLI uses)
  flipped the tenant Free → Pro with `subscription_status: active`, and
  `GET /usage` then showed the Pro limits (100,000 calls / 10M tokens).
- `test_forged_webhook_rejected_nothing_changes` (Probe 4): the same payload
  signed with the wrong secret → **400** `invalid_signature`; tenant stayed on
  Free and the event was never recorded.
- `test_replayed_event_processed_once` (Probe 4): re-delivering a real event ID
  returned `duplicate_ignored` and did not re-apply the upgrade.
- `test_subscription_deleted_downgrades_to_free`:
  `customer.subscription.deleted` → plan `free`, status `canceled`.

Live `POST /billing/checkout` (real Stripe test-mode Checkout) requires
`STRIPE_SECRET_KEY` / `STRIPE_PRO_PRICE_ID` in `.env`; without them the endpoint
honestly returns **501** with setup instructions instead of pretending. The full
local loop is documented in the README (`stripe listen` + `stripe trigger`).

## Data model, tests & documentation

- Schema is versioned migrations (`migrations/001_init.sql`); indexes on
  `(tenant_id, created_at)` for rollups; every query tenant-scoped.
- 19 tests, one command: `./.venv/bin/python -m pytest -q` → **19 passed**.
- This file, README.md (architecture diagram + run/seed steps + limitations),
  docs/DESIGN.md (Phase 1 design doc), capstone.yaml, BUILDLOG.md, .env.example
  are all present at the repo root.
