# Security Architecture

Treat this as HIPAA-relevant from day one. Current HIPAA Security Rule is the legal baseline; the January 2025 NPRM (90 FR 898) is a forward-looking design input, never described as current law (`docs/COMPLIANCE-ASSUMPTIONS.md` #14).

## Implemented today
- **Deny-by-default RBAC** (`src/domain/identity.ts`): `assertPermission` requires an explicit permission grant; there is no admin bypass, so every privileged action is a visible, auditable grant.
- **Tenant isolation check**: `assertPermission` throws `AuthorizationError` on any cross-tenant access attempt, independent of whether the actor otherwise holds the permission (Golden Test 8).
- **Append-only audit**: `AuditStore` interface exposes only `record` and read queries — no update/delete — so the reference implementation and any future Postgres-backed one are both structurally incapable of mutating history.
- **Row-level security stubbed** in `docs/db-schema-phase1.sql` (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`) — policies not yet written pending the connection-pooling decision in `OPEN-QUESTIONS.md` #2.

## Not yet implemented (do not assume otherwise)
- No authentication (no OAuth/OIDC, no session handling, no MFA).
- No encryption-at-rest/in-transit configuration (no live infrastructure yet).
- No secrets management (no live infrastructure yet).
- No PHI-specific field-level access controls (no PHI-bearing entities exist yet — Client/Care Plan modules are Phase 2).
- No break-glass access pattern.
- No backup/DR strategy (no live database yet).
- No dependency/vulnerability scanning configured in CI.
- No penetration-testing plan (premature before there's a running system).

## Defense-in-depth target (per source spec §8)
Application authorization → repository/service authorization → database constraints → PostgreSQL RLS → automated cross-tenant tests. Currently only the first and last layers exist (application-level `assertPermission` + `tests/golden.test.ts` Golden Test 8). The middle three layers require the live database decision in `OPEN-QUESTIONS.md` #2.

## AI-specific security (not yet applicable — no AI runtime exists)
When the AI runtime is built (Phase 8 per the roadmap), it must have: tool-level permissions, short-lived credentials, approval gates, damage caps, circuit breakers, prompt-injection defenses (untrusted input treated as data, never instructions), tamper-evident action logs, kill switches, and model/prompt/policy versioning. None of this is implemented; recorded here so it isn't forgotten when Phase 8 starts.
