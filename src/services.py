"""Service layer: pure business logic. No HTTP, no SQL strings.

MeteringService is the heart of the capstone: exactly-once usage recording under
retries, with honest quota boundaries.
"""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from . import repo
from .config import BILLING_BLOCKED_STATUSES, PLANS, PRICING
from .money import cost_cents, format_cents


class TenantNotFound(Exception):
    pass


class NothingToMeter(Exception):
    pass


@dataclass
class MeterResult:
    status_code: int
    body: dict
    replayed: bool  # True when this is a duplicate-key replay of an earlier result


class MeteringService:
    """Records billable usage exactly once per idempotency key."""

    @staticmethod
    def record(
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        idempotency_key: str,
        api_calls: int,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
    ) -> MeterResult:
        if api_calls == 0 and (
            input_tokens + cached_input_tokens + output_tokens + reasoning_tokens
        ) == 0:
            raise NothingToMeter("request meters nothing: all quantities are zero")

        requested_tokens = (
            input_tokens + cached_input_tokens + output_tokens + reasoning_tokens
        )

        # One IMMEDIATE transaction: replay-check, quota-check, and insert are
        # atomic, so two racing retries cannot both insert.
        conn.execute("BEGIN IMMEDIATE")
        try:
            tenant = repo.get_tenant(conn, tenant_id)
            if tenant is None:
                conn.rollback()
                raise TenantNotFound(f"unknown tenant_id: {tenant_id}")

            # 1) Idempotency: an already-seen key returns the ORIGINAL response
            #    verbatim — whether it was accepted or rejected.
            seen = repo.get_idempotency_record(conn, tenant_id, idempotency_key)
            if seen is not None:
                import json

                conn.commit()
                return MeterResult(
                    status_code=seen["status_code"],
                    body=json.loads(seen["response_json"]),
                    replayed=True,
                )

            plan = PLANS[tenant["plan"]]
            now = datetime.now(timezone.utc)
            used = repo.month_usage(conn, tenant_id, now.year, now.month)
            used_api = used["api_calls"]
            used_tokens = (
                used["input_tokens"]
                + used["cached_input_tokens"]
                + used["output_tokens"]
                + used["reasoning_tokens"]
            )
            resets_at = repo.next_month_start_iso(now.year, now.month)[:10]

            # 2) Billing gate: a broken subscription blocks with 402, not 429.
            if tenant["subscription_status"] in BILLING_BLOCKED_STATUSES:
                body = {
                    "error": {
                        "code": "payment_required",
                        "message": (
                            f"Tenant subscription is '{tenant['subscription_status']}'. "
                            "Resolve billing (or downgrade to Free) to resume usage."
                        ),
                        "tenant_id": tenant_id,
                        "subscription_status": tenant["subscription_status"],
                    }
                }
                repo.save_idempotency_record(
                    conn, tenant_id=tenant_id, key=idempotency_key,
                    endpoint="POST /generate", status_code=402, body=body,
                )
                conn.commit()
                return MeterResult(status_code=402, body=body, replayed=False)

            # 3) Quota gate: allowed iff used + requested <= limit.
            #    At exactly the limit, the next request is rejected (429).
            if used_api + api_calls > plan.api_call_limit:
                body = _quota_error(
                    tenant_id, "api_calls", used_api, plan.api_call_limit, resets_at
                )
                return _reject(conn, tenant_id, idempotency_key, body)
            if used_tokens + requested_tokens > plan.token_limit:
                body = _quota_error(
                    tenant_id, "ai_tokens", used_tokens, plan.token_limit, resets_at
                )
                return _reject(conn, tenant_id, idempotency_key, body)

            # 4) Record exactly one usage event.
            event_type = "ai_tokens" if requested_tokens > 0 else "api_call"
            try:
                event_id = repo.insert_usage_event(
                    conn,
                    tenant_id=tenant_id,
                    event_type=event_type,
                    api_calls=api_calls,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    idempotency_key=idempotency_key,
                )
            except sqlite3.IntegrityError:
                # Lost the race with a concurrent retry: the other insert won,
                # so return ITS stored response instead of double-counting.
                conn.rollback()
                return MeteringService.record(
                    conn,
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    api_calls=api_calls,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                )

            body = {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "idempotency_key": idempotency_key,
                "recorded": {
                    "api_calls": api_calls,
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_tokens": reasoning_tokens,
                },
                "quota_remaining": {
                    "api_calls": plan.api_call_limit - (used_api + api_calls),
                    "ai_tokens": plan.token_limit - (used_tokens + requested_tokens),
                },
            }
            repo.save_idempotency_record(
                conn, tenant_id=tenant_id, key=idempotency_key,
                endpoint="POST /generate", status_code=201, body=body,
            )
            conn.commit()
            return MeterResult(status_code=201, body=body, replayed=False)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def _quota_error(tenant_id, quota_name, used, limit, resets_at):
    return {
        "error": {
            "code": "quota_exceeded",
            "message": (
                f"{quota_name} quota exceeded for tenant '{tenant_id}': "
                f"used {used} of {limit} this month. "
                f"Quota resets on {resets_at}. Upgrade to Pro for higher limits."
            ),
            "tenant_id": tenant_id,
            "quota": quota_name,
            "used": used,
            "limit": limit,
            "resets_at": resets_at,
        }
    }


