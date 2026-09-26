# ARCHITECTURE.md — Actual Architecture and Required Changes

*Written 2026-09-26 during the takeover audit. Companion documents: `CURRENT_STATE_AUDIT.md` (what exists), `REQUIREMENTS_GAP_ANALYSIS.md` (handoff vs. code), `BUILD_PLAN.md` (sequence). This file describes the architecture as it actually is, then the changes needed to reach the target product. It does not restate the handoff's vision; it reconciles it with the code.*

---

## 0. The single most important architectural fact

There is **no one application**. The takeover inherits three artifacts that have never been combined:

| Artifact | Where | Stack | What it is |
|---|---|---|---|
| **A. Billing Reconciliation Assistant** | This repository, root (`app/`, `tests/`), branch `claude/hipaa-billing-reconciliation-qty4bc`, 17 commits, 2026-09-22 → 09-24 | Python 3.11, FastAPI, SQLAlchemy 2, SQLite (Postgres-capable), Jinja2 server-rendered HTML, Fernet field encryption, bcrypt, Docker + Caddy | A working, deployed-ready, single-tenant HIPAA-oriented tool for Parker's Adult Day Program: attendance, participant roster, payer rules, PointClickCare (PCC) batch reconciliation, exception reports. |
| **B. HCBS Workforce & Care Management Platform** | The handoff tarball only (`hcbs-platform/`, 22 commits on `main`, HEAD `bf04d5e`, **no git remote**, not in this repository) | Node 22, TypeScript 5.9, Fastify 5 + Zod 4, Drizzle ORM, PostgreSQL 16 with FORCE RLS, Temporal, Vitest | The multi-tenant, append-only workforce/time/travel/leave/mileage/payroll-export foundation the handoff document describes. API only; no UI. |
| **C. Prior audit branch** | `origin/claude/hcbs-platform-audit-dvrvd0` (5 commits on top of A) | — | A previous session's import of only the **first two** commits of B into `hcbs-platform/`, plus an audit (`AUDIT-2026-09-26.md`), a relocated CI file, and register edits whose numbering conflicts with B's later registers. Stale: B has since advanced 20 commits and fixed most of what C found. |

The handoff document (`PROJECT_HANDOFF.md`) describes **B**. The user-facing request describes "an existing care management application with meaningful working functionality," which is **A**. They share an owner, a tenant (Parker), a compliance posture (HIPAA, PHI minimization, audit) and a brand, but **no code, no schema, no language, no data, and different halves of the operating chain**:

```
Handoff operating chain:
Client → Engagement → Service Plan → Care Plan → Required Work → Workforce → Schedule
  → Work Completed → Documentation → Validation → Hours → Entitlement → Billing/Revenue → Quality

A (Python) covers, single-tenant and day-granular:   Client ─ (payer rule) ─ attended day ─ PCC charge ─ reconciliation exception
B (TypeScript) covers, multi-tenant and minute-granular:                  Workforce ─ Work Activity ─ Hours ─ Leave/Travel/Mileage ─ Payroll export
```

Everything else in this document is conditioned on the **codebase strategy decision** (`BUILD_PLAN.md` Decision D-1). The recommended and assumed strategy is: **B is the platform base; A keeps running unchanged as a production tool until a platform module supersedes it; A's domain becomes the reference specification and data-migration source for that module.** Under that assumption, "preserve everything that already works" means A is not touched except for maintenance, and B's 146 tests, RLS, triggers, grants and golden tests are invariants.

---

## 1. Architecture of A — Billing Reconciliation Assistant (Python, this repository)

### 1.1 Shape

Classic server-rendered monolith. One FastAPI app (`app/main.py`) mounts thirteen routers; each router renders Jinja2 templates through `app/render.py`; forms post back with CSRF tokens (`app/csrf.py`, `app/forms.py`). There is no JSON API, no OpenAPI (deliberately disabled, `app/main.py:63-66`), no JavaScript framework; `app/static/style.css` is the only client asset.

