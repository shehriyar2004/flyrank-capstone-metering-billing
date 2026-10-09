"""PROBE 2 — Boundary honesty.

Documented rule: a request is allowed iff used + requested <= limit.
At 999/1000 requesting 1 -> allowed (usage becomes exactly 1000).
At 1000/1000 requesting anything -> 429 with a clear, machine-readable message.
A tenant with a broken subscription -> 402, not 429.
"""
from src import repo


def test_api_call_boundary(client, tenant):
    for i in range(999):
        r = client.post("/generate", json={
            "tenant_id": tenant, "idempotency_key": f"b-{i}", "api_calls": 1})
        assert r.status_code == 201

    # 999 used + 1 requested <= 1000 -> allowed; usage is now exactly 1000.
    at_boundary = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "b-999", "api_calls": 1})
    assert at_boundary.status_code == 201, at_boundary.text

    # One more -> 429 with an explanatory message.
    over = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "b-1000", "api_calls": 1})
    assert over.status_code == 429, over.text
    err = over.json()["error"]
    assert err["code"] == "quota_exceeded"
    assert err["used"] == 1000 and err["limit"] == 1000
    assert "resets" in err["message"].lower() or "reset" in err["message"].lower()


def test_token_quota_boundary(client, tenant):
    # Free plan: 100,000 tokens/month. Land exactly on it, then exceed.
    r = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "tok-exact",
        "api_calls": 0, "input_tokens": 100_000})
    assert r.status_code == 201, r.text

    over = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "tok-over",
        "api_calls": 0, "input_tokens": 1})
    assert over.status_code == 429
    assert over.json()["error"]["quota"] == "ai_tokens"


def test_past_due_subscription_returns_402(client, tenant, db):
    repo.update_tenant_subscription(
        db, tenant, plan="free", subscription_status="past_due")
    db.commit()
    r = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "blocked-1", "api_calls": 1})
    assert r.status_code == 402, r.text
    assert r.json()["error"]["code"] == "payment_required"
    assert repo.count_usage_events(db, tenant) == 0


def test_bad_input_is_422_not_500(client, tenant):
    r = client.post("/generate", json={
        "tenant_id": tenant, "idempotency_key": "bad-1",
        "api_calls": 0, "input_tokens": -5})
    assert r.status_code == 422
    r = client.post("/generate", json={"tenant_id": tenant})  # missing key
    assert r.status_code == 422
    r = client.post("/generate", json={
        "tenant_id": "no-such-tenant", "idempotency_key": "x", "api_calls": 1})
    assert r.status_code == 404
