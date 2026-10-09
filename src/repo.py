"""Data-access layer: SQL only, every query tenant-scoped.

The service layer never writes SQL; the HTTP layer never touches the DB
except through these functions.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def month_start_iso(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-01 00:00:00"


def next_month_start_iso(year: int, month: int) -> str:
    if month == 12:
        return f"{year + 1:04d}-01-01 00:00:00"
    return f"{year:04d}-{month + 1:02d}-01 00:00:00"


# ---------------------------------------------------------------- tenants ---
def create_tenant(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    tenant_id = f"tnt_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO tenants (id, name, plan, subscription_status, created_at) "
        "VALUES (?, ?, 'free', 'none', ?)",
        (tenant_id, name, utcnow_iso()),
    )
    return get_tenant(conn, tenant_id)


def get_tenant(conn: sqlite3.Connection, tenant_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()


def get_tenant_by_stripe_customer(
    conn: sqlite3.Connection, customer_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tenants WHERE stripe_customer_id = ?", (customer_id,)
    ).fetchone()


def list_tenants(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()


def update_tenant_subscription(
    conn: sqlite3.Connection,
    tenant_id: str,
    *,
    plan: str,
    subscription_status: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> sqlite3.Row | None:
    conn.execute(
        "UPDATE tenants SET plan = ?, subscription_status = ?, "
        "stripe_customer_id = COALESCE(?, stripe_customer_id), "
        "stripe_subscription_id = COALESCE(?, stripe_subscription_id) "
        "WHERE id = ?",
        (plan, subscription_status, stripe_customer_id, stripe_subscription_id, tenant_id),
    )
    return get_tenant(conn, tenant_id)


# ---------------------------------------------------------- usage events ---
def insert_usage_event(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    event_type: str,
    api_calls: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    idempotency_key: str,
) -> int:
    """Insert a usage event. Raises sqlite3.IntegrityError on duplicate
    (tenant_id, idempotency_key) — the schema-level backstop."""
    cur = conn.execute(
        "INSERT INTO usage_events (tenant_id, event_type, api_calls, input_tokens, "
        "cached_input_tokens, output_tokens, reasoning_tokens, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id, event_type, api_calls, input_tokens, cached_input_tokens,
            output_tokens, reasoning_tokens, idempotency_key, utcnow_iso(),
        ),
    )
    return cur.lastrowid


def month_usage(
    conn: sqlite3.Connection, tenant_id: str, year: int, month: int
) -> dict[str, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(api_calls), 0) AS api_calls, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens, "
        "COUNT(*) AS events "
        "FROM usage_events WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
        (tenant_id, month_start_iso(year, month), next_month_start_iso(year, month)),
    ).fetchone()
    return dict(row)


def count_usage_events(conn: sqlite3.Connection, tenant_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()[0]


# ------------------------------------------------------ idempotency store ---
def get_idempotency_record(
    conn: sqlite3.Connection, tenant_id: str, key: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM idempotency_records WHERE tenant_id = ? AND key = ?",
        (tenant_id, key),
    ).fetchone()


def save_idempotency_record(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    key: str,
    endpoint: str,
    status_code: int,
    body: dict,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO idempotency_records "
        "(tenant_id, key, endpoint, status_code, response_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tenant_id, key, endpoint, status_code, json.dumps(body), utcnow_iso()),
    )


# -------------------------------------------------------- stripe events ---
def stripe_event_seen(conn: sqlite3.Connection, event_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM stripe_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        is not None
    )


def record_stripe_event(
    conn: sqlite3.Connection, event_id: str, event_type: str
) -> bool:
    """Returns False if this event was already processed (duplicate)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO stripe_events (event_id, event_type, received_at) "
        "VALUES (?, ?, ?)",
        (event_id, event_type, utcnow_iso()),
    )
    return cur.rowcount == 1


# ----------------------------------------------------------------- alerts ---
def alert_exists(conn: sqlite3.Connection, tenant_id: str | None, kind: str) -> bool:
    if tenant_id is None:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE tenant_id IS NULL AND kind = ?", (kind,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE tenant_id = ? AND kind = ?", (tenant_id, kind)
        ).fetchone()
    return row is not None


def add_alert(
    conn: sqlite3.Connection, tenant_id: str | None, kind: str, message: str
) -> None:
    conn.execute(
        "INSERT INTO alerts (tenant_id, kind, message, created_at) VALUES (?, ?, ?, ?)",
        (tenant_id, kind, message, utcnow_iso()),
    )


# --------------------------------------------------------------- job runs ---
def record_job_run(
    conn: sqlite3.Connection,
    job_name: str,
    status: str,
    error: str | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO job_runs (job_name, started_at, finished_at, status, error, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_name, utcnow_iso(), utcnow_iso(), status, error, detail),
    )