```
Browser ──HTTPS──▶ Caddy (TLS, Let's Encrypt) ──▶ uvicorn/FastAPI (app.main:app)
                                                    │
                       ┌────────────────────────────┼────────────────────────────┐
                       ▼                            ▼                            ▼
              app/routers/* (13)           app/reconcile.py              app/parsers/*
              auth · account · dashboard   (exception engine)            attendance (.xlsx/.ods)
              billing · uploads · batches  app/matching.py               rate_master (.xlsx)
              reports · checkin · calendar (identity resolution)         pcc_batch (.html/.pdf)
              participants · rate_master   app/payer_categories.py
              admin · admin_import
                       │
                       ▼
              SQLAlchemy 2 ORM (app/models.py) ── EncryptedString (Fernet) + HMAC blind indexes (app/crypto_types.py)
                       │
                       ▼
              SQLite file in data/ (default)  |  DATABASE_URL may point at Postgres (untested here)
              data/uploads/ — encrypted source documents and photos (app/storage.py, app/photos.py)
              data/.encryption_key, .session_secret, .setup_token — generated dev fallbacks (app/config.py)
```

### 1.2 Layers

- **Configuration** (`app/config.py`): environment variables with empty-string-as-unset handling; auto-generated Fernet key, session secret and setup token persisted to `data/` with `0600` permissions when not supplied. The generated-key path is a documented dev fallback that logs a startup warning (`app/main.py:49-54`).
- **Security** (`app/security.py`): signed, HttpOnly, SameSite=Lax cookie sessions via `itsdangerous`; 20-minute idle and 10-hour absolute timeouts; sessions invalidated by `password_changed_at`; forced password change gate; `X-Forwarded-For` last-hop IP; bcrypt via passlib; lockout after 5 failures for 15 minutes. Two roles only: `admin` and `reviewer` (`app/models.py:30-32`). Authorization is a two-tier dependency (`get_current_user`, `require_admin`), not a permission matrix.
- **Middleware** (`app/main.py:84-112`): CSP `default-src 'self'` with inline styles allowed, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, HSTS when cookies are secure, `Cache-Control: no-store` for everything except static assets. Unhandled exceptions render a generic page and log the trace (`app/main.py:126-138`).
- **Schema management** (`app/migrations.py`): `Base.metadata.create_all` for new tables plus hand-written idempotent "touch-ups" (add column, backfill, one SQLite table rebuild with `PRAGMA foreign_keys=OFF`, in-place encryption of previously plaintext columns). No migration tool, no version table.
- **Domain** is not separated from persistence: `app/reconcile.py` and `app/matching.py` take a `Session` and query the ORM directly. Parsers are pure and unit-tested.

### 1.3 Data model (11 tables)

`users` · `participants` (encrypted `full_name`, HMAC `name_index` unique, non-unique `last_name_index`, clear-text `external_id` e.g. `PAM-6`, status active/trial/discharged, encrypted notes, photo path + consent attestation) · `rate_rules` (payer source, float rate, rotating-grant cycle, effective dates, active flag) · `uploads` (kind, stored path, week_start / batch_date, encrypted parse warnings) · `attendance_records` (participant × date unique, attended boolean, source upload|check_in, recorded_by) · `attendance_schedules` (days-of-week CSV, effective dates) · `billing_records` (PCC line: raw name, payer, float amount, units, verified flag) · `reconciliation_runs` · `exceptions` (reason enum ×7, status open/resolved/not_an_error, resolution notes) · `audit_log` (encrypted detail, IP, success).

Properties that matter for the target architecture:

