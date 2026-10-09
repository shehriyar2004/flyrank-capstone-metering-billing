"""PROBES 3 & 4 — Stripe webhooks.

P3: a signed checkout.session.completed flips the tenant Free -> Pro and
    GET /usage shows the new limits.
P4: a forged webhook (bad signature) -> 400 and nothing changes; replaying a
    real event -> processed once (second delivery ignored).

Events are signed exactly the way the Stripe CLI signs them (HMAC-SHA256 over
"t={ts}.{payload}" with the whsec_ secret), so signature verification is tested
through the real code path.
"""
import uuid

from conftest import checkout_completed_event, post_webhook_event
from src import repo

WEBHOOK_SECRET = "whsec_test_capstone_secret"


def _post_event(client, event: dict, secret: str = WEBHOOK_SECRET):
    return post_webhook_event(client, event, secret)


def _checkout_completed(tenant_id: str) -> dict:
    return checkout_completed_event(tenant_id)


def test_checkout_webhook_upgrades_tenant_to_pro(client, tenant, db):
    event = _checkout_completed(tenant)
    r = _post_event(client, event)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "upgraded_to_pro"

    row = repo.get_tenant(db, tenant)
    assert row["plan"] == "pro"
    assert row["subscription_status"] == "active"
    assert row["stripe_customer_id"]

    usage = client.get("/usage", params={"tenant_id": tenant}).json()
    assert usage["plan"] == "pro"
    assert usage["limits"]["api_calls"] == 100_000
    assert usage["limits"]["ai_tokens"] == 10_000_000


def test_forged_webhook_rejected_nothing_changes(client, tenant, db):
    event = _checkout_completed(tenant)
    r = _post_event(client, event, secret="whsec_wrong_secret")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_signature"

    row = repo.get_tenant(db, tenant)
    assert row["plan"] == "free", "forged event must not change anything"
    assert not repo.stripe_event_seen(db, event["id"])


def test_replayed_event_processed_once(client, tenant, db):
    event = _checkout_completed(tenant)
    first = _post_event(client, event)
    assert first.json()["outcome"] == "upgraded_to_pro"

    # Downgrade manually, then replay: a true duplicate must not re-apply.
    repo.update_tenant_subscription(db, tenant, plan="free",
                                    subscription_status="none")
    db.commit()
    second = _post_event(client, event)
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate_ignored"
    assert repo.get_tenant(db, tenant)["plan"] == "free"


def test_subscription_deleted_downgrades_to_free(client, tenant, db):
    _post_event(client, _checkout_completed(tenant))
    assert repo.get_tenant(db, tenant)["plan"] == "pro"
    customer = repo.get_tenant(db, tenant)["stripe_customer_id"]

    event = {
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": f"sub_{uuid.uuid4().hex[:8]}",
                            "customer": customer, "status": "canceled"}},
    }
    r = _post_event(client, event)
    assert r.json()["outcome"] == "subscription_synced"
    row = repo.get_tenant(db, tenant)
    assert row["plan"] == "free" and row["subscription_status"] == "canceled"
