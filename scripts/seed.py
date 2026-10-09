"""Idempotent demo seed: two tenants + sample usage for /usage demos."""
import sys

sys.path.insert(0, ".")

from src import repo
from src.db import connect, migrate


def main() -> None:
    migrate()
    conn = connect()
    try:
        if repo.list_tenants(conn):
            print("seed: tenants already exist, nothing to do")
            return
        acme = repo.create_tenant(conn, "Acme Corp")
        globex = repo.create_tenant(conn, "Globex Inc")
        # A little deterministic usage so GET /usage isn't empty on first run.
        for i in range(12):
            repo.insert_usage_event(
                conn, tenant_id=acme["id"], event_type="api_call", api_calls=1,
                input_tokens=0, cached_input_tokens=0, output_tokens=0,
                reasoning_tokens=0, idempotency_key=f"seed-acme-call-{i}",
            )
        repo.insert_usage_event(
            conn, tenant_id=acme["id"], event_type="ai_tokens", api_calls=0,
            input_tokens=4000, cached_input_tokens=1000, output_tokens=2500,
            reasoning_tokens=500, idempotency_key="seed-acme-tokens-0",
        )
        for i in range(3):
            repo.insert_usage_event(
                conn, tenant_id=globex["id"], event_type="api_call", api_calls=1,
                input_tokens=0, cached_input_tokens=0, output_tokens=0,
                reasoning_tokens=0, idempotency_key=f"seed-globex-call-{i}",
            )
        conn.commit()
        print(f"seed: created tenants {acme['id']} (Acme Corp), {globex['id']} (Globex Inc)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
