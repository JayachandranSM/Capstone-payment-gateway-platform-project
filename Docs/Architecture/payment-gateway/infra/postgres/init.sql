-- init.sql — runs once on first start of the postgres container.
-- Bootstrap stage: extensions + empty schemas. Service-specific tables will
-- be added in later turns by the implementing developer.

-- Extensions ---------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- gen_random_uuid(), digest()
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector (HNSW + ops)

-- Schemas (boundary discipline: each service owns its own schema) ----------
CREATE SCHEMA IF NOT EXISTS core;            -- core-api: identity, payments, ledger, fraud
CREATE SCHEMA IF NOT EXISTS ai;              -- ai-service: embeddings, agent runs, evaluations
CREATE SCHEMA IF NOT EXISTS ops;             -- shared: audit, feature flags (future)

-- Smoke-test table the /readyz endpoint can SELECT from to prove the
-- connection is healthy (does not create per-service business tables —
-- those land with the relevant feature work).
CREATE TABLE IF NOT EXISTS ops.health_probe (
    probed_at TIMESTAMPTZ PRIMARY KEY DEFAULT now()
);

INSERT INTO ops.health_probe (probed_at) VALUES (now()) ON CONFLICT DO NOTHING;
