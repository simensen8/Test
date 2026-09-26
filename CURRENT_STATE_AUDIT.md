# CURRENT_STATE_AUDIT.md — What Actually Exists (2026-09-26)

*Audit performed on takeover, before any code change. Sources of truth: the repository at `simensen8/Test` (branch `claude/care-app-audit-reconciliation-7av730`, HEAD `2e1a076`, identical to `claude/hipaa-billing-reconciliation-qty4bc`), the handoff tarball `hcbs-platform-handoff.tar.gz` (HEAD `bf04d5e`), and the remote branch `claude/hcbs-platform-audit-dvrvd0`. Every module under `app/`, `tests/`, and `hcbs-platform/src|tests|drizzle|scripts|docs` was read in full. File references are `path:line`.*

---

## 0. Method and verification limits

| Check | Result |
|---|---|
| Python app tests, ruff, mypy, pip-audit | **Could not run.** `pip install` is denied by the organization's egress policy (`pypi.org` → 403 from the proxy). Prior session (branch `dvrvd0`, same date) recorded **201/201 pass, ruff clean, mypy clean, pip-audit clean** with `python -m pytest`. Static count here: 194 `test_` functions, 201 parametrised cases. |
| Platform typecheck, lint, unit tests, build, DB tests | **Could not run.** `npm ci` is denied (`registry.npmjs.org` → 403). Static verification below. A local PostgreSQL 16 cluster was started for the DB tests but is unusable without `node_modules`. |
| Static verification of the handoff's claims | Done (see §B.1). |
| Reviewer subagents defined in the platform (`.claude/agents/*`) | Not invoked (their instructions assume the platform is at the repository root; they have never been invoked by anyone, per `docs/TOOLING-INVENTORY.md`). |

To make test execution possible in future sessions, widen the environment's network access to include `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`.

---

## 1. The three artifacts

| | A. Python billing app | B. TypeScript platform | C. Prior audit branch |
|---|---|---|---|
| Location | repo root | tarball only, no remote | `origin/claude/hcbs-platform-audit-dvrvd0` |
| Commits | 17 (2026-09-22 → 09-24) | 22 (Phase 1A–2 + frontend gate docs) | A + 5 |
| Size | ~11.9k lines (31 modules, 20 templates, 20 test modules) | ~6.7k lines TS, 823 lines docs | imports only B's first 2 commits (7 domain files + golden tests) |
| Runs today | Yes (Docker + Caddy, verified by its author) | API starts, but no tenant/actor/credential can be created without SQL; adapters empty | No |
| UI | Complete server-rendered UI incl. phone layouts | None | None |
| Multi-tenant | No | Yes (FORCE RLS) | — |

**The handoff describes B. The repository contains A.** Neither references the other in code. Branch C is stale by 20 platform commits and must not be merged as-is (see §C).

---

## Part A — Billing Reconciliation Assistant (Python, repository root)

### A.1 Purpose and workflow

Automates Parker Adult Day Program's daily/weekly review of PointClickCare (PCC) ancillary billing batches against attendance and payer rules, surfacing only discrepancies. Workflow: upload PCC batch HTML (multi-file, dated from the file) → verify parsed rows → reconcile the day → resolve exceptions → export CSV. Attendance comes from an admin-imported Weekly Attendance workbook or the in-app check-in screen. Rate Master workbook import seeds per-participant payer rules.

### A.2 Important files

