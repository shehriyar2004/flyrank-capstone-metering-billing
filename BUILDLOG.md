# BUILDLOG.md — honest AI-usage log

The user asked the AI assistant to perform this capstone end to end, following
the brief. Everything below was built in one session on 2026-10-10. I (the AI)
wrote all the code; the user gave direction ("perform this capstone, follow all
instructions") and will review, run, and submit it.

## Where the AI did the work

- **Design (Phase 1):** drafted `docs/DESIGN.md` from the brief — schema,
  API surface, layer sketch, idempotency strategy, the one explicit non-goal
  (no invoicing/proration in core).
- **Scaffolding:** project layout, `src/config.py` (pinned plans/pricing),
  `src/money.py` (integer money math), `src/db.py` (migration runner),
  `migrations/001_init.sql`, `src/repo.py` (tenant-scoped data access).
- **Core logic:** `MeteringService` (idempotent record + quota gates),
  `BillingService` (cost rollups), `StripeSyncService` (webhook → plan sync).
- **HTTP + jobs:** FastAPI routes, validation schemas, webhook verification,
  background scheduler (usage alerts + reconciliation with retries/failure
  alerts), seed script, `run.sh`, `capstone.yaml`.
- **Tests:** 19 pytest tests covering all five acceptance probes; ran green.
- **Docs:** README, EVIDENCE.md, this log, `.env.example`.

## Where the AI was wrong (and what I changed)

1. **Pricing rationals were 10x off.** I first pinned `input_token` at 1/2000¢
   while documenting "$0.50/1M" — that's actually $5.00/1M. Caught it while the
   pricing test expectations didn't match the documented rates; fixed the
   constants to 1/20000¢ etc. and re-derived every test expectation by hand.
   Lesson I should have applied first: always sanity-check a pinned constant
   against its real-world unit before writing tests around it.
2. **`stripe.Webhook.construct_event` returns a Stripe object, not a dict.**
   The webhook tests failed with `AttributeError: 'get' is a dict method...`.
   Fixed by converting with `.to_dict()` at the route boundary so the service
   layer stays dependency-free. I had assumed dict-like access; the test run
   corrected me.
3. **Pricing test exceeded the Free token quota.** The 360k-token pricing test
   hit the 100k Free limit and got 429 instead of a cost figure. Fixed by
   upgrading the test tenant to Pro via a signed webhook first — which also
   made the test exercise the P3 upgrade path end to end.
4. **Over-clever test assertion.** I left a convoluted placeholder assertion in
   `test_jobs.py` (an `if False` inside a generator). Rewrote it as a plain
   before/after count check. If it looks clever, it's probably wrong.

## Decisions the AI made (user should be able to defend)

- **SQLite over Postgres-via-Docker:** the brief allows SQLite, and one-command
  boot on a clean machine (`./run.sh`) beats a Docker dependency for the
  evaluator. Trade-off documented in README limitations.
- **Exact-mirror replays:** a retried request returns the *stored* status code
  and body (201 stays 201), not a recomputed 200 — "mirrors the first" taken
  literally, per Probe 1.
- **429 vs 402 split:** quota exhaustion → 429; `past_due`/`unpaid`/`canceled`
  subscription → 402. Chosen because they are different failures needing
  different user actions (wait/upgrade vs fix billing).
- **Boundary rule `used + requested <= limit`:** at exactly the limit, usage is
  complete and the next request is rejected — documented in README and tested.

## What the user must still do themselves

- Create the **public** GitHub repo `flyrank-capstone-metering-billing` and push
  (needs their GitHub login — the assistant's `gh` is not authenticated).
- Optionally add real Stripe **test-mode** keys to `.env` to demo live Checkout;
  everything else works without them.
- Read the code until any 2–3 lines can be explained — "the AI wrote it" is not
  an answer at evaluation.
