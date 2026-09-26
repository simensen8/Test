# REQUIREMENTS_GAP_ANALYSIS.md — Handoff Requirements vs. Actual Implementation

*2026-09-26. Requirements are taken from `PROJECT_HANDOFF.md` (§2 vision, §4 requirements by fidelity level, §5 domain model, §6 business rules, §7 UX, §8 AI, §9 integrations, §10 architecture, §15 priorities) and from the owner's takeover brief (the two operating chains). Implementation evidence is in `CURRENT_STATE_AUDIT.md`.*

## 0. How to read this

**Classification** (per capability): **Implemented** · **Partially implemented** · **Not implemented** · **Architecturally blocked** (cannot be built without a structural change first) · **Unclear / requires decision**.

**Two codebases.** "Platform" = the TypeScript codebase in the handoff tarball (B). "Python app" = the repository's Adult Day billing app (A). A capability is marked against the platform unless stated, because the platform is the codebase the handoff describes and the recommended base (`BUILD_PLAN.md` D-1). Where the Python app already delivers a capability the platform lacks, the row says so — that is reuse potential, not platform credit.

**Fidelity levels from the handoff:** Level A = claimed implemented and tested; Level B = specified, not implemented; Level C = named only.

---

## 1. Capability map

### 1.1 Workforce, hours, travel, mileage, leave, payroll (handoff Level A/B)

| # | Requirement | Handoff level | Actual | Classification | Notes / evidence |
|---|---|---|---|---|---|
| W1 | Actual recorded segments are the source of pay; 11 segment kinds with policy flags from an effective-dated rule | A | Time Ledger with `time_segment_policy` rule; minutes = elapsed (domain + DB CHECK); one live entry per source | **Implemented** | `kind` is `z.string()` at the API and has no DB CHECK — invalid kinds fail only at rule lookup. |
| W2 | Travel first-class: origin/destination, period+tz, miles, vehicle, workday context, source; six independent answers; 10 travel classes | A | `classifyTravelSegment` returns compensable / hours worked / reimbursable / rate / taxable / rule versions; 13 classes; `undetermined` when employer facts missing | **Implemented** | Mileage rules hard-wired to `US-FEDERAL`; `redetermine` exists in service but has no route. |
| W3 | Mileage claims: immutable facts + events; priced by trip date; accountable-plan timing; negative miles rejected; business purpose required | A | Yes, current module; cross-claim duplicate check only in-memory within a request | **Implemented** (with gap) | `mileageClassification` is **caller-supplied** at the API with no link to a travel determination — a `mileage.write` holder can self-declare excludable business miles. |
| W4 | Leave Ledger: 8 entry types; derived balances; NJ ESL (1:30, three 40h caps, 120-day usage wait, protected purposes, attested per-diem exemption, CHHA never exempt); 6-year retention guard | A | Yes; caps enforced separately; purge guard | **Partially implemented** | Per-diem gate lacks the statutory "as-needed / substitute for absent employee" criterion (prior audit D6, still open). Frontload year-end payout-or-carry not modelled. Replay unbounded across years. |
| W5 | FLSA classification never inferred; complete record required; incomplete = explicit unresolved | A (record) / B (workflow) | `ClassificationRecord` is an interface only; no table; `employment.classification_record_id` is free text; contradictory records pass (D5) | **Partially implemented** | Completeness gate exists in legacy `classification.ts`; no persistence, no review workflow, no explicit `unresolved` state. |
| W6 | Employment effective-dated; Rev. Rul. 99-7 employer facts set by HR | A | Domain type + table + close-only trigger; **no service or route writes it**; never consulted by time/travel services | **Partially implemented** | API callers supply jurisdiction/timezone directly; employment facts are unused at runtime. |
| W7 | Employee Capacity / Compensation profiles | B | Only `computeProductivity()` (legacy `leave.ts`) | **Not implemented** | Golden Test 2 covers the arithmetic. |
| W8 | Productivity: available = scheduled − leave; expected = available × target; never false 0% | A | Pure function, golden-tested | **Partially implemented** | Not persisted, no inputs from ledgers, `deriveAvailableHours` floors the aggregate not per day (D9). |
| W9 | Payroll export: platform-owned dedup; full what/when/vendor/tenant/employee/period/sources/status/retries/reversal; retry-safe; reversal as event; superseding batch; reconciliation; manual acknowledgement | A | Idempotency key, event stream, claims with DB partial unique index, lost-ack handling, supersession, reconciliation, manual accept | **Partially implemented** | **Lines and sources are caller-asserted** (`server.ts:118-119`), `entry_id` has no FK, nothing derives lines from ledgers. Vendor call runs inside the DB transaction. `supersededByBatchId` never populated. |
| W10 | Payroll periods open → soft_closed → closed → reopened; export requires soft-closed or closed | A | Transitions implemented; export blocked only for `open` | **Partially implemented** | `reopened` periods export (contradicts handoff §6). |
| W11 | SmartLinx adapter | Decided vendor, blocked | Not built; fake adapter only; `adapters: {}` | **Architecturally blocked** (external) | Blocked on vendor confirmation OQ #15/#16; manual acceptance path works. |
| W12 | Employee / Employment / Classification CRUD services and routes | Named next | None | **Not implemented** | Tables exist. Prerequisite for any real user of the platform. |

