# Project State

**Read this file first in every new session.**

## What this project is
An HCBS workforce, care-management, payroll, compliance, and operations platform for a NJ-based healthcare organization, architected to extend to more states, tenants, and service lines. Full product vision: `docs/ARCHITECTURE.md` (condensed) and the originating design conversation (not copied verbatim into this repo — see "Why source documents aren't copied in full" below).

## Environment reality (read this before assuming otherwise)
This repository was built and is currently being extended inside a claude.ai chat sandbox with an ephemeral container — **not** inside the actual Claude Code product. State persists *within* one chat conversation's tool calls but **does not persist across conversations** and there is no git remote yet. Everything in this file is only real until the repo is exported (tarball) and pushed to an actual git remote and opened in a persistent environment. Do not proceed as if a previous "session" in the Claude Code sense occurred — there hasn't been one yet.

## Current phase
Phase 1 (Foundation): domain-logic slice is implemented and tested. Phase 1's other listed components (tenancy *as persisted entities*, migrations against a live database, security foundation beyond the RBAC function, test infrastructure beyond Vitest) are partially done — see Gap Table below. Initialization/control-layer work (this update) is now also done. **Phase 2 (Client/Care Management foundation) has not started.**

## Last commit
`14d1e21` — "Bootstrap Phase 1: identity/RBAC, audit ledger, time/leave/mileage/classification domain modules, 20 golden compliance tests, docs" (this update's control-layer docs are staged for the next commit — see bottom of this file for the exact commit hash once made).

## Tests passing
`npx tsc --noEmit` — clean. `npx vitest run` — 20/20 passing (`tests/golden.test.ts`).

## Gap Table

| Area | Required (per source spec) | Existing | Gap | Risk | Next Action |
|---|---|---|---|---|---|
| Tenancy | Persisted Tenant/Organization/Location/Program/Funding Source/Payer/MCO | `Tenant`/`TenantId` type only, no persisted entity, no Organization/Location/Program/Payer concepts | Large | Medium — blocks nothing yet since no live DB exists | Build with Phase 2 once DB decision (OPEN-QUESTIONS #2) is made |
| Identity | Separate User / Employee / Actor / Service Account / AI Agent | `Actor` (human \| ai_agent) conflates User and Employee | Medium | Low now, grows as Employment/Compensation entities are added | Split `Actor` into `Actor` (auth identity) + `Employee` (employment record) before Phase 3 |
| RBAC/ABAC | Role-based + contextual policies (client assignment, supervisor relationship, jurisdiction, sensitivity) | Deny-by-default RBAC only (`assertPermission`); no ABAC | Medium | Low until Client/Care Team entities exist | Add ABAC once Phase 2 introduces client-assignment relationships |
| Audit | actor/action/entity/tenant/timestamp/before-after/reason/correlation ID/source/IP-device | All fields present except `correlation_id` and IP/device metadata | Small | Low | Add `correlationId` field when the first multi-step workflow (e.g., payroll export) needs to tie events together |
| Ledger architecture | 4 core + 2 sibling ledgers as persisted, append-only entities | Only `AuditStore` (general) and `PayrollExportClaimStore` (dedup-only) exist; no Time/Entitlement/Revenue/Labor-Cost/Leave/Reimbursement ledger tables | **Large** | **High** — this is the architectural core of the whole platform | Highest-priority build item once Phase 2 clients/engagements exist to attach ledger entries to |
| Four-dimension status model | Delivery/Documentation/Validation/Financial as independent state machines | Not implemented — only the underlying `TimeEntry`/documentation *data* exists, no state machine | Large | High | Build alongside Work Activity in Phase 2/5 |
| Work Activity / Care Opportunity | First-class, visit is one subtype | Not implemented | Large | Medium | Phase 2/5 |
| Service Plan / Entitlement | ServicePlanTemplate → Agreement → Period → EntitlementBucket → LedgerEntry | Not implemented | Large | Medium — but "entitlement never blocks care" invariant must be designed in from the start, not retrofitted | Phase 2 (Care Management foundation) |
| Workforce time/travel/mileage/PTO | Full model per source spec §11 | Domain *functions* exist (`time.ts`, `leave.ts`, `mileage.ts`) but no persisted Employment, Schedule, Availability, Travel Segment, or Mileage Claim entities | Medium | Medium | Phase 3/4 — wire the existing pure functions to real persisted entities, don't reimplement the logic |
| Compliance rules | Persisted, versioned Rule/RuleVersion/Jurisdiction entities | Compliance facts live as TypeScript constants + `COMPLIANCE-ASSUMPTIONS.md` rows | Medium | Medium — fine for one jurisdiction (NJ), won't scale to multi-state without becoming data | Convert to persisted, versioned rules before adding a second jurisdiction |
| Finance/Payroll | Payroll Batch/Line, Reimbursement, Adjustment, Reversal as persisted entities | Only the idempotency/claim layer (`payrollExport.ts`) exists | Large | High | Phase 6 |
| Security | Auth, encryption, secrets, MFA, session handling | None (no live infrastructure) | Large | High once any real data exists | Must precede any real PHI or payroll data — see `SECURITY-ARCHITECTURE.md` |
| AI runtime | Agent, Agent Execution, Tool Invocation, Approval Requirement, evidence/confidence | Not implemented | Large | Low until Phase 8 | Do not start early — no domain to act on yet |
| Align/payroll/EVV integrations | Adapters with idempotency, retries, error mapping, reconciliation | Not implemented; vendor identity for Align unresolved | Large | Medium | Phase 7, after vendor identity/selection decisions in `OPEN-QUESTIONS.md` |

## Completed (domain logic, tested)
See `docs/ARCHITECTURE.md` and `docs/DATA-MODEL.md` "Implemented" sections for the authoritative list — not repeated here to avoid the two files drifting out of sync. Short version: identity/RBAC/tenant-isolation, append-only audit, a time-segment taxonomy that never collapses travel/PTO/direct-service into one field, NJ ESL accrual + narrow per-diem exemption gate, date-effective mileage pricing + accountable-plan timing, platform-side payroll-export idempotency, and an FLSA classification completeness gate. 20 golden tests, all passing.

## Control-layer documents (this update)
`CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY-ARCHITECTURE.md`, `docs/DATA-MODEL.md`, `docs/TEST-STRATEGY.md`, `docs/AGENT-WORKSTREAMS.md` (supersedes the earlier `docs/agent-team.md`, removed), `docs/RESEARCH-LOG.md` — all created and populated this session. `docs/OPEN-QUESTIONS.md`, `docs/IMPLEMENTATION-DECISIONS.md`, `docs/COMPLIANCE-ASSUMPTIONS.md` carried forward from the bootstrap, unchanged.

## Why source documents (PRD, Blueprint v2, Workforce Addendum, both compliance validations) aren't copied into `/docs` verbatim
They total tens of thousands of words. Copying them in full would have consumed this session's entire budget on file transcription rather than working code and real control documents. `docs/ARCHITECTURE.md`, `docs/DATA-MODEL.md`, and `docs/COMPLIANCE-ASSUMPTIONS.md` carry every operative fact, table, and decision from those documents in condensed form — nothing load-bearing was dropped, only prose exposition. **If a future session needs the full verbatim narrative** (e.g., to resolve an ambiguity these condensed docs don't cover), it exists in the design conversation history and should be pasted in at that point, not before.

## Known risks (carried forward + new)
- Environment persistence, as stated at the top of this file.
- `mileage.ts`'s `daysBetween` uses simple UTC millisecond subtraction — not stress-tested against DST transitions (`TEST-STRATEGY.md`).
- Compliance facts as code constants (not yet persisted/versioned `Rule` entities) will not scale past one jurisdiction without a real data model — flagged in the Gap Table above, not yet urgent with NJ-only scope.
- `Actor` currently conflates User and Employee identity — flagged in the Gap Table, should be split before Phase 3 (Workforce) to avoid a painful later migration.

## Research completed
See `docs/RESEARCH-LOG.md` — nine entries covering NJ travel time, NJ ESL per-diem exemption, 2026 IRS mileage rates, accountable-plan deadlines, EVV applicability, HIPAA Security Rule status, payroll vendor API behavior, and the unresolved Align vendor identity.

## Research still required
Per `docs/RESEARCH-LOG.md` follow-ups: NJ employment counsel confirmation on travel-time scope; NJ DMAHS + MCO-contract confirmation on EVV applicability to any Medicaid-billed service line; sandbox confirmation of the actually-selected payroll vendor's idempotency/correction behavior; direct confirmation of the Align vendor's identity.

## Next actions (in order)
1. Export this repository (tarball) and push to a real git remote.
2. Open in an actual persistent Claude Code environment.
3. Resolve `OPEN-QUESTIONS.md` #2 (RLS/connection strategy) and #3 (payroll vendor selection) — both are `DECISION_REQUIRED`, not implementation details, and several Gap Table items depend on them.
4. Split `Actor` into `Actor` + `Employee` before Phase 3 work begins (see Gap Table).
5. Stand up a real Postgres instance against `docs/db-schema-phase1.sql`; write a `PostgresAuditStore` implementing the existing `AuditStore` interface as a contract test alongside the in-memory one.
6. Begin Phase 2 (Client / Care Management foundation) per `docs/ARCHITECTURE.md` and the development sequence in the master handoff.
