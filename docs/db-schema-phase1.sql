-- Phase 1 target schema (PostgreSQL). This is the canonical production
-- target; the in-memory classes in src/domain/*.ts are reference
-- implementations used for fast, dependency-free tests in this
-- environment. An implementing team wires a Postgres-backed adapter to
-- the same interfaces (AuditStore, etc.) before Phase 2.
--
-- Tenant isolation is enforced with row-level security, not just
-- application code (Blueprint §6: "never rely solely on UI filtering").

CREATE TABLE tenant (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE actor (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  kind            TEXT NOT NULL CHECK (kind IN ('human','ai_agent')),
  display_name    TEXT NOT NULL,
  owner_actor_id  UUID REFERENCES actor(id), -- required for kind='ai_agent'
  active          BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_actor_tenant ON actor(tenant_id);

CREATE TABLE role (
  tenant_id     UUID NOT NULL REFERENCES tenant(id),
  name          TEXT NOT NULL,
  PRIMARY KEY (tenant_id, name)
);

CREATE TABLE role_permission (
  tenant_id     UUID NOT NULL,
  role_name     TEXT NOT NULL,
  permission    TEXT NOT NULL,
  PRIMARY KEY (tenant_id, role_name, permission),
  FOREIGN KEY (tenant_id, role_name) REFERENCES role(tenant_id, name)
);

CREATE TABLE role_assignment (
  tenant_id     UUID NOT NULL,
  actor_id      UUID NOT NULL REFERENCES actor(id),
  role_name     TEXT NOT NULL,
  PRIMARY KEY (tenant_id, actor_id, role_name),
  FOREIGN KEY (tenant_id, role_name) REFERENCES role(tenant_id, name)
);

-- Append-only. No UPDATE or DELETE grants for the application role in
-- production; corrections are new rows referencing the original via
-- caused_by (Blueprint Invariant 2 & 12).
CREATE TABLE audit_event (
  id              BIGSERIAL PRIMARY KEY,
  tenant_id       UUID NOT NULL REFERENCES tenant(id),
  actor_id        UUID NOT NULL REFERENCES actor(id),
  entity_type     TEXT NOT NULL,
  entity_id       TEXT NOT NULL,
  action          TEXT NOT NULL CHECK (action IN ('create','update','approve','reverse','export','close_period')),
  reason          TEXT NOT NULL,
  previous_value  JSONB,
  new_value       JSONB,
  caused_by       TEXT,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_entity ON audit_event(tenant_id, entity_type, entity_id);

ALTER TABLE actor ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
-- Policy definitions bind to the session's current_setting('app.tenant_id')
-- and are added when the connection-pooling strategy is finalized
-- (OPEN-QUESTIONS.md #2).
