# BUILD_PLAN.md — Sequenced Implementation Plan

*2026-09-26. Produced after the takeover audit (`CURRENT_STATE_AUDIT.md`, `REQUIREMENTS_GAP_ANALYSIS.md`, `ARCHITECTURE.md`). Nothing in this plan has started. Phase 0 and every later phase wait for the owner's approval of the decisions in §1.*

---

## 0. Principles

1. **Two codebases, one operating model.** The TypeScript platform is the base. The Python Adult Day app keeps running unchanged until a platform module reaches parity and its data is migrated. Its domain logic is the specification for that module.
2. **Vertical slices that add a link to the chain.** Every phase must connect at least two adjacent elements of `Client → Program → Authorization → Care Plan → Service → Activity → Documentation → Hours → Billing → Revenue` or `Employee → Position → Program → Employment → Capacity → Hours → Activity → Productivity`, end to end (schema → service → API → test, and UI where a UI exists). No isolated features.
3. **Preserve invariants.** FORCE RLS, composite FKs, append-only triggers, grants, `seq` replay, publication-aware rules, provenance on every derived row, domain/infrastructure boundary, strict API bodies, the golden tests (until D-3 is approved), and the Python app's encryption, session and identity-resolution behaviour.
4. **Gate every slice**: typecheck · lint · unit · DB tests · build · `drizzle-kit check` · secrets scan; reviewer subagents invoked for real (compliance, security, adversarial, architecture, ui-ux as relevant); registers updated in the same commit.
5. **Never guess product or compliance decisions.** Items marked `UNCERTAIN`/`NOT DISCUSSED` in the handoff become questions in §1, not code.

---

## 1. Decisions needed from the owner