def _reject(conn, tenant_id, idempotency_key, body):
    repo.save_idempotency_record(
        conn, tenant_id=tenant_id, key=idempotency_key,
        endpoint="POST /generate", status_code=429, body=body,
    )
    conn.commit()
    return MeterResult(status_code=429, body=body, replayed=False)


class BillingService:
    """Monthly cost rollups from usage events using the pinned pricing."""

    @staticmethod
    def usage_summary(
        conn: sqlite3.Connection, tenant_id: str, year: int, month: int
    ) -> dict:
        tenant = repo.get_tenant(conn, tenant_id)
        if tenant is None:
            raise TenantNotFound(f"unknown tenant_id: {tenant_id}")
        plan = PLANS[tenant["plan"]]
        used = repo.month_usage(conn, tenant_id, year, month)
        total_tokens = (
            used["input_tokens"]
            + used["cached_input_tokens"]
            + used["output_tokens"]
            + used["reasoning_tokens"]
        )
        cents = cost_cents(
            used["api_calls"],
            used["input_tokens"],
            used["cached_input_tokens"],
            used["output_tokens"],
            used["reasoning_tokens"],
            PRICING,
        )
        return {
            "tenant_id": tenant_id,
            "plan": tenant["plan"],
            "subscription_status": tenant["subscription_status"],
            "month": f"{year:04d}-{month:02d}",
            "usage": {
                "api_calls": used["api_calls"],
                "input_tokens": used["input_tokens"],
                "cached_input_tokens": used["cached_input_tokens"],
                "output_tokens": used["output_tokens"],
                "reasoning_tokens": used["reasoning_tokens"],
                "total_tokens": total_tokens,
                "events": used["events"],
            },
            "limits": {
                "api_calls": plan.api_call_limit,
                "ai_tokens": plan.token_limit,
            },
            "remaining": {
                "api_calls": max(0, plan.api_call_limit - used["api_calls"]),
                "ai_tokens": max(0, plan.token_limit - total_tokens),
            },
            "cost": {
                # Metered cost of this month's usage at the pinned rates.
                "cents": cents,
                "display": format_cents(cents),
                "currency": "USD",
            },
            "plan_monthly_price": {
                "cents": plan.monthly_price_cents,
                "display": format_cents(plan.monthly_price_cents),
                "currency": "USD",
            },
        }


class StripeSyncService:
    """Mirrors Stripe subscription truth into tenants — only from verified,
    deduplicated webhook events."""

    @staticmethod
    def apply_event(conn: sqlite3.Connection, event: dict) -> dict:
        """Apply a verified Stripe event. Returns an outcome description."""
        event_id = event["id"]
        event_type = event["type"]

        if not repo.record_stripe_event(conn, event_id, event_type):
            conn.commit()
            return {"outcome": "duplicate_ignored", "event_id": event_id}

        data = event.get("data", {}).get("object", {}) or {}

        if event_type == "checkout.session.completed":
            tenant_id = (data.get("metadata") or {}).get("tenant_id") or data.get(
                "client_reference_id"
            )
            tenant = repo.get_tenant(conn, tenant_id) if tenant_id else None
            if tenant is None and data.get("customer"):
                tenant = repo.get_tenant_by_stripe_customer(conn, data["customer"])
            if tenant is None:
                conn.commit()
                return {"outcome": "ignored_unknown_tenant", "event_id": event_id}
            repo.update_tenant_subscription(
                conn,
                tenant["id"],
                plan="pro",
                subscription_status="active",
                stripe_customer_id=data.get("customer"),
                stripe_subscription_id=data.get("subscription"),
            )
            conn.commit()
            return {"outcome": "upgraded_to_pro", "tenant_id": tenant["id"]}

        if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            customer_id = data.get("customer")
            tenant = (
                repo.get_tenant_by_stripe_customer(conn, customer_id)
                if customer_id
                else None
            )
            if tenant is None:
                conn.commit()
                return {"outcome": "ignored_unknown_tenant", "event_id": event_id}
            stripe_status = data.get("status", "")
            if event_type == "customer.subscription.deleted" or stripe_status == "canceled":
                plan, status = "free", "canceled"
            elif stripe_status in ("past_due", "unpaid"):
                plan, status = tenant["plan"], stripe_status
            else:  # active, trialing, etc.
                plan, status = tenant["plan"], "active"
            repo.update_tenant_subscription(
                conn, tenant["id"], plan=plan, subscription_status=status,
                stripe_subscription_id=data.get("id"),
            )
            conn.commit()
            return {
                "outcome": "subscription_synced",
                "tenant_id": tenant["id"],
                "plan": plan,
                "subscription_status": status,
            }

        if event_type == "invoice.payment_failed":
            customer_id = data.get("customer")
            tenant = (
                repo.get_tenant_by_stripe_customer(conn, customer_id)
                if customer_id
                else None
            )
            if tenant is not None:
                repo.update_tenant_subscription(
                    conn, tenant["id"], plan=tenant["plan"],
                    subscription_status="past_due",
                )
            conn.commit()
            return {
                "outcome": "marked_past_due" if tenant else "ignored_unknown_tenant",
                "tenant_id": tenant["id"] if tenant else None,
            }

        conn.commit()
        return {"outcome": "ignored_unhandled_type", "event_id": event_id,
                "event_type": event_type}
