# Data Model

## Implemented (see `docs/db-schema-phase1.sql` for the Postgres target; in-memory reference implementations in `src/domain/`)
- **Tenant**, **Actor** (human | ai_agent, with `ownerActorId` for AI actors), **Role**, **RoleAssignment**, **AuditEvent** — see `identity.ts`, `audit.ts`.
- **TimeEntry** (kind, minutes, employeeId, optional clientId) — `time.ts`. Kinds: direct_service, travel_client_to_client, travel_home_to_first_client, travel_last_client_to_home, documentation, client_family_communication, provider_coordination, wait_on_duty, administration, training, meal_break.
- **TimeOffDay** (category, hours, isPaid, approval) — `leave.ts`. Categories: pto, nj_earned_sick_leave, holiday, bereavement, jury_duty, unpaid_leave.
- **NjPerDiemExemptionAttestation** — `leave.ts`. Per-employee, auditable, never inferred from job title.
- **MileageRate** (jurisdiction, purpose, rate, effective_start/end, source, source_reference) — `mileage.ts`.
- **ClassificationRecord** (classification, exemptionCategory, salaryBasisMet, salaryLevelMet, dutiesAnalysisCompleted, reviewedBy) — `classification.ts`. Requires a human reviewer for any exempt classification; the completeness gate refuses incomplete records.
- **ExportClaim** (entryId, ledger, batchId, status) — `payrollExport.ts`. The dedup/idempotency layer, not the payroll ledger itself.

## Specified but not yet implemented
From the source spec §11, organized by area:

- **Organization/tenancy:** Organization, Location, Program, Funding Source, Payer, MCO (Tenant exists; the rest don't).
- **Identity:** User (as distinct from Actor — currently Actor conflates the two), Employment (distinct from Actor).
- **Workforce:** Employment record, Employee Capacity Profile as a persisted entity (only the calculation function exists), Compensation Profile, Availability, Schedule, Visit (as a Work Activity subtype), Travel Segment (as a first-class entity — currently travel is only a `TimeSegmentKind`, not a segment with origin/destination), Mileage Claim (as a persisted entity — currently only the rate-resolution function exists), Productivity Calculation as a persisted/versioned snapshot (only the pure function `computeProductivity` exists).
- **Care management:** Client, Referral, Intake, Assessment, Care Plan, Service Plan, Authorization, Task, Note, Contact, Care Team — none implemented (Phase 2).
- **Finance:** Payroll Batch/Line as persisted entities (only the claim/dedup layer exists), Reimbursement as a persisted entity (only the rate calculation function exists), Adjustment, Reversal, Export, External Transaction ID.
- **Compliance:** Rule, Rule Version, Jurisdiction as persisted/queryable entities (currently compliance facts live as code constants + `docs/COMPLIANCE-ASSUMPTIONS.md` rows, not database rows — this is a real gap once multiple jurisdictions are in play), Employee Determination (the classification record exists; a determination *review workflow* does not), Exception.
- **AI:** Agent, Agent Execution, Tool Invocation, Approval Requirement, Human Review, AI-generated Artifact, Source/Evidence, Confidence — none implemented (Phase 8).

## Key modeling decision carried forward from the design conversation
Every derived financial/compliance record must reference its source record, source TimeEntry (where applicable), and the RuleVersion that produced it. This traceability requirement is why `Rule`/`RuleVersion` needs to become a real persisted entity before the ledgers are built — right now compliance constants like `FEDERAL_EAP_WEEKLY_SALARY_THRESHOLD` are just TypeScript constants with a comment citing their source, which is fine for Phase 1 but won't satisfy "why was this calculated this way, traceable to the rule version in effect" once real payroll calculations exist.