| # | Decision | Options | Recommendation | Blocks |
|---|---|---|---|---|
| **D-0 (urgent)** | The repository is reported public and contains participant-style full names with internal `PAM-n` IDs in `README.md:201` and in `tests/test_participants.py`, `tests/test_same_surname_reconciliation.py`, `tests/test_roles.py` (five distinct people), described as mirroring real files, plus a real staff name in `README.md:4`. Are these real people? | (a) Make the repository private now, then replace fixture names with synthetic ones and rewrite history; (b) confirm the names are synthetic and only make private; (c) leave public | (a) — treat as a potential PHI disclosure until proven otherwise | Everything; do not import the platform (which adds Parker brand and business detail) into a public repo |
| **D-1** | Codebase strategy | (a) Platform is the base; Python app preserved as-is until superseded; its domain ported as a platform module with data migration; (b) evolve the Python app into the target product; (c) keep both indefinitely as separate products | (a). (b) is a rewrite of 6.7k lines of tested multi-tenant infrastructure in another language and contradicts platform decisions #1, #7–#12; (c) leaves the client chain permanently outside the system of record | Phase 0 onward |
| **D-2** | Where the platform lives | (a) This repo, `hcbs-platform/`, after it is private; (b) a new private repo, this one stays for the Python app | (a) if D-0 → private; otherwise (b) | Phase 0 |
| **D-3** | Retire legacy domain modules by re-targeting golden tests 1–6 and 9 at the ledgers and rules engine (documented invariant change per platform `CLAUDE.md` rule 2). Closes prior-audit D4-legacy, D5, D6 (NJ per-diem "as-needed/substitute" criterion), D9, D11 | approve / defer | Approve, in Phase 3 | Phase 3 |
| **D-4** | Unit of measure for authorization/entitlement | (a) per service definition (hours, days, visits, units); (b) hours only | (a) — Adult Day bills days | Phase 6 design |
| **D-5** | Identity provider for staff sign-in (OQ #19) | Entra ID / Auth0 or Clerk / platform passwords | Entra ID (org runs Microsoft 365; MFA and offboarding from IT) | Phase 5 (staff use of any platform UI) |
| **D-6** | SmartLinx mechanism, T&A vs Payroll, employee ids, pay-code map (OQ #15–#18) | vendor answers | Send the checklist in `docs/SMARTLINX-INTEGRATION.md` now | Phase 9 adapter only |
| **D-7** | Cut-over policy for the Adult Day module | parity + parallel run (recommended) / big-bang | Parity, then ≥ 1 full billing cycle parallel run, then cut-over | Phase 7 |
| **D-8** | OQ #4 GPS notice default, #9 business date, #10 audit retention, #11 NJFLA | per register | Keep documented defaults; confirm before first live payroll period | Phase 2 go-live |
| **D-9** | Platform deployment target, secrets manager, observability | AWS (the Python app already targets AWS under a BAA) / Azure (if Entra) / other | Decide alongside D-5; verify the superuser bootstrap works on the chosen managed Postgres | Phase 4 deployment |
| **D-10** | Apply the four Python-app bug fixes now (merge-with-schedule crash, CSP-blocked confirmations, re-upload deleting resolutions, 422 echoing form input) | now / with Phase 0 / never | With Phase 0, as a separate small commit with tests; they are correctness and PHI fixes, not features | Track M |

---

## 2. Sequence overview

```
Track M  (maintenance, Python app)  ── M1 four bug fixes ── M2 CI ── (frozen except security fixes) ──▶ retired after Phase 7 parallel run

Phase 0  Repository, safety, truth            (no product surface)
Phase 1  Workforce operability slice          Employee → Employment → Time → Hours, operable end-to-end     ◀── FIRST VERTICAL SLICE
Phase 2  Hours → Payroll closed               derive export lines from ledgers; two-phase submit; periods
Phase 3  Rules as data + legacy retirement    rule.publish, DB-loaded registry, golden re-target (D-3)
Phase 4  Frontend F1                          apps/web shell, Today, Time (dev auth = API keys)
Phase 5  Staff sign-in + ABAC + F2 screens    OIDC/BFF (D-5), relationship scope, Travel/Leave/Mileage/Payroll/Compliance/Admin
Phase 6  Care-management foundation           Program, Client, identifiers, Payer, Authorization (unit-aware), Care Plan
Phase 7  Adult Day module + migration         attendance activity, check-in UI, PCC import, reconciliation, entitlement in days, parallel run, cut-over
Phase 8  Documentation/Validation/Revenue     remaining dimensions, Revenue ledger, EVV determination, scheduling foundation
Phase 9  External adapters + AI runtime       SmartLinx (D-6), Align (OQ #1), AI runs with provenance
```

Phases 1–3 are platform-internal and can proceed while D-5/D-6/D-9 remain open. Phase 6 needs D-4. Phase 7 needs D-7.

---

## Track M — Python app maintenance (parallel, small)

**Objective.** Keep the running tool correct and safe; make no functional or architectural change.
**Value.** Prevents a merge crash on a real roster, silent destructive actions, loss of reviewer work, and a PHI echo.
**Data model.** None.
**Backend.** M1: reassign `AttendanceSchedule` rows and remove the source photo file in `merge_participant`; replace inline `onsubmit` confirmations with a CSP-compatible pattern (a small `static/confirm.js` bound by `data-confirm` attributes); carry resolutions across `clear_batch_for_date` the way `run_reconciliation` does, or refuse re-upload of a reconciled day without admin; override `RequestValidationError` to render the HTML error page without echoing input. M2: add `.github/workflows/python-app-ci.yml` (pytest, ruff, mypy, pip-audit) with path filters.
**Frontend.** Templates only as needed for M1.
**Integrations.** None.
**Permissions.** Consider moving batch re-upload of a reconciled day to admin (needs owner OK; otherwise unchanged).
**Testing.** One regression test per fix; existing 201 cases green.
**Acceptance.** All four defects reproduced by a failing test first, then green; CI runs on push.
**Regression risks.** Confirmation UX change is visible to staff; keep wording identical.
**Dependencies.** D-10; network access to pypi for verification.

---

## Phase 0 — Repository, safety, truth (no product surface)

**Objective.** One repository that holds both codebases with full history, runs CI for both, and whose documentation matches its code.
**Value.** Removes the "all platform work lives in a tarball" risk; makes every later claim verifiable.
**Data model.** None.
**Backend/API.** None. Steps: (1) act on D-0; (2) import the tarball into `hcbs-platform/` with `git subtree add` preserving 22 commits (or push to a new private repo per D-2); (3) move `hcbs-platform/.github/workflows/ci.yml` to `.github/workflows/hcbs-platform-ci.yml` with `working-directory` and path filters; add `GITLEAKS_LICENSE` if the org requires it; (4) import `AUDIT-2026-09-26.md` from branch C into `hcbs-platform/docs/`, append its research corrections to `RESEARCH-LOG.md` (IR-2026-29 source; Gusto optimistic concurrency; NJ EVV list; N.J.S.A. 34:6B-22 text) and its open questions as new numbered rows; do **not** merge branch C; (5) docs truth pass: rewrite platform `README.md`, `TEST-STRATEGY.md`, `SECURITY-ARCHITECTURE.md`; delete `db-schema-phase1.sql`; correct decision #17 wording; reconcile the two retention constants; record the handoff-vs-code corrections from `REQUIREMENTS_GAP_ANALYSIS.md` §4 in `PROJECT-STATE.md`; (6) adjust `.claude/agents/*` paths for the subdirectory; (7) run the full gate in CI and record the first real green run; (8) invoke each of the five reviewer agents once on the imported tree and record findings — the handoff asks for this baseline.
**Frontend.** None.
**Integrations.** None.
**Permissions.** None.
**Testing.** CI green for both codebases.
**Acceptance.** `git log hcbs-platform/` shows the 22 imported commits; both CI workflows green; no doc statement contradicted by code in the audit list.
**Regression risks.** Subtree import path collisions with branch C's stale `hcbs-platform/` if anyone merges C — do not.
**Dependencies.** D-0, D-1, D-2.

---

## Phase 1 — Workforce operability slice (recommended first vertical slice)

**Objective.** A real tenant with a real employee can be created, given employment terms, have time recorded against a work activity, and read hours — with jurisdiction and timezone taken from Employment, not the request.
**User/business value.** Turns the platform from a tested library into an operable system for the first internal user; closes the "compliance facts are caller-asserted" gap that every later slice would otherwise inherit.
**Data model.** `classification_record` table (explicit `status: complete|unresolved|under_review`, reviewer, dates, documentation ref; FK from `employment.classification_record_id`); `position` (job role/title, effective-dated) — optional here, required before Phase 5 ABAC; DB CHECK on `time_ledger_entry.kind`; `seq` on `travel_determination`; index review.
**Backend/API.** Bootstrap CLI (`hcbs bootstrap-tenant`) creating tenant, first human actor, admin role, credential — replaces raw SQL. Credential routes: create (hash returned once), revoke, list. `EmployeeService`, `EmploymentService` (new record + close prior, no overlap), `ClassificationService` (completeness gate from legacy module moved to a current module; contradictory records refused; `unresolved` explicit). Routes: `POST/GET /v1/employees`, `POST /v1/employees/:id/employments`, `POST /v1/employees/:id/classification`, `GET /v1/employees/:id`. `WorkActivityService` with `POST /v1/work-activities` (delivery dimension) and `POST /v1/work-activities/:id/deliver` which records the time entry — the first Activity → Hours link written by the server. Time/travel/leave/mileage services resolve `jurisdiction` and `timezone` from `employmentAsOf(businessDate)`; request fields become optional-then-removed (additive OpenAPI change). Response schemas on every route; shared Zod contracts extracted to `src/contracts/`. `@fastify/rate-limit` (decision entry). Error mapping: unmapped pg codes → 500 with opaque body; 40001 → 503/retry hint. Move the vendor call out of the payroll transaction now (two-phase) even though no adapter exists — see Phase 2 for the derivation.
**Frontend.** None (Phase 4).
**Integrations.** None.
**Permissions.** Use `employee.read/write`, `actor.manage`, `tenant.admin`; new `credential.manage` permission (additive to the 24).
**Testing.** DB tests: employment overlap rejected at DB and domain; classification `unresolved` blocks nothing but is visible; time entry with Employment-derived jurisdiction; RLS on every new table (extend `rls-isolation.test.ts`); HTTP e2e: bootstrap → create employee → employment → activity → deliver → hours summary; credential expiry and revocation over HTTP (currently untested); rate-limit 429. Golden tests untouched.
**Acceptance criteria.** A non-developer can, with the CLI and API only, onboard a tenant and an employee and see correct hours-worked for a week that includes client-to-client travel; a request that supplies a jurisdiction different from Employment is rejected; every route appears in `/openapi.json` with typed responses; all 146 existing tests plus new ones green; security-reviewer and adversarial-reviewer findings addressed.
**Regression risks.** Changing services to read Employment could break the 10 existing e2e tests that supply jurisdiction directly — keep the fields accepted but validated for one phase; the `.strict()` bodies mean new optional fields are additive only.
**Dependencies.** Phase 0. No owner decision needed.

---

## Phase 2 — Hours → Payroll closed

**Objective.** Export lines are derived by the platform from live time-ledger and leave-ledger entries for a period; nothing is exportable that the ledgers do not contain.
**Value.** Makes the handoff's "payroll-ready calculations" true; eliminates the largest wage-liability gap (caller-asserted quantities).
**Data model.** `export_source_claim` gains a checked reference (composite FK to `time_ledger_entry`/`leave_ledger_entry` via a polymorphic check or two nullable FKs with a CHECK exactly-one); `export_line` splits `quantity` into `hours numeric` and `amount_minor integer` (additive columns, `kind` decides); `payroll_period` gains `business_date_convention` provenance; `vendor_pay_code_map` and `employee_external_id` tables (effective-dated) as designed in `SMARTLINX-INTEGRATION.md`.
**Backend/API.** `PayrollLineDerivationService`: for a soft-closed/closed period, select live entries by business date, group by employee × `PayInputKind` via the pay-code map, produce lines and claims in one transaction; `POST /v1/payroll/periods/:id/derive-batch` replaces client-supplied lines (old route kept, marked deprecated, then removed in a later major). Block export for `reopened`. Two-phase submit (record attempt, commit, call adapter, record outcome, reconcile on `unknown`). Populate `supersededByBatchId`. Persisted cross-claim mileage duplicate check. Integer minor units for money in domain (`Money` type) with conversion at the boundary; rates as scaled integers or decimal strings.
**Frontend.** None.
**Integrations.** Fake adapter only; manual acknowledgement path retained.
**Permissions.** `payroll.export`, `period.close`; derivation requires `payroll.export`; a separate `payroll.review` (additive) for read-only.
**Testing.** Unit: derivation over a period with reversals, leave vs hours-worked separation, DST week, midnight-crossing period under the documented business-date convention. DB: FK rejects a claim for a non-existent entry; concurrent derive for the same period yields one batch. E2e: derive → submit (fake, `unknown` outcome) → resubmit reconciles without resend → accept manually → reverse → supersede. Golden Test 6 still green via the facade.
**Acceptance.** No route accepts caller quantities for a live period; a reversed time entry disappears from the next derived batch and appears as a negative adjustment line in a superseding batch; export of a `reopened` period is a 409; adapter exceptions never roll back the `submit_attempted` event.
**Regression risks.** Money type migration touches mileage pricing tests; keep old float paths until parity tests pass. OpenAPI additive only.
**Dependencies.** Phase 1 (Employment-derived facts). D-8 confirmations before first live period, not before code.

---

## Phase 3 — Rules as data and legacy retirement

**Objective.** The API resolves rules from `rule_version` rows (including `TENANT:<uuid>` policies) with a `rule.publish` workflow; legacy Phase-1 modules are removed.
**Value.** Multi-jurisdiction and tenant-specific policy without deploys; closes five open prior-audit defects; removes the dual-registry failure mode.
**Data model.** `rule_version` status transitions as events (`rule_version_event`, append-only); no schema change to `rule_version` itself.
**Backend/API.** `PostgresRuleRegistry` implementing `RuleRegistry` (cached per tenant, invalidated on publish); `RuleService.publish` (approver ≠ author, requires `rule.publish`, audit); routes `GET /v1/rules`, `GET /v1/rules/:id/versions`, `POST /v1/rules/:id/versions` (draft), `POST /v1/rule-versions/:id/publish`. Seeds become a migration-time loader only. Legacy retirement (D-3): re-target golden tests 1, 2, 3, 4, 5, 6, 9 at `workforce/core.ts`, `leave/ledger.ts`, `mileage/claim.ts`, `payroll/export.ts`, the new `classification` module; add the NJ per-diem "as-needed/substitute" criterion as a required attestation field and a positive/negative case; fix `deriveAvailableHours` per-day floor; delete `time.ts`, `leave.ts`, `mileage.ts`, `payrollExport.ts`; move `assertValidMiles`, per-diem gate and `computeProductivity` into current modules. Decision entry documenting the invariant change with before/after test names.
**Frontend.** None.
**Integrations.** None.
**Permissions.** `rule.publish` (exists); `rule.read` additive.
**Testing.** Parity test: every seeded rule resolves identically from code and DB for a grid of work dates and knowledge instants; publish/approve separation; tenant policy visible only to its tenant (RLS on `rule_version` already exists); re-targeted golden tests assert the same numbers as before (28.42, 32/24/91.7%, 72.5¢/76¢, 60/61, 120/121, 1h/30h, 40h caps, CHHA excluded, duplicate claim rejected, incomplete classification refused).
**Acceptance.** `src/main.ts` no longer imports `seeds.ts`; `grep -r "new Date" src/domain` is empty; prior-audit table shows D4–D11 closed; golden count ≥ 20 with identical assertions.
**Regression risks.** This is the one phase that changes golden test wiring; requires D-3 and a reviewer pass by compliance-reviewer with the statutory text for the per-diem criterion.
**Dependencies.** D-3. Phase 1 (classification table).

---

## Phase 4 — Frontend F1 (shell, Today, Time)

**Objective.** `hcbs-platform/apps/web` exists with the design system, shell (sidebar/rail, top bar with tenant, ⌘K), Today screen (unresolved items) and the Time flow, usable against API credentials in development.
**Value.** First screens on the decided stack; establishes visual/a11y QA before more screens accrue.
**Data model.** None. Backend: `GET /v1/today` aggregating unresolved items (travel `requiresReview`, unresolved classifications, batches not reconciled, periods awaiting close) — decision-driving KPIs only; list endpoints with URL-held filters for time entries (new, additive).
**Frontend.** Vite + React 19, TanStack Router/Query/Table v9, shadcn/ui on Base UI, Tailwind v4 with tokens from `.claude/skills/frontend-design/tokens.reference.css`, react-hook-form + contracts from `src/contracts/`, openapi-fetch client generated from `/openapi.json` (works only because Phase 1 added response schemas), `/dev/components` route, Playwright + axe at three viewports. Time flow completable one-handed on a phone (bottom tab bar, sticky primary action, autosave drafts). Provenance popover on hours-worked numbers.
**Integrations.** None.
**Permissions.** Navigation hidden (not disabled) by permission from the credential's resolved set — needs `GET /v1/me`.
**Testing.** Playwright screenshots at 375/768/1280; axe critical/serious = 0; ARIA snapshots for shell and dialogs; contract test that the generated client compiles against `/openapi.json`.
**Acceptance.** A developer with an API key can record a time entry from a phone-sized viewport in ≤ 3 taps after opening the app; every derived number shows rule version and knowledge instant; ui-ux-reviewer and security-reviewer invoked.
**Regression risks.** None to the API; adds a second package to CI.
**Dependencies.** Phase 1. Not blocked by D-5 (dev auth only).

---

## Phase 5 — Staff sign-in, relationship-scoped authorization, remaining workforce screens

**Objective.** Staff sign in through the organisation's IdP; authorization is scoped by relationship; Travel, Leave, Mileage, Payroll (periods/exports/reconciliation), Compliance (rules, classifications, audit) and Admin screens exist.
**Value.** The platform becomes usable by field staff, supervisors and payroll without API keys; removes self-approval and cross-employee write paths.
**Data model.** `session` table (server-side, revocable; hashed id; tenant; actor; expiry), `supervisor_assignment` (effective-dated employee → supervisor), `program_assignment` (employee → program; Program entity arrives in Phase 6, so this can be deferred to 6 if preferred).
**Backend/API.** OIDC authorization-code flow with PKCE in Fastify (BFF), httpOnly Secure SameSite=Lax cookie, CSRF double-submit, session → `ActorContext` identical to credentials; `authorize()` gains a relationship predicate layer (self, supervisee, program membership); approval routes reject self-approval; `GET /v1/audit-events` (filters, no snapshots without `audit.read`), `POST /v1/travel-segments/:id/redetermine` exposed; list endpoints for each ledger. Pay attention to the Python app's proven session rules (absolute + idle, invalidation on credential change) as the behavioural spec.
**Frontend.** Screens in the documented order; payroll flows keyboard-first; export screens render `ExportDescription` and events exactly as returned, including manual acknowledgement; "Reverse with reason" never "undo".
**Integrations.** Entra ID (or chosen IdP) — configuration only.
**Permissions.** New `*.approve` enforcement paths; relationship predicates; navigation filtered.
**Testing.** DB: relationship predicates under RLS; e2e: supervisor approves supervisee's mileage, self-approval 403, cross-employee time write 403; session expiry, revocation on IdP sign-out; Playwright + axe for each screen.
**Acceptance.** A supervisor can approve travel review items only for their supervisees; a payroll clerk can close a period and derive a batch from a keyboard; all audit events show correlation ids in the UI.
**Regression risks.** Adding ABAC tightens existing permissive paths — the Phase 1 e2e tests need relationship fixtures.
**Dependencies.** D-5 (IdP), Phase 4. D-9 for the first deployed environment.

---

## Phase 6 — Care-management foundation

**Objective.** Program, Client (PHI-minimised), external identifiers, Payer/Funding Source, unit-aware Authorization and Care Plan → Required Work exist as tenant-scoped, effective-dated entities with the Delivery dimension attached to `work_activity`.
**Value.** First half of the client chain; designed against a real running business (Adult Day) rather than an abstract spec; prerequisite for entitlement, billing and revenue.
**Data model.** `program` (tenant, name, unit of measure default, timezone), `service_definition` (program, code, unit of measure, EVV determination `required|not_required|pending_determination`, external codes e.g. PCC `ADC MPNT`), `client` (encrypted name columns at application layer using the Python app's Fernet + HMAC blind-index pattern ported to TS, status effective-dated, PHI columns flagged for `redactForAudit`), `client_external_identifier` (system, value; PCC `PAM-n` first), `client_program_enrollment` (effective-dated), `payer`/`funding_source`, `authorization` (client, service definition, payer, effective window, authorised units, unit of measure, rate schedule ref), `rate_schedule` (effective-dated per payer × service), `payer_rotation_policy` as a **rule type** (primary days, secondary days, secondary payer — the Python app's rotation generalised), `care_plan` → `required_work` (expected pattern; weekdays for Adult Day). `work_activity` gains `service_definition_id`, `program_id`, composite FKs; Documentation/Validation/Financial dimension columns typed with `not_required|…` defaults but only Delivery written here.
**Backend/API.** Services and routes for each entity; `client.read/write` gating PHI columns; list views return initials/ids only (PHI minimisation); `authorizationAsOf(client, service, date)`; Delivery transitions on work activities with the invariant "scheduled is never automatically delivered".
**Frontend.** Client list (PHI-minimised), client profile (details behind `client.write`/`client.read` full), program and authorization admin screens; provenance affordance on expected payer.
**Integrations.** None yet (Phase 7 adds PCC import).
**Permissions.** `client.read`, `client.write` (exist), new `program.manage`, `authorization.write`, `care_plan.write`; ABAC via care-team/program membership.
**Testing.** RLS on every table; PHI never in audit snapshots (test that `redactForAudit` runs on client events); blind-index lookup parity with the Python app for the same normalised names; rotation rule reproduces the Python test cases (`test_reconcile.py` rotation scenarios) as golden-style tests; Entitlement never blocks delivery (an exhausted authorization yields an exception record, delivery still transitions).
**Acceptance.** A client enrolled in a program with a rotating-payer authorization and a Mon/Wed/Fri required-work pattern shows the expected payer for any date, with rule provenance; the Python app's `test_participants.py` rotation and supersession scenarios pass as platform tests.
**Regression risks.** None to workforce ledgers; `work_activity` column additions are additive.
**Dependencies.** D-4 (unit of measure), Phase 5 (ABAC), Phase 3 (rules as data for the rotation policy).

---

## Phase 7 — Adult Day module in the platform, data migration, cut-over

**Objective.** The platform performs everything the Python app does today for the Adult Day Program — check-in, roster, calendars, PCC batch import and review, reconciliation, exception resolution, billing week, dashboard — and the Python app's data is migrated. Then a parallel run and cut-over per D-7.
**Value.** Retires the second codebase; puts participant attendance and billing reconciliation inside the multi-tenant system of record with append-only history.
**Data model.** `attendance_day` as a `work_activity` subtype (delivery `delivered|missed|not_scheduled`, source `check_in|imported_sheet`, recorded-by); `entitlement_ledger_entry` (consumption in units — days here — with authorization and rule provenance; adjustments/reversals); `external_document` (uploaded sheet/batch, encrypted at rest, provenance); `billing_import_line` (PCC actual charge: raw name, charge code, description, payer code, amount minor units, units, service date, verified-by); `reconciliation_finding` (append-only; reason, expected, actual, status via events rather than in-place updates); `integration_event` (PCC import as an inbound integration with idempotency on file hash + date).
**Backend/API.** Port `matching.py` (external id → surname family → first-name compatibility, warnings, never auto-merge), `payer_categories.py` (as tenant configuration rows, not code), the three parsers (pure, unit-tested with the same fixture shapes, UTF-8-first decoding fixed), `reconcile.py` as a `ReconciliationService` producing findings from expected (authorization + rotation rule + delivered activity) vs actual (import lines) — with carry-over of resolutions as events and **no deletion of prior runs**. Batch replace-a-day semantics preserved but as supersession, not deletion. Migration tool: read the SQLite file (decrypting with the app's Fernet key), map participants → clients + identifiers, attendance → attendance_day activities, rate rules → authorizations + rotation policies, billing records → import lines, exceptions → findings with resolution events, audit log → audit events (source `migrated`), users → actors; dry-run report; idempotent.
**Frontend.** Check-in (one-click present/out/clear, drop-ins, mark-rest-present with confirmation), program and client calendars, roster with same-surname disambiguation, batch upload (multi-file, dated from file, collision refusal) and review, exception report with resolution and CSV export (formula-injection safe), billing week with single next action, dashboard KPIs. Phone-first for check-in.
**Integrations.** PointClickCare by file export (HTML first, PDF fallback) recorded as `integration_event`; no PCC API assumed (UNVERIFIED).
**Permissions.** Program-scoped ABAC; batch re-upload of a reconciled day requires a reason and is an event; participant merge admin-only with confirmation.
**Testing.** Port every Python behaviour test whose scenario is stack-independent (matching, parsers, reconcile, rate check, rotation, same-surname, multi-file upload, replace, billing week state machine, check-in concurrency, roles); migration dry-run against an anonymised copy; parallel-run comparison job: both systems' exception sets per day must match for ≥ 1 billing cycle before cut-over.
**Acceptance.** For every day in the parallel run, the platform's findings equal the Python app's (or every difference is explained by a documented improvement); staff complete check-in in the platform UI on a phone; the Python app is switched to read-only and then retired; its encrypted data directory is archived under the retention policy.
**Regression risks.** Highest of the plan: changes the tool staff use daily. Mitigate with parallel run and feature-flagged cut-over per program.
**Dependencies.** D-7, Phase 6, Phase 5 (staff sign-in on the platform UI), Phase 3 (rotation policy as rule).

---

## Phase 8 — Documentation, Validation, Revenue, EVV determination, scheduling foundation

**Objective.** The remaining status dimensions and the Revenue ledger exist; EVV requirement is determined per service definition; scheduling produces `scheduled` activities that never auto-deliver.
**Value.** Completes Activity → Documentation → Validation → Billing → Revenue for delivered work; positions for MCO/Medicaid service lines.
**Data model.** `documentation_record` (state machine Required → Draft → Submitted → Returned → Final; external document refs), `validation_event` (Auto-Validated | Clinical Review → Approved | Returned | Held), `revenue_ledger_entry` (Eligible → Credited → Classified → Invoiced → Posted | Written Off; adjustments as entries), `schedule`/`shift` (planned activities with delivery `scheduled`), `evv_record` placeholder with `pending_determination`.
**Backend/API.** Dimension transitions as services with the four-dimension invariant enforced in domain (tested: none cascades automatically); revenue eligibility from validated activities + authorization; charge computation from rate schedule by service date with provenance; invoice generation left as export until an accounting target is decided.
**Frontend.** Documentation queue, validation queue, revenue exceptions.
**Integrations.** None assumed (EVV vendor UNVERIFIED; accounting NOT DISCUSSED).
**Permissions.** `documentation.write/review` (exist), `revenue.read/write` (new).
**Testing.** Dimension independence property tests; revenue replay reproduces historical charges under retroactive rate publication via `repriced` adjustments.
**Acceptance.** A delivered, documented, validated attendance day yields a revenue entry with rule provenance; an undocumented one yields an exception, never revenue.
**Regression risks.** Adds columns/transitions to `work_activity`; additive.
**Dependencies.** Phase 7; decisions on documentation templates and assessments (handoff Level C — do not invent; ask).

---

## Phase 9 — External adapters and AI runtime

**Objective.** SmartLinx adapter (renderer first, pure and unit-tested against the confirmed schema; manual acknowledgement stays), Align interface with `pending` status + manual import, AI run provenance plumbing so AI actors can propose through services with an `AiRun` and a human approval — no autonomous action.
**Value.** Closes the payroll loop with the real vendor; begins assistive AI without touching money.
**Data model.** `ai_run`, `ai_output`, `ai_proposed_action`, `ai_approval`, `ai_committed_action`, `ai_escalation` per `contracts.ts`; `integration`/`integration_event` generalised from Phase 7.
**Backend/API.** `SmartlinxPayrollAdapter` behind the verified capability flags; Align adapter interface; AI runtime service that records runs and routes proposed actions to approval; `AI_ALWAYS_REQUIRES_HUMAN_APPROVAL` enforced.
**Frontend.** Approval queue for AI proposals; export acknowledgement screens per vendor answers.
**Integrations.** SmartLinx per D-6 answers; Align per OQ #1.
**Permissions.** `ai.configure` (exists); approval permissions per action type.
**Testing.** Contract tests against recorded vendor responses; AI proposal without approval never commits; kill switch mid-run.
**Acceptance.** A batch derived in Phase 2 is submitted to SmartLinx with a machine or clerk acknowledgement recorded; an AI actor's proposal is visible, approvable and fully provenanced.
**Regression risks.** Low to core; adapters are behind interfaces.
**Dependencies.** D-6, OQ #1; Phases 2, 5.

---

## 3. What each phase adds to the operating model

```
Phase 1: Employee ─ Employment ─ Activity ─ Hours                                (server-derived facts)
Phase 2:                                     Hours ─ Payroll export               (derived, two-phase)
Phase 3: Rules as data under every derived number
Phase 4/5: UI + staff identity + relationship scope over all of the above
Phase 6: Client ─ Program ─ Authorization(unit-aware) ─ Care Plan ─ Service ─ Activity(delivery)
Phase 7:                                                    Activity(attendance day) ─ Entitlement(days) ─ Billing import ─ Reconciliation
Phase 8:                                     Activity ─ Documentation ─ Validation ─ Revenue ; Schedule ─ Activity
Phase 9: Payroll export ─ SmartLinx ; AI proposals ─ approvals ─ committed actions
```

## 4. Out of scope until the owner decides

Assessments, goals, interventions, task management, referral/intake steps, care-team composition, documentation templates, scheduling matching/availability, invoices/payments, EHR/accounting/telephony integrations, dark mode, any AI autonomy, gross-to-net or any payroll computation, schema-per-tenant, weakening RLS/triggers/grants, the SmartLinx adapter before vendor confirmation.