### 1.2 Clients, care management, intake (handoff Level B/C)

| # | Requirement | Handoff level | Actual (platform) | Actual (Python app) | Classification | Notes |
|---|---|---|---|---|---|---|
| C1 | Client entity with PHI minimization | B | None (nullable `client_id` uuid, no table) | `participants` with encrypted name, blind indexes, external id, status, notes, photo/consent | **Not implemented** (platform) | Python app is a working reference for PHI-minimised client identity. |
| C2 | Engagement → Service Plan (Template → Agreement → Period → Entitlement buckets) | B | None | None (payer rule per participant approximates one bucket) | **Not implemented** | Entitlement unit of measure must be decided (days vs hours) before design — see §3. |
| C3 | Care Plan → Required Work | B | None | `attendance_schedules` (expected weekdays, effective dates) | **Not implemented** | Python schedule is the Adult Day instance of "required work". |
| C4 | Four-dimension status (Delivery / Documentation / Validation / Financial) | B | Delivery statuses typed on `WorkActivity`; no writer; other three not modelled | Attendance boolean; billing `verified`; exception `status` | **Partially implemented** (Delivery only) | Python app collapses delivery into a boolean and has no documentation/validation/financial states as such. |
| C5 | Entitlement never blocks care | B | n/a | n/a (reconciliation is post-hoc, so nothing blocks) | **Not implemented** | Must be designed in from the start of C2. |
| C6 | Assessments, goals, interventions, tasks, referrals/intake, care team, documentation templates | C | None | None | **Unclear / requires decision** | Handoff says NOT DISCUSSED; do not invent. |
| C7 | Program / Funding Source / Payer / MCO / Location | B (names) | None (`regularWorkLocationIds` free text) | Implicit single program; payer as string category (8 hard-coded) | **Not implemented** | |
| C8 | Authorization relating Service to Entitlement | B | None | `rate_rules`: payer, rate, rotation, effective dates | **Not implemented** | Python rotation logic is a real business rule to port as a rule version. |

### 1.3 Scheduling / EVV (Level C / B)

| # | Requirement | Actual | Classification |
|---|---|---|---|
| S1 | Scheduling, availability, matching | None in either codebase | **Not implemented**; details NOT DISCUSSED → **requires decision** |
| S2 | EVV posture service-code driven with `pending_determination`; never "care management is exempt" | Nothing in code; documented posture only | **Not implemented** |
| S3 | Delivery dimension states | Typed | **Partially implemented** |

### 1.4 Billing / Revenue (Level B)

| # | Requirement | Actual (platform) | Actual (Python app) | Classification |
|---|---|---|---|---|
| B1 | Revenue Ledger (eligibility → charges → invoicing → posting → adjustments → write-offs) | None | None; imports PCC's actual charges and reconciles them against expected payer/rate | **Not implemented** |
| B2 | Financial dimension states | None | `exceptions.status` open/resolved/not_an_error on findings, not on services | **Not implemented** |
| B3 | Invoices / payments | None | None | **Not implemented** |
| B4 | Reconciliation of expected vs. actual billing | None | **Implemented** in Python app (7 exception types, resolution carry-over, CSV) | **Not implemented** (platform) — strong reuse candidate |

### 1.5 Reporting / dashboards (Level B)

