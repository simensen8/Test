# Architecture

Condensed from the design conversation's PRD / Blueprint v2 / Workforce Addendum. This file states the frozen architecture; it does not re-derive it. Full narrative reasoning lives in the originating design conversation, not copied here verbatim (see PROJECT-STATE.md for why).

## Operating chain
Client → Engagement → Service Plan → Care Plan → Required Work → Workforce → Schedule → Work Completed → Documentation → Validation → Hours → Entitlement Consumption → Billing/Revenue → Quality → Outcomes.

**Atomic unit of work is Work Activity, not Visit.** A visit is one Work Activity subtype among many (calls, coordination, assessments, crisis response, AI-performed administrative work). Not yet implemented — see Gap Table in PROJECT-STATE.md.

## Four-dimension status model (frozen; do not collapse into one field)
1. **Delivery:** Planned → Scheduled → Confirmed → In Progress → Delivered | Cancelled | Missed | Rescheduled
2. **Documentation:** Not Required | Required → Draft → Submitted → Returned → Resubmitted → Final
3. **Validation:** Not Started → Auto-Validated | In Clinical Review → Approved | Returned | Held
4. **Financial:** Not Eligible → Eligible → Credited → Classified (Included | Additional-Pending-Auth | Additional-Billable | Absorbed | Non-Billable) → Invoiced → Posted | Written Off

A scheduled visit is never automatically delivered; delivered is never automatically documented; documented is never automatically validated; validated is never automatically revenue. Not yet implemented as code — the current repo has the domain primitives (`TimeEntry`, `TimeOffDay`) these statuses will attach to, not the state machines themselves.

## Four core append-only ledgers (frozen)
- **Time Ledger** — validated time and classification.
- **Entitlement Ledger** — Service Plan hour consumption, grants, adjustments, expiration, authorization relationships.
- **Revenue Ledger** — revenue eligibility, charges, invoicing, posting, adjustments, write-offs.
- **Labor Cost Ledger** — employee labor cost and compensation/premium calculations.

Plus two sibling ledgers from the Workforce Addendum: **Leave Ledger** and **Reimbursement Ledger** — kept separate because leave is never hours worked and reimbursement is never compensation.

Every derived ledger entry references its source record, source TimeEntry (where applicable), RuleVersion, actor, timestamp, and reason. Corrections are reversal/adjustment/addendum entries, never in-place edits. **Currently implemented:** the reference `AuditStore` (general-purpose, not ledger-specific) and `PayrollExportClaimStore` (dedup/idempotency layer, not a full ledger). The four/six domain ledgers themselves are not yet built.

## Service Plan / Entitlement model (frozen concept, not yet implemented)
ServicePlanTemplate (versioned) → ServicePlanAgreement (per-engagement, effective-dated) → ServicePeriod (Open → Soft-closed → Closed → Reopened) → EntitlementBucket (Included, Rollover, Authorized-additional, Crisis-override, Goodwill) → EntitlementLedgerEntry. **Entitlement must never block care** — an exhausted bucket creates an operational/financial exception, never a delivery block.

## Employee Capacity Profile (partially implemented)
Effective-dated: employment type, FTE, scheduled hours, expected productive/client-facing hours, individual productivity target %, admin allocation, supervisor, effective dates, link to a restricted Compensation Profile (kept strictly separate from client billing rates). **Implemented:** `computeProductivity()` in `src/domain/leave.ts` implements the calculation given inputs. **Not implemented:** the persisted, effective-dated profile entity itself.

## Deterministic rules only for money
AI never calculates money. Commercial and compliance rules are deterministic, versioned (`RuleVersion`), and jurisdiction-aware. AI proposes; rules validate; humans approve where required; the system commits; audit records everything.

## AI workforce (not yet implemented)
Named AI Actors with human owners, permission manifests, autonomy levels (Observe → Recommend → Act-with-approval → Act-autonomously-with-notice), audit logs, kill switches. AI-performed work never consumes client hours, is never billed as labor, and never produces a payroll input. Candidate agents (Documentation Assistant, AI Scheduler, etc.) are specified in the design conversation but not built.

## Integration boundary
External systems (Align, payroll, accounting) connect through adapters, never direct coupling. Every outbound transaction carries an internal transaction ID, idempotency/dedup key, attempt count, response, status, and reversal relationship. Never assume vendor idempotency, update, or delete support without confirmation — see `docs/RESEARCH-LOG.md`.

## What exists in code today vs. what's specified above
See the Gap Table in `docs/PROJECT-STATE.md`. Short version: identity/RBAC/audit/time-taxonomy/leave/mileage/payroll-export-idempotency/classification primitives exist and are tested. Work Activity, the four-dimension state machines, the six ledgers as persisted entities, Service Plan/Entitlement, Care Plan, and the AI runtime do not exist yet.
