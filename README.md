# Usage Metering & Billing Engine

The backend every SaaS needs: **how much has this customer used, what does it
cost, and have they hit their limit?** Idempotent usage metering, honest quota
boundaries, exact money math (including AI-token pricing rules), and Stripe
test-mode subscription sync — built for the FlyRank Backend Track capstone.

## What it does

- **Meters** billable actions (`POST /generate`) exactly once per idempotency key —
  retries never double-count.
- **Enforces** plan quotas with honest answers: `429` when the quota is exhausted,
  `402` when the subscription has a billing problem.
- **Prices** usage with pinned, integer-only math: cached input tokens are cheaper,
  reasoning tokens bill as output tokens, one round-half-up at the final total.
- **Syncs** Stripe test-mode subscriptions via signature-verified, deduplicated
  webhooks: payment truth lives at Stripe, the DB mirrors it through verified
  events only.
- **Rolls up** monthly usage + cost per tenant (`GET /usage`).
- **Watches** quotas in the background: 80%/100% usage alerts plus a Stripe
  reconciliation job, with retries and failure alerts.

## Architecture

```
                    +-------------------+
                    |      Client       |
                    +--------+----------+
                             |
              POST /generate |  GET /usage
                             v
              +-----------------------------+
              |        HTTP layer           |  FastAPI routes + pydantic
              |  validation at the boundary |  (bad input -> 4xx, never 500)
              +-------------+---------------+
                            |
              +-------------v---------------+
              |       Service layer         |  MeteringService  (idempotent record)
              |   pure logic, no SQL/HTTP   |  Quota rules      (429 vs 402)
              +-------------+---------------+  BillingService   (cost rollups)
                            |                  StripeSyncService (webhook -> plan)
              +-------------v---------------+
              |       Repository layer      |  SQL only, every query tenant-scoped
              +-------------+---------------+
                            |
              +-------------v---------------+
              |   SQLite (versioned         |  UNIQUE(tenant_id, idempotency_key)
              |   migrations)               |  stripe_events PK dedupe
              +-----------------------------+

  Stripe (test mode)                      Background scheduler thread
  Checkout -> signed webhook ->            usage alerts (80%/100%)
  /webhooks/stripe -> verify -> dedupe     reconciliation vs Stripe API
  -> mirror plan/status                    job_runs log + failure alerts
```

Layering: `src/api/` (HTTP) → `src/services.py` (logic) → `src/repo.py`
(data) → SQLite. Background jobs in `src/jobs.py` never sit on the request path.

## Quickstart (one command, clean machine)

```bash
./run.sh
```

This creates the venv, installs dependencies, applies migrations, seeds two demo
tenants, and serves the API on `http://127.0.0.1:8000`. Then:

```bash
# create a tenant
curl -s -X POST localhost:8000/tenants -H 'content-type: application/json' \
  -d '{"name":"Acme"}'
# -> {"id":"tnt_...","plan":"free", ...}

# meter one billable action (idempotent)
curl -s -X POST localhost:8000/generate -H 'content-type: application/json' -d '{
  "tenant_id":"tnt_...",
  "idempotency_key":"demo-1",
  "input_tokens":1000, "cached_input_tokens":500,
  "output_tokens":2000, "reasoning_tokens":250
}'

# send it again: same body back, X-Idempotent-Replayed: true, one usage event
# monthly rollup
curl -s 'localhost:8000/usage?tenant_id=tnt_...'
```

Other commands: `./.venv/bin/python scripts/seed.py` (idempotent seed),
`./.venv/bin/python -m pytest -q` (19 tests). See `capstone.yaml` for the
evaluator manifest.

## Plans & pricing (pinned in `src/config.py`)

| Plan | API calls / month | AI tokens / month | Price |
|------|-------------------|-------------------|-------|
| Free | 1,000 | 100,000 | $0 |
| Pro  | 100,000 | 10,000,000 | $20/month |

| Unit | Price | Note |
|------|-------|------|
| API call | $0.001 | 1/10 ¢ |
| Input token | $0.50 / 1M | 1/20000 ¢ |
| Cached input token | $0.125 / 1M | cheaper — provider already had it cached |
| Output token | $1.50 / 1M | reasoning tokens bill here, not as a free category |

Money is stored and computed as integers (cents). Per-unit prices are integer
rationals; totals accumulate as exact fractions and round half-up **once** at
the end. No floats anywhere in the money path.

## Boundary & status-code rules

- A request is allowed iff `used + requested <= limit`. At exactly the limit,
  the next request is rejected.
- **429** `quota_exceeded` — usage quota exhausted. The body carries
  `used`, `limit`, `resets_at`, and an upgrade hint.
- **402** `payment_required` — subscription is `past_due`, `unpaid`, or
  `canceled`. Quota problems and billing problems are different failures.
- Retried requests (accepted *or* rejected) return the originally stored
  response verbatim.

## Stripe test mode (no card, no real money — ever)

1. `cp .env.example .env` and fill in `STRIPE_SECRET_KEY`, `STRIPE_PRO_PRICE_ID`
   (both from the Stripe **test-mode** dashboard).
2. In another terminal: `stripe listen --forward-to localhost:8000/webhooks/stripe`
   and copy the printed `whsec_...` into `STRIPE_WEBHOOK_SECRET`.
3. `curl -X POST localhost:8000/billing/checkout -d '{"tenant_id":"tnt_..."}'`
   → open `checkout_url`, pay with `4242 4242 4242 4242` (any future expiry).
4. The `checkout.session.completed` webhook flips the tenant Free → Pro;
   `GET /usage` shows the new limits. Replays (`stripe trigger`) are ignored
   after the first delivery; forged payloads get 400.

Secrets live in `.env` (git-ignored) — a committed Stripe key is an instant
repo-hygiene fail, so `.env.example` ships placeholders only.

## Limitations (honest)

- SQLite, single node: the idempotency transaction serializes writers; under
  heavy concurrent load you may see brief write contention (documented, not
  hidden). Postgres would be the next step, and the repo layer is the seam.
- Monthly windows are calendar months (UTC); no proration or overage billing
  in core (stretch goals).
- The reconciliation job needs Stripe API access; without keys it skips cleanly.
- `POST /generate` is a dummy endpoint — token counts are metered, no model is
  called (by design; no AI key needed).
