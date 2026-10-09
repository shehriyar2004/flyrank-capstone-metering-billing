"""POST /webhooks/stripe — Stripe event receiver.

Order of operations is the whole game:
  1. Verify the signature FIRST (forged payload -> 400, nothing changes).
  2. Deduplicate by Stripe event ID (replay -> 200, processed once).
  3. Only then mirror subscription truth into the tenant row.

Payment truth lives at Stripe; this database is a mirror fed by verified events.
"""
import stripe
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...config import settings
from ...services import StripeSyncService
from .common import error_body, get_conn

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        return JSONResponse(status_code=503, content=error_body(
            "webhook_not_configured",
            "STRIPE_WEBHOOK_SECRET is not set; cannot verify signatures."))

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError:
        # Malformed payload.
        return JSONResponse(status_code=400, content=error_body(
            "invalid_payload", "webhook payload is not valid JSON"))
    except stripe.SignatureVerificationError:
        # Forged or tampered event: reject, change nothing.
        return JSONResponse(status_code=400, content=error_body(
            "invalid_signature",
            "webhook signature verification failed; event ignored"))

    # construct_event returns a Stripe object; convert to a plain dict at the
    # boundary so the service layer stays dependency-free.
    conn = get_conn(request)
    try:
        outcome = StripeSyncService.apply_event(conn, event.to_dict())
    finally:
        conn.close()
    return {"received": True, **outcome}
