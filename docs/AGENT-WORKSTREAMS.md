# Agent Workstreams

Conceptual ownership for a single architectural source of truth (this repo). These are lenses to adopt per task in a Claude Code session, not separate autonomous processes that redesign the same domain independently.

| Workstream | Owns | Must never |
|---|---|---|
| **Architect** | System architecture, domain boundaries, dependency decisions, state machines, integration patterns, scalability | Let two modules reach into each other's storage directly; introduce a dependency without an `IMPLEMENTATION-DECISIONS.md` entry |
| **Compliance** | FLSA, NJ wage/hour, NJ ESL, mileage, accountable plans, EVV, HIPAA/security requirements, `COMPLIANCE-ASSUMPTIONS.md`, legal-risk flags | State a legal conclusion as settled fact without a confidence label; treat healthcare workers as automatically ESL-exempt; treat care management as automatically outside EVV |
| **Workforce** | Employee, worker classification, availability, scheduling, visits, travel, timekeeping, PTO, productivity, mileage | Auto-derive FLSA exemption from title/license/salary alone |
| **Care Management** | Clients, referrals, intake, assessments, care plans, tasks, notes, authorizations, service plans, contacts, care-team workflows | Not yet started — Phase 2 |
| **Finance/Payroll** | Payroll calculation boundary, reimbursement, mileage, payroll exports, corrections, reversal/re-export, payroll vendor adapters, accounting integrations | Let the platform calculate gross-to-net; export the same source entry twice; overwrite payroll history |
| **Security** | Authentication, authorization, tenant isolation, audit, PHI protection, encryption, secrets, access logging | Rely on UI-only tenant filtering; claim HIPAA compliance from technical controls alone |
| **QA/Test** | Golden tests, invariant tests, integration tests, regression tests, compliance test cases, adversarial tests, tenant-isolation tests | Mark a phase done because code compiles; delete a passing test without documenting why |
| **Integration** | External APIs, payroll vendors, EHR/CRM (Align), EVV, accounting, communications, future AI integrations | Infer vendor API capability from a blog, generic SDK behavior, or another vendor's behavior — mark `UNVERIFIED` and isolate behind an adapter instead |

## Escalation rule (unchanged from the bootstrap session)
Stop and ask the human only for: a material product decision, a compliance interpretation that would materially change system behavior, an architecture change, a major scope change, needing credentials/access, or an external vendor commitment. Everything else: decide, document in `IMPLEMENTATION-DECISIONS.md`, test, continue.

*(Supersedes the earlier, shorter `docs/agent-team.md` from the initial bootstrap — kept in git history, not duplicated going forward.)*