- **Single-tenant.** No `tenant_id` anywhere; `ORGANIZATION_NAME` is a display string.
- **Mutable state.** Attendance marks, participant names, rate rules and exceptions are updated in place. Reconciliation runs are **deleted and recreated** per day (`app/reconcile.py:191-198`); resolution state is carried forward by a `(participant_id, reason)` key. History exists only in the audit log's encrypted free text.
- **Day-granular.** The unit of delivered service is "attended on a date," not a timed activity. Charges are per-day amounts with a units field that is almost always 1.
- **Money as float** (`rate_rules.rate`, `billing_records.amount`), compared with a half-cent tolerance (`app/reconcile.py:162`).
- **Time as naive `datetime.utcnow()`** (16 call sites) with no timezone; business dates are local `date` objects without a declared zone.
- **Domain vocabulary is Parker-Adult-Day specific** and partially hard-coded: `Private Pay` default payer (`app/reconcile.py:33`), PCC charge-code categorisation (`app/payer_categories.py`), Parker Grant / Title III / VA payer names surfaced in templates and parsers.

### 1.4 Deployment

Two-stage Dockerfile (non-root user, `/app/data` volume), `docker-compose.yml` (app + Caddy, healthcheck on `/healthz`, app not exposed to host), `Caddyfile` with a placeholder domain. Backups are a documented `tar` of `/app/data`. README targets a single AWS Lightsail instance under an AWS BAA. No CI configuration exists in the repository for A.

### 1.5 What is architecturally sound in A

The reconciliation problem is modelled honestly: PCC is the billing system of record and A never generates billing; it imports PCC's output and compares it with attendance and payer rules. Identity resolution across three heterogeneous documents is careful (external-ID first, surname family, conservative first-name compatibility, never auto-merge on ambiguity). PHI handling is deliberate and consistent (column encryption, blind indexes, encrypted uploads, EXIF stripping, encrypted audit detail, no PHI in error pages, no OpenAPI). Session and password handling exceeds what small internal tools usually ship.

---

## 2. Architecture of B — HCBS Platform (TypeScript, tarball)

### 2.1 Shape

Layered service with a strict domain/infrastructure boundary (verified: no infrastructure imports under `src/domain/`).

```
                       src/main.ts (Fastify entry; adapters: {} ; rule registry seeded from code)
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
          src/api/server.ts               src/api/auth.ts
          19 routes · Zod .strict() bodies · OpenAPI at /openapi.json
          401/404/400/409/422/500 semantics · request id = audit correlationId
                 │
                 ▼
          src/app/*Service.ts  (time · leave · travel · mileage · payroll)  + common.ts
          authorize(ActorContext) → withTenantTransaction(set_config app.tenant_id, LOCAL)
            → replay domain ledger from rows ordered by seq → validate/append in memory
            → INSERT rows → INSERT audit_event   (one transaction; all-or-nothing)
                 │
      ┌──────────┴────────────────────────────────────────────┐
      ▼                                                       ▼
 src/domain/  (pure TypeScript, Temporal, no I/O)        src/db/  (Drizzle schema 26 tables, 11 migrations,
   identity.ts   authorize(), 24 permissions               stores/*: replay + insert, mappers)
   audit.ts      validateNewAuditEvent, redactForAudit     client.ts: withTenantTransaction, provisionTenant
   rules/        Rule/RuleVersion engine, seeds (10/12)    seedRules.ts, migrate.ts
   time/         UTC instants, business date, DST reject
   workforce/    Employment, WorkActivity, TimeLedger
   travel/       6-question classifier, Rev. Rul. 99-7
   leave/        Leave Ledger, NJ ESL
   mileage/      MileageClaim + events, trip-date pricing
   payroll/      periods, batches, claims, reconcile, adapter contract, fake adapter
   ai/           contracts only
   time.ts leave.ts mileage.ts payrollExport.ts classification.ts   ← legacy Phase-1 modules kept for golden tests
                                                              │
                                                              ▼
                                        PostgreSQL 16 — ENABLE + FORCE RLS on all 26 tables,
                                        tenant_isolation policies on app.current_tenant_id(),
                                        composite (tenant_id, id) FKs, append-only triggers,
                                        roles: hcbs_migrator (owner) · hcbs_app (NOBYPASSRLS, least privilege)
                                               · hcbs_auth_resolver (only BYPASSRLS, NOLOGIN, owns 2 SECURITY DEFINER fns)
```

