"""Background jobs: usage alerts fire at 80%/100%, failures are recorded."""
from src import jobs, repo


def test_usage_alerts_at_thresholds(client, tenant, db):
    # Push tenant to exactly 80% of the free API-call quota (800/1000).
    for i in range(800):
        r = client.post("/generate", json={
            "tenant_id": tenant, "idempotency_key": f"alert-{i}", "api_calls": 1})
        assert r.status_code == 201
    result = jobs.run_usage_alerts(db)
    assert result["alerts_created"] >= 1
    kinds = [row["kind"] for row in
             db.execute("SELECT kind FROM alerts WHERE tenant_id = ?", (tenant,))]
    assert any("quota_80pct:api_calls" in k for k in kinds)

    # Running again must not duplicate the 80% alert for the same month.
    before = db.execute(
        "SELECT COUNT(*) FROM alerts WHERE tenant_id = ? AND kind LIKE ?",
        (tenant, "quota_80pct:api_calls%"),
    ).fetchone()[0]
    jobs.run_usage_alerts(db)
    after = db.execute(
        "SELECT COUNT(*) FROM alerts WHERE tenant_id = ? AND kind LIKE ?",
        (tenant, "quota_80pct:api_calls%"),
    ).fetchone()[0]
    assert before == after == 1


def test_failed_job_records_run_and_alert(db):
    def boom(conn):
        raise RuntimeError("simulated job failure")

    outcome = jobs._run_with_retries(db, "test_boom", boom, attempts=2)
    assert outcome["status"] == "failed"
    run = db.execute(
        "SELECT * FROM job_runs WHERE job_name = 'test_boom' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "failed" and "simulated job failure" in run["error"]
    alert = db.execute(
        "SELECT * FROM alerts WHERE kind = 'job_failure:test_boom'"
    ).fetchone()
    assert alert is not None


def test_reconciliation_skipped_without_stripe(db):
    result = jobs.run_reconciliation(db)
    assert result["skipped"] is True
