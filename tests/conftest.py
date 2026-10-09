"""Shared test fixtures: isolated temp DB, test webhook secret, no scheduler."""
import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="metering-test-")
os.environ["DATABASE_PATH"] = os.path.join(_tmp, "test.db")
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test_capstone_secret"
os.environ["STRIPE_SECRET_KEY"] = ""  # checkout endpoint stays honest: 501
os.environ["JOB_INTERVAL_SECONDS"] = "3600"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from src.api.app import create_app  # noqa: E402
from src.db import connect  # noqa: E402


@pytest.fixture()
def client():
    app = create_app(run_scheduler=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def tenant(client):
    r = client.post("/tenants", json={"name": "Test Tenant"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture()
def db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Signed Stripe webhook helpers (shared by webhook + pricing tests). Events are
# signed exactly the way the Stripe CLI signs them: HMAC-SHA256 over
# "t={ts}.{payload}" with the whsec_ secret.
# ---------------------------------------------------------------------------
import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402

WEBHOOK_SECRET = "whsec_test_capstone_secret"


def sign_webhook_payload(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def post_webhook_event(client, event: dict, secret: str = WEBHOOK_SECRET):
    payload = json.dumps(event).encode()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": sign_webhook_payload(payload, secret),
                 "content-type": "application/json"},
    )


def checkout_completed_event(tenant_id: str) -> dict:
    return {
        "id": f"evt_{uuid.uuid4().hex[:16]}",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": f"cs_{uuid.uuid4().hex[:12]}",
            "client_reference_id": tenant_id,
            "customer": f"cus_{uuid.uuid4().hex[:12]}",
            "subscription": f"sub_{uuid.uuid4().hex[:12]}",
            "metadata": {"tenant_id": tenant_id},
        }},
    }
