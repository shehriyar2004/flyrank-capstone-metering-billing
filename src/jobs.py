"""Background jobs: usage alerts + Stripe reconciliation.

Runs on a scheduler thread (started by the app lifespan), never on the request
path. Every run is recorded in job_runs; a job that exhausts its retries raises
a job_failure alert so a human notices.
"""
import threading
import time
import traceback

from . import repo
from .config import PLANS, settings
from .db import connect


def _alert_kind(tenant_id: str, quota: str, threshold: int, month: str) -> str:
    return f"quota_{threshold}pct:{quota}:{month}:{tenant_id}"


def run_usage_alerts(conn) -> dict:
    """Warn tenants at 80% of any quota and at 100% (limit reached)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    month_label = f"{now.year:04d}-{now.month:02d}"
    created = 0
    for tenant in repo.list_tenants(conn):
        plan = PLANS[tenant["plan"]]
        used = repo.month_usage(conn, tenant["id"], now.year, now.month)
        tokens = (
            used["input_tokens"] + used["cached_input_tokens"]
            + used["output_tokens"] + used["reasoning_tokens"]
        )
        for quota_name, used_n, limit in (
            ("api_calls", used["api_calls"], plan.api_call_limit),
            ("ai_tokens", tokens, plan.token_limit),
        ):
            pct = (100.0 * used_n / limit) if limit else 0.0
            for threshold in (100, 80):
                if pct >= threshold:
                    kind = _alert_kind(tenant["id"], quota_name, threshold, month_label)
                    if not repo.alert_exists(conn, tenant["id"], kind):
                        repo.add_alert(
                            conn, tenant["id"], kind,
                            f"Tenant '{tenant['name']}' reached {threshold}% of "
                            f"{quota_name} quota ({used_n}/{limit}) for {month_label}.",
                        )
                        created += 1
                    break  # only the highest crossed threshold per quota
    conn.commit()
    return {"alerts_created": created}


def run_reconciliation(conn) -> dict:
    """Nightly-style check: does our DB agree with Stripe about subscriptions?

    Skipped (not failed) when Stripe isn't configured — reconciliation needs the
    Stripe API, and test-mode keys are optional for a local demo.
    """
    if not settings.stripe_configured:
        return {"skipped": True, "reason": "stripe_not_configured"}
    import stripe

    stripe.api_key = settings.stripe_secret_key
    stripe_subs = {}
    for sub in stripe.Subscription.list(status="active", limit=100).auto_paging_iter():
        stripe_subs[sub.id] = sub.metadata.get("tenant_id")

    mismatches = []
    for tenant in repo.list_tenants(conn):
        sid = tenant["stripe_subscription_id"]
        if not sid:
            continue
        stripe_tenant = stripe_subs.get(sid)
        if stripe_tenant is None:
            mismatches.append(
                f"tenant {tenant['id']}: DB has subscription {sid} but Stripe "
                "reports it inactive/missing"
            )
            repo.add_alert(
                conn, tenant["id"], f"reconcile_mismatch:{sid}",
                f"Subscription {sid} is active in our DB but not at Stripe.",
            )
    conn.commit()
    return {"checked": len(stripe_subs), "mismatches": mismatches}


def _run_with_retries(conn, job_name: str, fn, attempts: int = 3) -> dict:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            detail = fn(conn)
            repo.record_job_run(conn, job_name, "ok", detail=str(detail))
            conn.commit()
            return {"job": job_name, "status": "ok", "detail": detail}
        except Exception as exc:  # noqa: BLE001 - jobs must not kill the scheduler
            last_error = f"attempt {attempt}: {exc}\n{traceback.format_exc()}"
            time.sleep(min(2 ** attempt, 8))
    repo.record_job_run(conn, job_name, "failed", error=last_error)
    repo.add_alert(
        conn, None, f"job_failure:{job_name}",
        f"Background job '{job_name}' failed after {attempts} attempts. "
        f"Last error: {last_error.splitlines()[0] if last_error else 'unknown'}",
    )
    conn.commit()
    return {"job": job_name, "status": "failed"}


def run_all_jobs() -> dict:
    conn = connect()
    try:
        return {
            "usage_alerts": _run_with_retries(conn, "usage_alerts", run_usage_alerts),
            "reconciliation": _run_with_retries(conn, "reconciliation", run_reconciliation),
        }
    finally:
        conn.close()


def start_scheduler(interval_seconds: int, stop_event: threading.Event) -> threading.Thread:
    def loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                run_all_jobs()
            except Exception:  # noqa: BLE001 - scheduler thread must survive
                continue

    thread = threading.Thread(target=loop, name="job-scheduler", daemon=True)
    thread.start()
    return thread
