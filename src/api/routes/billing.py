"""Stripe Checkout (test mode): customer picks Pro -> Checkout -> subscription.

Without STRIPE_SECRET_KEY configured this returns 501 with setup instructions —
it never pretends to work and never touches live mode.
"""
import stripe
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import repo
from ...config import settings
from ..schemas import CheckoutRequest
from .common import error_body, get_conn

router = APIRouter()


@router.post("/billing/checkout")
def create_checkout(payload: CheckoutRequest, request: Request):
    if not settings.stripe_configured:
        return JSONResponse(status_code=501, content=error_body(
            "stripe_not_configured",
            "Stripe test mode is not configured. Set STRIPE_SECRET_KEY, "
            "STRIPE_WEBHOOK_SECRET and STRIPE_PRO_PRICE_ID in .env "
            "(see .env.example), then restart.",
        ))

    conn = get_conn(request)
    try:
        tenant = repo.get_tenant(conn, payload.tenant_id)
        if tenant is None:
            return JSONResponse(status_code=404, content=error_body(
                "tenant_not_found", f"unknown tenant_id: {payload.tenant_id}"))
        if tenant["plan"] == "pro" and tenant["subscription_status"] == "active":
            return JSONResponse(status_code=409, content=error_body(
                "already_pro", "tenant already has an active Pro subscription",
                tenant_id=payload.tenant_id))
    finally:
        conn.close()

    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            client_reference_id=payload.tenant_id,
            customer_email=None,
            line_items=[{"price": settings.stripe_pro_price_id, "quantity": 1}],
            metadata={"tenant_id": payload.tenant_id},
            subscription_data={"metadata": {"tenant_id": payload.tenant_id}},
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
    except Exception as exc:  # Stripe API failure: report, don't 500 blindly
        return JSONResponse(status_code=502, content=error_body(
            "stripe_error", f"Stripe Checkout failed: {exc}"))
    return {"checkout_url": session.url, "session_id": session.id,
            "tenant_id": payload.tenant_id}