### 2.2 Invariants actually enforced (preserve exactly)

1. **Tenant isolation, four layers:** credential-derived tenant → `authorize()` tenant match → `SET LOCAL app.tenant_id` + FORCE RLS with `USING`/`WITH CHECK` → composite FKs. Proven by `tests/db/rls-isolation.test.ts` (unset context = zero rows; cross-tenant FK fails; SET LOCAL does not leak across transactions).
2. **Append-only ledgers at the database:** `app.reject_mutation` triggers on audit, rule versions, travel determinations, time and leave ledgers, mileage claims/events, period events, export batches/lines/events; no UPDATE/DELETE grants to `hcbs_app` on those tables. Documented exceptions: `export_source_claim` release-once, `employment` close-only, `api_credential` revoke-only.
3. **Replay by insertion `seq`, never timestamp** (decision #19) — with one gap: `travel_determination` has no `seq` and orders by `determined_at`.
4. **Publication-aware rule selection:** by work date **and** knowledge instant; provenance (rule version id + `asOf`) stored on every derived row; drafts never resolve.
5. **Time via Temporal only** in current modules; DST gap/overlap rejected by default; minutes must equal elapsed instants (domain check and DB CHECK).
6. **Audit in the same transaction as the mutation**, reason required, actor must belong to the event's tenant, AI actors without run provenance are refused and the write rolls back (tested end-to-end).
7. **No gross-to-net, no vendor behaviour assumed:** `PayrollVendorAdapter` with per-capability verification flags; only the fake non-idempotent adapter exists; manual acknowledgement path works without an adapter.
8. **Golden tests never weakened** (20 tests, all against legacy modules, one commit in history).

### 2.3 Where B's architecture is weaker than its documentation says

Detailed with file references in `CURRENT_STATE_AUDIT.md` §B.8. The architecturally significant ones:

- **The Hours → Payroll link is not closed.** `ExportLine.quantity` and `ExportSourceClaim` sources are supplied by the API caller (`src/api/server.ts:118-119`); nothing derives lines from the time or leave ledgers and `entry_id` has no FK. The platform records what it is told rather than producing "payroll-ready calculations." This is the biggest gap between the handoff's Level A claims and the code.
- **A vendor call happens inside the database transaction** (`src/app/payrollService.ts:121-155`). A failure after the wire call rolls back the record of a real submission — the exact failure the design forbids.
- **Compliance facts are caller-asserted at the API edge:** `jurisdiction`, `timezone`, `kind`, and `mileageClassification` come from request bodies rather than from `Employment` and travel determinations. `Employment` and `WorkActivity` tables have no code path that writes them.
- **RBAC is tenant-wide with no relationship scope:** any `time.write` holder can record time for any employee; a mileage submitter can approve and reimburse their own claim.
- **Two rule registries:** an in-memory registry seeded from code and `rule_version` rows joined by text ids. A version in code but absent in the DB fails with 422; `TENANT:<uuid>` policies can never load.
- **Dual legacy/current modules:** `time.ts`/`leave.ts`/`mileage.ts`/`payrollExport.ts`/`classification.ts` remain and are still imported by current modules (`assertValidMiles`, per-diem gate, static policy as the seed). Five of the prior audit's twelve defects remain open precisely inside these files.
- **Money is floating point** throughout the domain; Postgres `numeric` columns are converted with `Number()`.
- **Unbounded replay:** leave replays every entry for an employee × leave type across all years on each write.
- **Error mapping** returns any unmapped error as 400 with its raw message, which for Drizzle includes SQL text.

### 2.4 Operational reality

B has **never run CI, never been pushed, never had a reviewer subagent invoked**, and its README/TEST-STRATEGY/SECURITY-ARCHITECTURE files are partially stale (Phase-1 statements not updated). Rules load from code, `adapters` is empty, there is no way to create a tenant, actor, credential, employee or employment through the API. It is a sound foundation, not yet an operable system.

---

## 3. Target architecture: what must change and what must not

### 3.1 Must not change (both codebases)

- A: keep running as-is in production for the Adult Day Program. No refactor, no re-platforming, no "quick" schema changes. Bug fixes only, with its 194 tests, ruff, mypy and pip-audit green.
- B: the eight invariants in §2.2; the domain/infrastructure import boundary; migrations as append-only files; the API routes and OpenAPI contract (add, never mutate); the golden test file.

### 3.2 Repository and delivery topology (Decision D-1, assumed answer)

```
simensen8/Test  (currently PUBLIC — Decision D-0 asks to make it private or split)
├── app/, tests/, Dockerfile, docker-compose.yml, Caddyfile, README.md   ← A, unchanged
├── hcbs-platform/                                                        ← B imported with full history (subtree)
│   ├── src/ tests/ drizzle/ scripts/ docs/ .claude/
│   └── apps/web/                                                         ← future frontend (per B's FRONTEND-ARCHITECTURE)
├── .github/workflows/
│   ├── hcbs-platform-ci.yml   (B's ci.yml with working-directory + path filters)
│   └── python-app-ci.yml      (new: pytest, ruff, mypy, pip-audit for A — nothing exists today)
├── CURRENT_STATE_AUDIT.md · REQUIREMENTS_GAP_ANALYSIS.md · ARCHITECTURE.md · BUILD_PLAN.md
└── hcbs-platform/PROJECT_HANDOFF.md (arrives with the import; not copied before Decision D-0 on repository visibility)
```

Branch C must **not** be merged: its `hcbs-platform/` is 20 commits behind the tarball and its register edits (OPEN-QUESTIONS #9–#15, RESEARCH-LOG 2026-09-26 entries) conflict in numbering with B's current registers. Its content is preserved by importing `AUDIT-2026-09-26.md` into B's `docs/` and re-numbering its open questions as new rows.

### 3.3 Platform-side changes needed before the care-management chain is built

Ordered by dependency; each becomes a slice in `BUILD_PLAN.md`.

1. **Operability:** tenant/actor/credential bootstrap CLI and credential-management routes; employee, employment and classification services and routes (tables exist, no writers); rate limiting; rules loaded from `rule_version` with a `rule.publish` route; response schemas on every route so the generated client is typed.
2. **Close the Hours → Payroll link:** a `PayrollLineDerivation` service that builds export lines from live time-ledger and leave-ledger entries for a period, with FK-checked source claims; move the vendor call out of the transaction (record `submit_attempted`, commit, call, record outcome).
3. **Move compliance facts server-side:** jurisdiction/timezone from `Employment`; `kind` as a DB CHECK; mileage classification only from a travel determination; `reopened` periods block export.
4. **Retire the legacy modules by documented invariant change:** re-target golden tests 1, 2, 3, 4, 5, 6, 9 at the ledgers and rules engine, then delete `time.ts`, `leave.ts`, `mileage.ts`, `payrollExport.ts` and give `ClassificationRecord` a table with an explicit `unresolved` state and the missing NJ per-diem criterion. This is the only path that closes prior-audit defects D4-legacy, D5, D6, D9 and D11.
5. **Relationship-scoped authorization (ABAC):** supervisor→employee, care-team→client, self-approval prohibition. Prerequisite for anything client-facing.
6. **Money and replay hygiene:** integer minor units (or a decimal type) for amounts and rates; benefit-year-bounded leave replay or periodic snapshots; `seq` on `travel_determination`; single clock per transaction; `redactForAudit` actually applied.
7. **Docs truth pass:** rewrite README, TEST-STRATEGY, SECURITY-ARCHITECTURE; delete `db-schema-phase1.sql`; fix decision #17 wording vs. migration 0007; reconcile the two retention constants.

### 3.4 The care-management chain: how A's domain maps onto B

The user's operating model, with the entity in B that should carry it and the A concept that seeds it:

| Chain element | B today | A today | Target in B |
|---|---|---|---|
| Client | nullable `client_id` uuid, no table | `participants` (encrypted name, external id, status, photo/consent) | `client` table, PHI columns encrypted at application layer (A's pattern), blind indexes for lookup, `external_identifier` table keyed by system (PCC `PAM-n` is the first) |
| Program | — | implicit (one Adult Day program) | `program` under tenant; service definitions with charge codes (A's `ADC MPNT`, `ADC P GRANT`, …); EVV determination per service definition |
| Authorization / funding | — | `rate_rules`: payer source, rate, rotating-payer cycle, effective dates | `funding_source`/`payer`, `authorization` (effective-dated, units), rotation as a **rule version** not a column pair; rate as effective-dated `rate_schedule` |
| Care Plan → Required Work | — | `attendance_schedules` (expected weekdays) | `care_plan` → `required_work` (expected attendance pattern is the Adult Day instance) |
| Service / Activity / Visit | `work_activity` (delivery dimension, table only) | `attendance_records` (attended/absent per day, source upload or check-in) | `work_activity` subtype `attendance_day` with delivery status, plus the Documentation and Validation dimensions; A's check-in screen becomes the first UI over it |
| Documentation | typed, not modelled | — (paper sign-in sheet remains the signed record) | Documentation dimension; the paper sheet is an external document reference, not an attachment |
| Hours | `time_ledger_entry` (staff time) | — (participant days, not hours) | Entitlement ledger consumes **units** (days here, hours elsewhere); staff hours remain the Time Ledger |
| Billing | — | `billing_records` imported from PCC, `exceptions` from comparison | Revenue ledger `eligible → charged → (external) invoiced`; a **reconciliation** subsystem comparing platform-expected charges with PCC actuals (A's engine, generalised); PCC batch import as an `integration_event` |
| Revenue | — | — | Revenue ledger posting/adjustment/write-off entries |

Employee chain: `employee → role assignment (RBAC role; a job role/title is a separate `position` concept not yet modelled) → program assignment (new) → employment (exists) → capacity profile with productivity target (new, effective-dated) → time ledger (exists) → work activity (exists, no writer) → productivity snapshot (pure function exists; persist as a versioned calculation with provenance)`.

Two conceptual corrections the target model must make explicit:

- **"Role" means two things.** B's `Role` is an authorization role. The user's chain "Employee → Role → Program" reads as job role/position. Model `position` separately; do not overload RBAC roles with HR meaning.
- **Units are not always hours.** A bills days; the handoff's Entitlement Ledger speaks in hours. The entitlement/authorization model must carry a unit of measure per service definition, or Adult Day cannot be represented without distortion.

### 3.5 Frontend

B has no UI; A has a complete server-rendered UI for its workflow, including phone layouts. The decided platform frontend (React 19 + Vite SPA, TanStack, shadcn/ui on Base UI, Tailwind v4, Playwright + axe) does not yet exist. Under the assumed strategy A's UI is preserved as is, and B's first screens follow B's rollout order (Today → Time → Travel → Leave → Mileage → Payroll → Compliance → Admin). Adult Day screens (check-in, roster, billing week, exception report) are re-implemented in the platform UI only when the platform module that replaces A is built, using A's templates as the behavioural specification.

### 3.6 Human authentication

A: platform-native passwords with bcrypt, lockout, forced change (rejected by B's decision #25 for the platform because of MFA/offboarding burden). B: API keys only; OIDC → BFF cookie session decided in principle, IdP undecided (OQ #19). Until the IdP is chosen, the platform UI can only be used with API credentials in development. This is on the critical path for any platform UI reaching staff.

### 3.7 Deployment

A has a concrete, verified single-VM Docker deployment. B has none decided (hosting, secrets manager, TLS, observability all NOT DECIDED). The Postgres roles bootstrap (`scripts/db-bootstrap.sql`) requires superuser, which rules out some managed offerings without a workaround; this must be checked against the chosen host before the first platform deployment.
