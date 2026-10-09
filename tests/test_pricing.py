"""PROBE 5 — Pinned pricing rules.

Cached input tokens are cheaper, reasoning tokens bill as output, categories
are never naively added, and GET /usage matches the pinned constants exactly.

Pinned constants (integer rationals, USD cents):
  api_call            1/10    ($0.001)
  input_token         1/2000  ($0.50 / 1M)
  cached_input_token  1/8000  ($0.125 / 1M)
  output_token        3/2000  ($1.50 / 1M, reasoning bills here)
"""
from src.config import PRICING
from src.money import cost_cents, format_cents


def test_cached_input_is_cheaper_than_fresh_input():
    fresh = cost_cents(0, 80_000, 0, 0, 0, PRICING)
    cached = cost_cents(0, 0, 80_000, 0, 0, PRICING)
    assert fresh == 4       # 80_000 * 1/20000 = 4.00c  ($0.50/1M)
    assert cached == 1      # 80_000 * 1/80000 = 1.00c  ($0.125/1M)
    assert cached < fresh


def test_reasoning_tokens_bill_as_output():
    as_reasoning = cost_cents(0, 0, 0, 0, 20_000, PRICING)
    as_output = cost_cents(0, 0, 0, 20_000, 0, PRICING)
    assert as_reasoning == as_output == 3  # 20_000 * 3/20000 = 3.00c


def test_mixed_total_rounds_half_up_once():
    # 100_000 input (5c) + 40_000 cached (0.5c) + 200_000 output + 20_000
    # reasoning (220_000 * 3/20000 = 33c) = 38.5c -> 39c, rounded ONCE at the end.
    assert cost_cents(0, 100_000, 40_000, 200_000, 20_000, PRICING) == 39


def test_rounding_half_up_not_bankers():
    assert cost_cents(5, 0, 0, 0, 0, PRICING) == 1   # 0.5c -> 1c
    assert cost_cents(3, 0, 0, 0, 0, PRICING) == 0   # 0.3c -> 0c


def test_usage_endpoint_matches_pinned_pricing(client, tenant):
    # 360k tokens exceeds the Free quota, so upgrade via a signed webhook first
    # (this also exercises the P3 upgrade path end to end).
    from conftest import checkout_completed_event, post_webhook_event
    r = post_webhook_event(client, checkout_completed_event(tenant))
    assert r.json()["outcome"] == "upgraded_to_pro"

    client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "price-1", "api_calls": 0,
        "input_tokens": 100_000, "cached_input_tokens": 40_000,
        "output_tokens": 200_000, "reasoning_tokens": 20_000})
    body = client.get("/usage", params={"tenant_id": tenant}).json()
    assert body["cost"]["cents"] == 39
    assert body["cost"]["display"] == "$0.39"
    assert body["usage"]["total_tokens"] == 360_000
    # Categories are reported separately, never merged:
    assert body["usage"]["reasoning_tokens"] == 20_000
    assert body["usage"]["output_tokens"] == 200_000


def test_format_cents():
    assert format_cents(2000) == "$20.00"
    assert format_cents(4) == "$0.04"
    assert format_cents(0) == "$0.00"