| File | Role |
|---|---|
| `app/main.py` | App factory, 13 routers, security-headers middleware, error pages, `create_all` + migrations at import |
| `app/models.py` | 11 SQLAlchemy models (see A.3) |
| `app/crypto_types.py` | `EncryptedString` (Fernet) column type, `blind_index()` HMAC-SHA256 keyed with the Fernet key bytes |
| `app/security.py` | Cookie sessions, timeouts, lockout, `get_current_user`, `require_admin`, `log_audit` |
| `app/csrf.py`, `app/forms.py`, `app/flash.py` | CSRF token (HMAC of uid:issued_at), form helpers, Fernet-sealed 30 s flash cookies |
| `app/config.py` | Env config; auto-generated Fernet key / session secret / setup token persisted 0600 under `data/` |
| `app/migrations.py` | Hand-rolled idempotent schema touch-ups (SQLite-specific DDL, one table rebuild) |
| `app/reconcile.py` | Exception engine (7 reasons), expected-billing and rotation math, resolution carry-over |
| `app/matching.py` | Participant identity resolution: external id → surname family → first-name compatibility → fuzzy |
| `app/payer_categories.py` | 8 hard-coded canonical payers; PCC charge-code/description/payer-code categorisation |
| `app/parsers/attendance.py`, `rate_master.py`, `pcc_batch.py` | Workbook and HTML/PDF parsers built against real sample files |
| `app/routers/*` (13) | auth, account, dashboard, billing, uploads, batches, reports, checkin, calendar, participants (743 lines), rate_master, admin, admin_import |
| `app/photos.py`, `app/storage.py` | EXIF-stripping re-encode, encrypted file storage under UUID names |
| `tests/*` (20 files) | Behaviour tests through `TestClient` against a temp SQLite DB |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.env.example`, `README.md` | Deployment |

### A.3 Data model (11 tables, SQLite default)

- `users` — email (unique), bcrypt hash, role `admin|reviewer`, lockout fields, `must_change_password`, `password_changed_at`.
- `participants` — encrypted `full_name`; `name_index` (unique blind index; surname slot or composite), `last_name_index` (non-unique blind index), clear-text `external_id` (`PAM-n`, unique), status `active|trial|discharged`, encrypted notes, photo path + consent attestation (who/when).
- `rate_rules` — payer source (encrypted), `rate` **Float**, `grant_rule_type none|rotating_grant`, cycle length / secondary days / secondary payer, effective_start/end, `active`, `source_upload_id`.
- `uploads` — kind `weekly_attendance|pcc_batch|rate_master`, stored path, `week_start` or `batch_date`, encrypted parse warnings.
- `attendance_records` — `(participant_id, date)` unique; `attended` boolean; source `upload|check_in`; recorded_by/at.
- `attendance_schedules` — weekday CSV, effective dates, active.
- `billing_records` — PCC line: encrypted raw name, date, encrypted payer, `amount` **Float**, `units` free text, `verified`, `source_upload_id` (NOT NULL).
- `reconciliation_runs`, `exceptions` — run per date; exception reason ×7, status `open|resolved|not_an_error`, encrypted snapshots and notes.
- `audit_log` — action (50 distinct), resource (plaintext), IP, encrypted detail, success.

Properties: UUID string PKs; naive `datetime.utcnow()` everywhere (16 sites); no `updated_at`, no history tables; no `tenant_id`/`organization_id`/`program_id`.

**Mutable where history matters (no prior version retained):** attendance marks overwritten and hard-deleted on undo (`app/routers/checkin.py:153-166, 209-215`); rate rules edited in place (`app/routers/participants.py:531-532`; `app/routers/rate_master.py:96-97`) with audit recording only the id; exceptions overwritten on resolve and **all prior runs/exceptions for a date deleted on re-run** (`app/reconcile.py:191-198`); billing rows edited in place and deleted on re-upload (`app/routers/uploads.py:39-68`); participant merge hard-deletes the source (`app/routers/participants.py:368`).

### A.4 Routes and roles (summary)

Unauthenticated: `/`, `/healthz`, `/setup`, `/login`, `/static/*`. Reviewer or admin: dashboard, billing week, upload PCC batches, batch review/save/add-row, reconcile, report, resolve, CSV export, check-in, calendars, roster read/create/edit/schedule/photo, rate-master read. Admin only: `/admin/users/*`, `/admin/audit-log`, `/admin/import/*`, participant merge, all rate-rule writes, billing-row delete. Authorization is per-route `Depends`; a route that omits it is public (not deny-by-default architecturally).

### A.5 Business logic

- **Reconciliation** (`app/reconcile.py:172-349`): attended-but-unbilled → `MISSING_FROM_BATCH`; >1 line → `DUPLICATE_ENTRY`; payer mismatch → `GRANT_RULE_NOT_APPLIED` if a rotation rule is in force else `WRONG_PAYER`; rate × units ≠ amount (tolerance 0.005) → `WRONG_RATE` (deliberately silent when no rate, no amount, rotation day, or unreadable units); billed with no attendance record → `UNMATCHED_NAME` (semantic overload); billed but absent → `BILLED_BUT_ABSENT`; unmatched name → `UNMATCHED_NAME`. Prior non-open resolutions are carried forward by `(participant or name, reason)`.
- **Expected payer** (`:43-96`): newest active rule covering the date (ordering by `created_at`); rotation position from a count of **all** attended days ≤ date with no lower bound — correctness depends on complete continuous attendance history in this DB (documented in README).
- **Matching** (`app/matching.py`): external id wins; then surname family via `last_name_index`; first-name compatibility (blank, equal, prefix/suffix ≥ 3, `fuzz.ratio ≥ 85`); never auto-merge ambiguous same-surname people; fuzzy lookup never creates. A full-table decrypt-and-scan fallback exists (`:242-250`).
- **Parsers**: attendance (xlsx/ods, header-row heuristics, `Trials:` sentinel, whitelist marks `X P YES Y 1`); rate master (sheet scoring by "new/current" vs "old/legacy", "Discount" column as exception text, rotation phrase parsing, `ID_KEYS` defined but unused); PCC batch (HTML table first, windows-1252-first decoding, batch date from "Services for M/D/YYYY" else modal row date; PDF fallback).
- **Payer categories**: `Private Pay`, `Parker Grant`, `Alzheimer Grant`, `Title III`, `VA`, `Hospice`, … hard-coded; charge code/description preferred over PCC payer code because `PP-ADC` conflates Parker Grant and Private Pay.

### A.6 Security posture (what is true in code)

Strong for its size: Fernet column encryption on every PHI text field including audit detail, parse warnings and flash cookies; encrypted uploads and photos (EXIF stripped, pixel-cap before decode, consent attestation); bcrypt; lockout; idle 20 min / absolute 10 h sessions; `password_changed_at` invalidation; forced temp-password change; setup token with race-safe first-admin; timing-equalised login; last-admin protection; CSRF on state-changing forms; strict CSP; HSTS; `no-store`; no OpenAPI; generic error pages; ORM-only SQL; last-hop `X-Forwarded-For`.

Gaps found (none are showstoppers for the current single-site deployment; all matter before scaling scope):

1. **Inline `onsubmit="return confirm(...)"` handlers are blocked by the CSP** `script-src 'self'` (`app/main.py:101-104` vs `app/templates/participant_profile.html:27,155,259`, `check_in.html:29`, `rate_master.html:21`). Merge, remove-rule and mark-rest-present proceed **without confirmation**.
2. **Merge crashes when the source participant has an `AttendanceSchedule`** — schedules are never reassigned before `db.delete(source)` (`app/routers/participants.py:321-368`; FK enforced by `PRAGMA foreign_keys=ON`). Untested path. Source photo file is orphaned.
3. **Re-uploading a batch deletes that day's exceptions including resolution notes** (`app/routers/uploads.py:39-68`); any reviewer can do it.
4. FastAPI's default `RequestValidationError` handler is not overridden: a form POST missing a required field returns JSON 422 echoing the submitted `input` (possibly a participant name). Contradicts README "No PHI in error responses". Parser tracebacks logged via `logger.exception` can include workbook cell contents (`uploads.py:150`, `admin_import.py:160,250`).
5. Stateless signed sessions: logout cannot revoke a stolen cookie before idle timeout; login form has no CSRF token.
6. Blind-index HMAC key = Fernet key bytes (`app/crypto_types.py:25`); no `MultiFernet`, no rotation path; rotating the key invalidates every blind index.
7. Startup only warns when the encryption key is auto-generated; nothing refuses to run in production without an explicit key.
8. Audit log "append-only" is convention only (no trigger, no hash chain); `resource` and failed-login email attempts are plaintext; screen capped at 500 rows, no export.
9. CSV export has no formula-injection guard (`app/routers/reports.py:120-129`).
10. Rename updates `last_name_index` but not `name_index` (`participants.py:277-283`), so the legacy fallback lookup can resolve the old surname to the renamed person.
11. `ValueError` → 500 on enum coercion of form input (`app/routers/admin.py:51`, `reports.py:95`).
12. Reviewers can rename/re-ID participants, clear attendance marks and re-upload batches — arguably admin-grade.

### A.7 Tests

19 test modules, 194 functions / 201 cases; fixtures create a fresh SQLite DB per test and a logged-in admin client. Coverage is behaviour-oriented and good on: multi-file upload and replace semantics, billing-week state machine with query-count ceilings, dashboard, calendar, check-in (incl. concurrency), logout, matching and same-surname handling, migrations, parsers (xlsx, HTML), photos, participants (merge, rules, rotation, schedules), password lifecycle, payer categories, rate check, reconciliation, roles, security hardening (headers, cookies, setup token, sealed flash).

Untested: lockout after N failures; idle timeout; `.ods` parsing; PDF table path; user create/reactivate and audit-log routes; `resolve` and `export.csv` routes end-to-end; roster/rates import happy paths through the routes; rate-master edit/deactivate; merge with a schedule (bug above); rename leaving `name_index` stale; CSP vs inline handlers; anything on Postgres.

### A.8 Configuration and deployment

Env: `APP_DATA_DIR`, `DATABASE_URL` (SQLite default), `APP_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`, `APP_SETUP_TOKEN`, timeouts, lockout, `COOKIE_SECURE`, `ORGANIZATION_NAME`. Two-stage Dockerfile (non-root, `/app/data` volume), compose with Caddy TLS, healthcheck on `/healthz`. No CI in the repository. Backups are a documented manual `tar`.

**Postgres readiness is nominal only**: no driver pinned; migrations emit SQLite `DATETIME` DDL and a SQLite-only table rebuild; native enum types would not be extendable by these migrations; `create_all` + migrations run at import in every worker process.

### A.9 Strengths (preserve)

Encryption and blind-index design; session/password lifecycle; setup token; identity resolution with explicit ambiguity warnings; replace-a-day upload semantics; resolution carry-over; bulk `day_states` with query-count regression tests; concurrency-safe check-in; photo pipeline; a test suite that drives real routes; unusually good rationale comments; script-free responsive UI; honest README about limits (rotation history, nickname limits, HIPAA operational obligations).

### A.10 Technical debt (beyond A.6)

Hand-rolled unversioned import-time migrations; float money; naive UTC timestamps and container-local `date.today()` for "today" (a NJ evening after 8 pm EDT lands on the next date); N+1 in calendar and rate-master, full-table Python sorting of decrypted names, per-page fuzzy re-matching on the import screen; missing indexes (`rate_rules.participant_id`, `billing_records.participant_id`, `uploads(kind,batch_date)`, `exceptions(run_id,status)`); free-text payer at review never normalised; windows-1252-first decoding will corrupt UTF-8 exports; rotation ordinal ignores `effective_start`; rate-master add creates undated, non-superseding rules; hard-coded Parker/Adult-Day vocabulary and Mon–Fri assumptions; 743-line router; duplicated rule form in three templates; dead code (`ID_KEYS`).

### A.11 README vs code

- README says `/upload` accepts Weekly Attendance and Rate Master workbooks; only PCC batches are accepted there — workbooks are admin-only at `/admin/import`.
- README says trial participants are excluded from `MISSING_FROM_BATCH`; true only for rows under a `Trials:` marker in the workbook. `ParticipantStatus.TRIAL` is never read by `reconcile.py`.
- README says the audit log is append-only and error responses carry no PHI — both are partially true (see A.6).
- README's deploy instructions clone branch `claude/hipaa-billing-reconciliation-qty4bc`; there is no `main`. The remote's HEAD branch is that feature branch.

### A.12 Data-integrity and disclosure risk specific to this repository

The prior audit (branch C) states the GitHub repository is **public**. `README.md:201` and the test fixtures (`tests/test_participants.py:50,69,217,236`, `tests/test_same_surname_reconciliation.py:84-90`, `tests/test_roles.py`) use participant-style full names with internal `PAM-n` IDs (five distinct people across those lines; not repeated here), and the README states these formats mirror **real sample files** and that real rosters contained two Goldsteins. Whether these specific names are real participants cannot be determined from the code. If they are, a public repository holding an adult-day-program participant name plus internal ID is a PHI disclosure. This needs the owner's immediate determination (`BUILD_PLAN.md` Decision D-0). The README also names a staff member.

---

## Part B — HCBS Workforce & Care Management Platform (TypeScript, tarball)

### B.1 Handoff claims verified against code

| Claim (`PROJECT_HANDOFF.md` §3) | Verified |
|---|---|
| 26 Drizzle tables | **Yes** — `pgTable(` ×26 in `src/db/schema/*.ts` |
| Migrations 0000–0010 | **Yes** — 11 files + `drizzle/meta/_journal.json` |
| 19 API routes | **Yes** — `src/api/server.ts:79-124` |
| 146 tests = 20 golden + 97 unit + 29 DB (10 HTTP e2e) | **Yes by static count** (unit 14+13+12+11+26+21; DB 10+3+12+4). Not executed here. |
| 5 application services | **Yes** — `src/app/{time,leave,travel,mileage,payroll}Service.ts` |
| "20 commits at 516ab14" | Now 22 (two docs commits after) |
| "typecheck, lint, build, drizzle check, audit, gitleaks all clean" | Not re-verified (registry blocked); CI has **never run** (no remote) |

### B.2 Architecture

Layered: `src/api` (Fastify + Zod strict bodies + OpenAPI) → `src/app` services (authorize → `withTenantTransaction` → replay domain from rows by `seq` → append → persist → audit in one transaction) → `src/domain` (pure TS, Temporal, no I/O — verified) and `src/db` (Drizzle schema, migrations, stores). Entry `src/main.ts` seeds the in-memory rule registry from `src/domain/rules/seeds.ts` and passes an empty `adapters` map.

### B.3 Data model (26 tables, all FORCE RLS, composite `(tenant_id, id)` FKs)

`tenant` · `actor` (human|ai_agent|system; AI requires owner, DB CHECK) · `app_user` (1:1 actor) · `employee` (optional actor) · `ai_agent` (manifest, kill switch) · `role` · `role_permission` · `role_assignment` · `api_credential` (sha256 hash, revoke-only) · `audit_event` · `rule` · `rule_version` · `employment` (close-only) · `work_activity` · `travel_segment` · `travel_determination` (append) · `time_ledger_entry` · `leave_ledger_entry` · `mileage_claim` · `mileage_claim_event` · `payroll_period` · `payroll_period_event` · `export_batch` · `export_line` · `export_source_claim` (release-once) · `export_event`.

Roles: `hcbs_migrator` (owner), `hcbs_app` (NOLOGIN→LOGIN by bootstrap, NOBYPASSRLS, SELECT/INSERT only on ledgers), `hcbs_auth_resolver` (only BYPASSRLS, NOLOGIN, owns exactly `resolve_api_credential` and `resolve_ai_agent`). Append-only triggers via `app.reject_mutation`. CHECKs: minutes = elapsed, ESL purpose/basis, reversal shape, single reversal, one live claim per source (partial unique), one acceptance per batch.

**No code path writes** `employment`, `work_activity`, `app_user`, `ai_agent`, `employee`, `tenant` (except `provisionTenant` in tests), `role*`, `api_credential` — those tables exist but only tests and SQL populate them.

### B.4 Domain modules

Current: `time/timezone.ts` (branded `UtcInstant`/`BusinessDate`, DST reject-by-default, business date = period start); `rules/engine.ts` (publication-aware selection, latest published wins, drafts never resolve, recalculation candidates; in-memory registry only; 10 rules / 12 versions seeded); `travel/segment.ts` (13 travel classes, Rev. Rul. 99-7 with explicit `undetermined`, six independent answers with provenance; mileage rules hard-wired to `US-FEDERAL`); `workforce/core.ts` (effective-dated Employment, WorkActivity delivery dimension, TimeLedger with duplicate-live-source and single-reversal guards); `leave/ledger.ts` (8 entry types, separate ESL accrual/usage/carryover caps, protected purposes from rule, usage eligibility, 6-year purge guard); `mileage/claim.ts` (immutable claim + events, trip-date pricing, accountable-plan timeliness, transition table); `payroll/export.ts` + `service.ts` (periods, idempotency key over tenant/vendor/period/sorted sources, lost-ack handling, reconciliation outcomes, vendor adapter contract, fake adapter); `identity.ts` (24 permissions, `authorize()` with tenant match, role grant, AI kill switch, manifest ∩, observe-only reads); `audit.ts` (validation, redaction — `redactForAudit` never called outside tests); `ai/contracts.ts` (types only).

Legacy (Phase-1 bootstrap, kept for golden tests and **still imported by current modules**): `time.ts` (static policy = seed for the NJ time-segment rule), `leave.ts` (per-diem gate, `assertNonNegativeHours`, `computeProductivity`), `mileage.ts` (`assertValidMiles`, string-date comparison, constants), `payrollExport.ts` (facade with `LEGACY_TENANT`), `classification.ts` (interface only; `employment.classification_record_id` is free text, no table).

### B.5 API, services, auth

Routes: health, openapi, time-entries (create, reverse, hours), leave-entries, esl-accruals, leave-balance, travel-segments, mileage-claims (create, events), payroll periods (create, transition), batches (create, submit, accept, reverse, reconcile, get). Only two routes declare response schemas (one is `z.any()`).

Auth: `hcbs_` + 32 random bytes; SHA-256 stored; resolution via SECURITY DEFINER functions; tenant only from the credential; `.strict()` bodies make a client `tenantId` a 400; cross-tenant reads 404. No rate limiting, no OIDC, no CORS/helmet, no credential lifecycle, no tenant/actor provisioning route.

Error mapping (`src/app/common.ts:37-50`): forbidden 403; rule_unresolved 422; validation 400; pg 23505 → 409; 23503/23514/23000 → 422; 42501 → 403; **anything else → 400 with the raw message** (Drizzle messages include SQL text).

### B.6 Tests

Golden ×20 (all against legacy modules): client↔client travel counts / home legs don't; productivity 32/24/91.7% and N/A never 0%; mileage June 30 @72.5¢ / July 1 @76¢ / late-submitted June trip; accountable plan 60/61 and 120/121 days; ESL 1h/30h, 40h cap, healthcare alone ≠ exempt, CHHA excluded; duplicate export claim rejected / re-export after reversal; audit shape; cross-tenant denied; incomplete duties analysis refused.

Unit: rules engine, timezone DST both directions, travel taxonomy and six-answer independence, ledgers, payroll idempotency/lost-ack/supersession/reconciliation, identity/audit/AI narrowing. DB: RLS deny-by-default, WITH CHECK, SET LOCAL scope, composite FK, AI owner/provenance, audit immutability for owner, migration idempotence, concurrent claim race, release-once, 10 HTTP end-to-end.

Gaps: no HTTP test of credential expiry or AI-agent auth; `redetermine` has no route or test; `api.test.ts:104` correlation assertion is tautological; `api.test.ts:164` accepts `[201, 409]` for a duplicate mileage claim; no test that a `reopened` period blocks export (it doesn't); referenced `tests/db/auditStore.contract.test.ts` does not exist.

### B.7 CI and tooling

`.github/workflows/ci.yml`: quality job, PostgreSQL 16 job (bootstrap SQL, migrate, `drizzle-kit check`, seed, DB tests), gitleaks job. Never executed. `.claude/agents/` defines five read-only reviewer subagents pointing at `docs/skills/*`; `.claude/settings.json` allows the npm scripts and denies reading `.env*`. `npm run secrets:scan` uses local gitleaks.

### B.8 Technical debt and risks (file references)

1. **Vendor call inside the DB transaction** — `src/app/payrollService.ts:121-155`: `submit_attempted` insert and `adapter.submit()` share one transaction; a failure after the wire call rolls back the record of a real send.
2. **Payroll lines are caller-asserted** — `src/api/server.ts:118-119`: `quantity` and `sources` come from the request; nothing derives lines from the time/leave ledgers; `export_source_claim.entry_id` has no FK; tests use random UUIDs as sources. The handoff's "payroll-ready calculations" are not yet computed by the platform.
3. **`reopened` periods can be exported** — `payrollService.ts:79` blocks only `open`; handoff §6 says soft-closed or closed required.
4. **Error mapping leaks/misclassifies** — unmapped pg codes (22P02, 40001 serialization, 57014) surface as 400 "validation" with SQL text.
5. **Compliance facts caller-asserted** — `jurisdiction`, `period.timezone`, `kind` (`z.string()`, no DB CHECK), `mileageClassification` (`server.ts:109`) come from bodies; `Employment` is never consulted; a `mileage.write` holder can self-classify miles as business.
6. **Tenant-wide RBAC only** — any `time.write` actor records/reverses time for any employee; mileage submitter can approve and reimburse own claim (`mileageService.ts:33`, dead ternary); `time.approve` unused.
7. **Two rule registries** — in-memory seeds vs `rule_version` rows joined by text ids (`workforce.ts:133,170`); tenant `TENANT:<uuid>` policies cannot load.
8. `travel_determination` has no `seq`; `latestDetermination` orders by `determined_at desc` (`postgresTravelStore.ts:47`) — the same-millisecond collision decision #19 exists to prevent.
9. **Unbounded leave replay** — every entry for employee × leave type across all years per write (`postgresLeaveLedgerStore.ts:51-55`), O(n²) in `append`.
10. `describeFrom(..., undefined)` never computes `supersededByBatchId` (`payrollService.ts:109,134,209`).
11. Response schemas absent → generated client will be untyped.
12. `assertPermission` still ignores `Role.tenantId` (`identity.ts:159`); safe only because `auth.ts:44` stamps roles with the credential's tenant.
13. Any holder of `hcbs_app` DB credentials can INSERT tenants (`0001:123`).
14. Two clocks per transaction: audit `occurredAt` = DB `now()`, ledgers = injected `Clock`. `requestContext` never populated.
15. Seed drift: `esl_accrual.recordRetentionYears: 5` vs `record_retention.years: 6` (`seeds.ts:163,215`).
16. **Money is floating point** throughout domain (`Math.round(x*100)/100`); Postgres `numeric` converted with `Number()`; `export_line.quantity` holds hours and dollars in one column disambiguated by `kind`.
17. Decision #17 says the app role has no privilege on `api_credential`; migration `0007:25` grants SELECT/INSERT/UPDATE.
18. `Date` is used in legacy domain modules despite decision #10.

### B.9 Prior audit defects D1–D12 (branch C) — status in current tree

| # | Status | Evidence |
|---|---|---|
| D1 authz ignores role tenant | Partial | `identity.ts:159`; actor `active` checked only at credential resolution; `assertAiActorHasOwner` never called in auth path |
| D2 shallow audit freeze / empty reason | Partial | reason + correlation fixed (`audit.ts:73-76,110`); nested snapshot still mutable (`:147`); DB trigger makes Postgres path immune |
| D3 tenant-less claim key | **Fixed** | `payroll/export.ts:131-134,250` |
| D4 string-date mileage / constants / float | Fixed in `mileage/claim.ts`; **open in legacy `mileage.ts:61-75,95`**; money still float |
| D5 classification contradictions / no unresolved state | **Open** | `classification.ts:34-45` unchanged |
| D6 NJ per-diem "as-needed/substitute" criterion | **Open** | `leave.ts:87-96`; `ledger.ts:246` delegates to the same gate |
| D7 single 40h cap | Mostly fixed | separate caps `seeds.ts:29-32`, `ledger.ts:148-165`; frontload payout-or-carry not modeled; legacy fn keeps single cap |
| D8 minutes/derived travel/taxonomy | Fixed in current path | `core.ts:205`, `workforce.ts:150`, `segment.ts:164-171`, `seeds.ts:47-60`; legacy `time.ts` retained for GT1 |
| D9 available-hours floor | **Open** | `leave.ts:28-33` |
| D10 RLS without FORCE / single-column FKs | **Fixed** | all 26 tables; `docs/db-schema-phase1.sql` stale |
| D11 golden test tolerance / zone-less Dates | **Open (frozen by golden rule)** | `golden.test.ts:19-21,66` |
| D12 tooling | Mostly fixed | typed eslint, gitleaks, audit; no formatter |

Every open item lives in a legacy module pinned by golden tests — the cost of decision #15.

### B.10 Documentation drift inside the platform

`README.md` ("136 tests", "no API, no database connection"); `docs/TEST-STRATEGY.md` (Phase-1 era); `docs/SECURITY-ARCHITECTURE.md:5-22` contradicts its own appended §"Implemented as of Phase 2"; `docs/COMPLIANCE-ASSUMPTIONS.md` #11 says Rev. Rul. 99-7 not implemented (it is); `docs/db-schema-phase1.sql` obsolete; `PROJECT_HANDOFF.md` §6 "export requires soft-closed or closed" (code allows `reopened`); §4 "platform-owned … payroll-ready calculations" (lines are caller-asserted); §10 "`Date` not used in domain code" (legacy modules use it).

### B.11 Strengths (preserve exactly)

FORCE RLS + composite FKs + `SET LOCAL` + deny-by-default `authorize()` as four independent layers; `hcbs_auth_resolver` pattern; append-only triggers that block even the owner (tested); `export_source_claim_open_uq` under real concurrency (tested); publication-aware rules with `CalculationProvenance` on every derived row; six-answer travel determination with explicit `undetermined`; Temporal-only time with DST rejection; strict Zod bodies; `seq`-ordered replay; AI-without-provenance rollback test; the honesty of the registers (UNVERIFIED / COUNSEL_REVIEW confidence, "never invoked" tooling inventory).

---

## Part C — Prior audit branch `claude/hcbs-platform-audit-dvrvd0`

Five commits on top of A: subtree import of B's first two commits into `hcbs-platform/`; CI relocated to `.github/workflows/hcbs-platform-ci.yml` with path filters; `.gitignore` widened; `hcbs-platform/docs/AUDIT-2026-09-26.md` (defects D1–D12, research corrections); `PROJECT-STATE.md`/`OPEN-QUESTIONS.md` edits proposing a "Phase 1 hardening" roadmap that the tarball has since largely executed.

Value to carry forward: the D1–D12 table (now mostly resolved, five open); research corrections — IRS H2-2026 mileage source is IR-2026-29 (not "Announcement 2026-11"); Gusto uses version-based optimistic concurrency, not idempotency keys (downgrade from VERIFIED); N.J.S.A. 34:6B-22 "tracking device" definition text; NJ DMAHS EVV service list and HHAeXchange as state vendor; its open questions #9 (repo public / platform private), #10 (Python app relationship), #11 (D6 golden-test change), #12–#15.

Conflicts if merged: its `hcbs-platform/` tree is 20 commits behind; its `OPEN-QUESTIONS.md` numbering (#9–#15) collides with the tarball's (#9 business date, #10 retention, …). **Recommendation: do not merge C; import its audit document into the platform's `docs/` and re-number its questions as new rows.**

---

## Part D — Cross-cutting risks

1. **Repository is public with participant-style fixtures and a real staff name** (A.12). Highest urgency; owner decision.
2. **No shared remote for the platform**: all Phase 1–2 work exists only in the tarball until pushed.
3. **No verification possible in this environment** (registries blocked) — any change made here ships untested unless network access is widened.
4. **Two operating models for the same tenant**: A is single-tenant, mutable, day-granular; B is multi-tenant, append-only, minute-granular. Any "evolve A into B" path is a rewrite in disguise; any "absorb A into B" path requires a client/authorization/entitlement model B does not have yet.
5. **Handoff Level-A claims that are actually Level B**: payroll-ready calculations (lines caller-asserted), employee/employment management (tables only), rules at runtime (code-seeded), compliance facts from employment (caller-asserted).
6. **Golden tests pin legacy code with known defects** (D5, D6, D9, D11, D4-legacy); retiring them requires a documented invariant change the handoff forbids without owner approval.
7. **Deployment for the platform is undecided** (host, secrets, TLS, observability) while A has a working single-VM deployment; the platform's superuser bootstrap may not fit managed Postgres offerings.
