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

## Proposed Claude Code configuration (2026-09-26 — pending approval, not yet created)

Principle: **one main session owns every decision**; the source of truth is `/docs`. Subagents exist only where a fresh, narrow context improves quality, and reviewers are **read-only** (no Edit/Write) so they cannot make competing architectural changes — they report, the main session decides and records.

| Mechanism | Name | Why it earns its place |
|---|---|---|
| Subagent (read-only) | `compliance-reviewer` | Reviews each diff against COMPLIANCE-ASSUMPTIONS / golden invariants; flags any LIKELY/UNRESOLVED item turned into hard logic |
| Subagent (read-only) | `security-reviewer` | Tenant isolation, RLS, PHI in logs, audit coverage, authz on every path |
| Subagent (read-only + web) | `researcher` | Primary-source research returned in RESEARCH-LOG format with confidence labels; never edits code |
| Subagent (writes tests only) | `test-adversary` | Adversarial tests: DST, midnight, duplicates, stale versions, cross-tenant, retroactive rules |
| Skill | `phase-close` | PLAN→IMPLEMENT→TEST→TYPECHECK→SECURITY→COMPLIANCE→DOCS→COMMIT checklist |
| Skill | `add-rule-version` | How to add an effective-dated rule row (source, confidence, audit) without code edits |
| Skill | `record-compliance-claim` | How to add/upgrade a COMPLIANCE-ASSUMPTIONS row + RESEARCH-LOG entry |
| Hook | SessionStart | `npm ci` in `hcbs-platform/` so web sessions can run tests immediately |
| Built-in | `/code-review`, `/security-review` | Per-milestone review before commit |

Product/Domain, Backend, Database, Frontend, Payroll, AI, DevOps and Integration remain **lenses** in the table above, not agents, until their phase starts; each gets a skill only when it has a repeatable procedure.
