-- 001_init: core schema for the metering & billing engine.
-- Money is stored as integers (cents). Prices are pinned rationals in config.

CREATE TABLE tenants (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  plan                 TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
  subscription_status  TEXT NOT NULL DEFAULT 'none',
  stripe_customer_id   TEXT,
  stripe_subscription_id TEXT,
  created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE usage_events (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id           TEXT NOT NULL REFERENCES tenants(id),
  event_type          TEXT NOT NULL CHECK (event_type IN ('api_call', 'ai_tokens')),
  api_calls           INTEGER NOT NULL DEFAULT 0,
  input_tokens        INTEGER NOT NULL DEFAULT 0,
  cached_input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens       INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
  idempotency_key     TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  -- The no-double-count guarantee lives in the schema:
  UNIQUE (tenant_id, idempotency_key)
);

-- Exact-response replay store: a retried request returns the ORIGINAL
-- status code + body, whether it was accepted or rejected.
CREATE TABLE idempotency_records (
  tenant_id   TEXT NOT NULL,
  key         TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (tenant_id, key)
);

-- Stripe webhook dedupe: event IDs are globally unique per Stripe.
CREATE TABLE stripe_events (
  event_id    TEXT PRIMARY KEY,
  event_type  TEXT NOT NULL,
  received_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE alerts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id  TEXT REFERENCES tenants(id),
  kind       TEXT NOT NULL,
  message    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE job_runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name   TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status     TEXT NOT NULL,
  error      TEXT,
  detail     TEXT
);

-- Rollups always filter by tenant + month, so index that path.
CREATE INDEX idx_usage_tenant_month ON usage_events (tenant_id, created_at);
CREATE INDEX idx_alerts_tenant ON alerts (tenant_id, created_at);
CREATE INDEX idx_tenants_stripe_customer ON tenants (stripe_customer_id);