| # | Requirement | Actual | Classification |
|---|---|---|---|
| R1 | Decision-driving KPIs; Today = unresolved items | Platform: none (no UI). Python app: dashboard with today panel, needs-attention list, month KPIs; billing week grid with single next action | **Not implemented** (platform); Python app implements the principle for its domain |

### 1.6 Administration / permissions / auditability (Level A)

| # | Requirement | Actual | Classification | Notes |
|---|---|---|---|---|
| P1 | Deny-by-default RBAC, no admin bypass, 24 permissions | `authorize()` | **Implemented** | `assertPermission` ignores `Role.tenantId` (D1 partial); only 11 permissions used. |
| P2 | ABAC (relationship scope) | None | **Not implemented** (Level B) | Self-approval of mileage possible; any `time.write` can write any employee's time. |
| P3 | AI narrowing (manifest ∩ roles, kill switch, observe-only) | Yes | **Implemented** | |
| P4 | Navigation filtered by permission | No UI | **Not implemented** | |
| P5 | Audit event per material mutation in same tx with actor kind, tenant, correlation, source, reason, redacted before/after, AI provenance | Yes for the 5 services | **Implemented** | `redactForAudit` never applied (services hand-pick fields); `requestContext` never populated. |
| P6 | Audit and rule-version append-only at DB | Triggers + grants | **Implemented** | |
| P7 | Credential management API, first-tenant bootstrap | None (SQL only) | **Not implemented** | Blocks any non-developer use. |
| P8 | Human sign-in (OIDC → BFF cookie) | None | **Architecturally blocked** on IdP decision (OQ #19) | Python app has native passwords, rejected for the platform by decision #25. |
| P9 | Rate limiting | None | **Not implemented** | |

### 1.7 Integrations (§9)

| System | Actual | Classification |
|---|---|---|
| SmartLinx | Adapter contract + fake; vendor checklist | **Architecturally blocked** (vendor) |
| Gusto/ADP/Paychex/QuickBooks | Contract accommodates | **Not implemented**; not targeted |
| Align CRM | Nothing | **Unclear / requires decision** (vendor identity OQ #1) |
| EVV vendors / MCOs | Nothing | **Not implemented** |
| **PointClickCare** (not in handoff) | Python app imports PCC batch HTML/PDF exports and categorises PCC charge codes | **Implemented** in Python app; **absent from handoff** — the first concrete EHR integration in the organisation |
| EHR / accounting / comms / telephony | Nothing | **Unclear** (NOT DISCUSSED) |
| Entra ID | Nothing | **Requires decision** |

### 1.8 AI (§8)

| Requirement | Actual | Classification |
|---|---|---|
| Contracts (AiRun … AiEscalation), always-approval list | `ai/contracts.ts` | **Implemented** (types) |
| AI actors resolved with manifest/kill switch; refuse mutations without run provenance | Yes, tested | **Implemented** |
| AI runtime, copilot UI, provenance plumbing | None | **Not implemented** (by design; handoff says do not build yet) |

### 1.9 UX / frontend (§7)

| Requirement | Actual | Classification |
|---|---|---|
| Design system, tokens, navigation model, states, mobile/desktop rules | Documented (`docs/FRONTEND-DESIGN-SYSTEM.md`, skill, tokens CSS) | **Not implemented** (docs only) |
| `apps/web` scaffold, ⌘K, Playwright + axe | None | **Not implemented** |
| Workforce screens one-handed on phone; payroll keyboard-first | None | **Not implemented** |
| Python app UI | Complete, responsive, script-free, phone layouts | Exists for its domain; **not** on the platform stack |

### 1.10 Technical architecture (§10) and platform hygiene (§15 item 1)

| Requirement | Actual | Classification |
|---|---|---|
| Node 22 / TS 5.9 / ESM strict; Vitest; typed eslint | Yes | **Implemented** |
| Postgres 16 + Drizzle; FORCE RLS; roles; composite FKs; append-only triggers; `seq` replay | Yes (`travel_determination` lacks `seq`) | **Implemented** |
| Domain imports no infrastructure | Verified | **Implemented** |
| Fastify + Zod + OpenAPI, strict bodies, error semantics | Yes; response schemas on 2/19 routes; unmapped errors leak SQL text as 400 | **Partially implemented** |
| Temporal only; no `Date` in domain | Current modules yes; legacy modules use `Date` | **Partially implemented** |
| CI (GitHub Actions) | Defined, **never run** (no remote) | **Partially implemented** |
| Rules loaded from `rule_version` at runtime; `rule.publish` route | Code-seeded in-memory registry | **Not implemented** |
| Shared Zod contracts extracted to `src/contracts/` | Inline in `server.ts` | **Not implemented** |
| Persisted cross-claim mileage duplicate check | In-memory only | **Not implemented** |
| Deployment / secrets / TLS / observability | NOT DECIDED (platform); Python app has a working single-VM Docker + Caddy deployment | **Unclear / requires decision** |
| Git remote for the platform | None | **Requires decision** (this repository, private, or another) |

---

## 2. The owner's two operating chains, element by element

### 2.1 Client → Program → Authorization → Care Plan → Service → Activity/Visit → Documentation → Hours → Billing → Revenue

| Element | Platform | Python app | Verdict |
|---|---|---|---|
| Client | uuid column only | `participants` ✔ | Not implemented (platform); implemented single-tenant (Python) |
| Program | — | implicit single program | Not implemented |
| Authorization | — | `rate_rules` (payer + rate + rotation + effective dates) ≈ | Not implemented (platform); partial and Adult-Day-specific (Python) |
| Care Plan | — | `attendance_schedules` ≈ required work only | Not implemented |
| Service | `WorkActivityType` enum | PCC charge codes inside `raw_line` | Not implemented as an entity in either |
| Activity / Visit | `work_activity` table, no writer | `attendance_records` day boolean ✔ | Partial (platform: table only; Python: day-level only) |
| Documentation | typed dimension, not modelled | paper sheet referenced, not modelled | Not implemented |
| Hours | `time_ledger_entry` ✔ (staff hours) | — (participant days) | Implemented for staff; **participant units absent** in both |
| Billing | — | `billing_records` imported from PCC + reconciliation ✔ | Not implemented (platform); post-hoc reconciliation (Python) |
| Revenue | — | — | Not implemented |

The link **Activity → Hours** exists only for staff time and only when the caller records both. The link **Hours → Billing** does not exist anywhere. The link **Client → Authorization → Visit → Billing** exists only in the Python app, only for one program, and only as reconciliation of a third party's billing.

### 2.2 Employee → Role → Program → Employment Status → Productivity Target → Hours → Service Activity → Productivity

| Element | Platform | Python app | Verdict |
|---|---|---|---|
| Employee | `employee` table, no writer | — (users are logins, not employees) | Partial |
| Role | RBAC `role` (authorization), not job role/position | `admin|reviewer` | **Concept mismatch**: no job-role/position entity anywhere |
| Program | — | — | Not implemented |
| Employment Status | `employment` (effective-dated, close-only), no writer, not consulted | — | Partial |
| Productivity Target | — (Capacity Profile Level B) | — | Not implemented |
| Hours | Time Ledger ✔ | — | Implemented |
| Service Activity | `work_activity` no writer; `ACTIVITY_TO_SEGMENT` map | — | Partial |
| Productivity | `computeProductivity()` pure fn, golden-tested | — | Partial (not persisted, no ledger inputs) |

---

## 3. Handoff assumptions that appear technically problematic

1. **"Payroll-ready calculations" (Level A).** The platform stores export lines whose quantities and sources the caller asserts. Until a derivation service builds lines from live ledger entries with FK-checked source claims, the export is a *record of a claim*, not a *calculation*. Flag: the handoff's Level A for W9 is really Level B for the derivation step.
2. **Entitlement in hours.** The handoff's Entitlement Ledger and Service Plan buckets speak in hours. The only live client business (Adult Day) bills **days** with per-day rates and payer rotations counted in attended days. The entitlement/authorization model needs a unit of measure per service definition or Adult Day cannot be represented without distortion.
3. **"Role" overloading.** The owner's chain uses Role as job role; the platform's Role is an authorization construct. Model `position` separately.
4. **Golden tests pin legacy modules with open defects** (D4-legacy, D5, D6, D9, D11). The handoff forbids weakening them without a documented invariant change, which means the defects cannot be closed without owner approval. This is a policy, not a technical, blocker — but it should be surfaced now rather than discovered mid-slice.
5. **Compliance facts from the client.** The design says jurisdiction, timezone and Rev. Rul. 99-7 facts come from Employment set by HR. The API takes them from request bodies. Closing this requires Employment to be writable and consulted first (W6/W12).
6. **Vendor call inside a transaction** contradicts the handoff's own "never lose the record of a real send" rule. Needs a two-phase submit before any real adapter.
7. **Rule registry duality.** The API cannot honour tenant-specific rule versions until the registry loads from the database; several handoff statements ("rules are data, not code") are aspirational at runtime.
8. **Superuser bootstrap** (`hcbs_auth_resolver` BYPASSRLS) may not be possible on some managed Postgres services. Verify against the eventual host before committing to it.
9. **Handoff omits PointClickCare entirely** while the organisation's only running system integrates with it by file export. The integration list should add PCC (EHR/billing) with its actual export format.
10. **Python app assumptions carried into any port:** rotation position counted over all attended days with no lower bound; Mon–Fri only; container-local "today"; payer vocabulary hard-coded. These are correct for one site today and must become rule versions and tenant configuration in the platform.

---

## 4. Conflicts between documentation and code (consolidated)

| Where | Doc says | Code does |
|---|---|---|
| Handoff §3 / PROJECT-STATE | "Built and tested … all clean" | Never run in CI; no remote; not re-verified in this environment |
| Handoff §6 | Export requires soft-closed or closed | `reopened` allowed (`payrollService.ts:79`) |
| Handoff §4, §10 | Platform computes payroll-ready calculations | Lines caller-asserted |
| Handoff §10 | `Date` not used in domain code | Legacy `time.ts`, `leave.ts`, `classification.ts` use `Date` |
| Decision #17 | App role has no privilege on `api_credential` | `0007:25` grants SELECT/INSERT/UPDATE |
| Decision #19 | Replay by `seq` never timestamp | `travel_determination` has no `seq` |
| Platform README | 136 tests, no API, no DB | 146 tests, API, Postgres |
| TEST-STRATEGY, SECURITY-ARCHITECTURE | Phase-1-era statements | Phase 2 reality |
| COMPLIANCE-ASSUMPTIONS #11 | Rev. Rul. 99-7 not implemented | Implemented |
| Seeds | retention 5 years (`esl_accrual`) | 6 years (`record_retention`) |
| Python README | `/upload` accepts workbooks; trials excluded from missing-from-batch; audit append-only; no PHI in errors | Workbooks admin-only; TRIAL status unused by reconcile; convention only; 422 echoes form input |
| Prior audit branch PROJECT-STATE | "awaiting approval to start Phase 1 hardening" | Tarball already executed most of it |

---

## 5. Decisions required before or during the build

Numbered as in `BUILD_PLAN.md`.

- **D-0** Repository visibility and fixture names (urgent).
- **D-1** Codebase strategy: platform as base, Python app preserved until superseded (recommended) vs. evolve Python vs. separate repos.
- **D-2** Where the platform lives: this repository under `hcbs-platform/` (recommended if the repo becomes private) or a new private repository.
- **D-3** Approval to retire legacy modules by re-targeting golden tests at the ledgers and rules engine (fixes D5, D6, D9, D11, D4-legacy). Includes the NJ per-diem criterion change (prior OQ #11).
- **D-4** Unit of measure for entitlement/authorization (days and hours both supported per service definition — recommended) before any client model.
- **D-5** Identity provider for staff sign-in (Entra ID recommended) — on the critical path for any platform UI reaching staff.
- **D-6** SmartLinx: mechanism, T&A vs Payroll, employee identifiers, pay-code map (OQ #15–#18).
- **D-7** Whether the Adult Day module in the platform must reach feature parity with the Python app before cut-over (recommended: yes, with a parallel-run period) and who owns the data migration window.
- **D-8** Business-date convention for midnight-crossing periods (OQ #9); audit retention (OQ #10); NJFLA scope (OQ #11); GPS notice default (OQ #4).
- **D-9** Deployment target, secrets manager, observability for the platform (NOT DECIDED anywhere).
- **D-10** Whether to apply the four Python-app bug fixes now (merge crash, CSP-blocked confirmations, re-upload deleting resolutions, 422 echo) as a maintenance patch.
