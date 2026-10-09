"""PROBE 1 — Exactly-once metering.

The same billable request sent twice with one idempotency key creates exactly
one usage event; the second response mirrors the first.
"""
from src import repo


def test_double_send_creates_one_event(client, tenant, db):
    payload = {
        "tenant_id": tenant,
        "idempotency_key": "probe1-key-abc",
        "api_calls": 1,
        "input_tokens": 100,
    }
    first = client.post("/generate", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/generate", json=payload)
    assert second.status_code == 201, second.text
    assert second.headers.get("x-idempotent-replayed") == "true"
    assert second.json() == first.json(), "replay must mirror the original response"

    assert repo.count_usage_events(db, tenant) == 1


def test_retry_of_rejected_request_mirrors_rejection(client, tenant, db):
    # Exhaust the free API-call quota (1000), then retry the rejected request.
    for i in range(1000):
        r = client.post("/generate", json={
            "tenant_id": tenant, "idempotency_key": f"fill-{i}", "api_calls": 1})
        assert r.status_code == 201, (i, r.text)

    payload = {"tenant_id": tenant, "idempotency_key": "rejected-once", "api_calls": 1}
    first = client.post("/generate", json=payload)
    assert first.status_code == 429, first.text
    second = client.post("/generate", json=payload)
    assert second.status_code == 429
    assert second.json() == first.json()
    # Still exactly 1000 events: rejections are never metered.
    assert repo.count_usage_events(db, tenant) == 1000
