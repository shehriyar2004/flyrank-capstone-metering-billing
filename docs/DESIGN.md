# Design Doc — Usage Metering & Billing Engine (Phase 1 gate)

## Problem
Every SaaS must answer three questions: how much has this customer used, what does it cost,
and have they hit their limit? This service is the backend that answers all three for tenants
on Free/Pro plans: idempotent usage metering, honest quota boundaries, exact money math
(including the tricky AI-token pricing rules), and Stripe test-mode subscription sync.

## Data model
- **tenants**(id, name, plan, subscription_status, stripe_customer_id, stripe_subscription_id)
- **usage_events**(id, tenant_id, event_type, api_calls, input_tokens, cached_input_tokens,
  output_tokens, reasoning_tokens, idempotency_key, created_at) —
  `UNIQUE(tenant_id, idempotency_key)` is where the no-double-count guarantee lives.
- **idempotency_records**(tenant_id, key, endpoint, status_code, response_json) —
  replays return the stored status + body verbatim (exact mirror, not a re-computation).
- **stripe_events**(event_id PK, event_type) — webhook dedupe; a replayed event is ignored.
- **alerts**, **job_runs** — background-job observability (usage warnings, failures).

Tenant isolation: every data-access query filters by `tenant_id`. Tenants never see each
other's data. Usage is windowed by calendar month (UTC).

## API surface
- `POST /generate` — the one dummy billable endpoint. Body: tenant_id, idempotency_key,
  api_calls, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens.
  Meters → checks quota → records, or rejects honestly.
- `GET /usage?tenant_id=&month=YYYY-MM` — rollup: used, limits, remaining, cost.
- `POST /billing/checkout` — creates a Stripe Checkout Session (test mode) for Pro.
- `POST /webhooks/stripe` — signature-verified, idempotent webhook handler.
- `POST /tenants`, `GET /tenants/{id}` — demo/admin tenant management.
- `GET /health` — liveness.

## Layer sketch
```
HTTP (FastAPI routes + pydantic validation)
  → services (Metering, Quota, Billing, StripeSync — pure logic)
    → repo (SQL only, tenant-scoped queries)
      → SQLite (schema via versioned migrations)
Background jobs run on a scheduler thread → alerts / job_runs tables.
```

## Key decisions
- **Idempotency:** replay-check then insert inside a single `BEGIN IMMEDIATE` transaction;
  the UNIQUE constraint is the backstop against race double-inserts. A retried request —
  accepted *or* rejected — returns the originally stored response.
- **Quota boundary:** a request is allowed iff `used + requested <= limit`. At exactly the
  limit, the next request is rejected. Quota exceeded → **429** with used/limit/reset info;
  billing problem (`past_due` / `unpaid` / `canceled` subscription) → **402**.
- **Money:** prices pinned as integer rationals (numerator/denominator) in USD cents.
  Reasoning tokens bill as output tokens; cached input tokens are cheaper; categories are
  never naively summed. One round-half-up at the final total. No floats anywhere.
- **Stripe:** test mode only, secrets in `.env` (git-ignored). The DB mirrors Stripe only
  through signature-verified, deduplicated webhook events.
- **Plans (documented, pinned):** Free = 1,000 API calls + 100k tokens/month, $0.
  Pro = 100,000 API calls + 10M tokens/month, $20/month.

## Explicit non-goal
No invoicing, proration, or overage billing in the core build — those are stretch goals.
The engine reports metered cost; it does not issue invoices or charge cards.
