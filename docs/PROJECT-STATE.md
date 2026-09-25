# Project State

**Read this file first in every new session.**

## Current phase
Phase 1 (Foundation) — partially complete. Bootstrapped outside of Claude Code (in a Claude.ai chat sandbox with no persistent storage); this repo must be pushed to a real git remote and continued in an environment with persistent state (Claude Code, a real Postgres instance, CI) before further phases begin.

## Completed
- Git repository initialized.
- TypeScript + Node 22 + Vitest toolchain installed and configured (`package.json`, `tsconfig.json`).
- Domain modules with reference (in-memory) implementations and full type coverage:
  - `src/domain/identity.ts` — Tenant, Actor (human/AI), Role, deny-by-default RBAC (`assertPermission`).
  - `src/domain/audit.ts` — append-only `AuditStore` interface + in-memory reference impl.
  - `src/domain/time.ts` — `TimeSegmentKind` taxonomy (never a single "hours" field); `deriveHoursWorked`, `deriveClientFacingHours`.
  - `src/domain/leave.ts` — `TimeOffDay` (separate from TimeEntry), `computeProductivity`, NJ ESL accrual, narrow per-diem exemption gate.
  - `src/domain/mileage.ts` — date-effective `resolveMileageRate`, `calculateReimbursement`, accountable-plan timing helpers.
  - `src/domain/payrollExport.ts` — `PayrollExportClaimStore` (platform-side idempotency, vendor-agnostic).
  - `src/domain/classification.ts` — FLSA classification record + completeness gate (never auto-derives exemption).
- `tests/golden.test.ts` — 20 tests covering Golden Test Cases 1–9 from the source spec (travel compensability, PTO/productivity, mileage date-of-travel pricing incl. late June submission, accountable-plan 60/120-day boundaries, NJ ESL accrual + narrow per-diem exemption, payroll export duplicate rejection + post-reversal re-export, audit trail shape, tenant isolation denial, FLSA classification refusal on incomplete duties analysis).
- `npx tsc --noEmit` — clean. `npx vitest run` — 20/20 passing.
- `docs/db-schema-phase1.sql` — canonical Postgres target schema for tenant/actor/role/audit, with row-level security enabled (policies not yet written — see OPEN-QUESTIONS #2).
- `docs/IMPLEMENTATION-DECISIONS.md`, `docs/OPEN-QUESTIONS.md`, `docs/COMPLIANCE-ASSUMPTIONS.md` created and populated.

## NOT yet done (do not assume otherwise)
- No HTTP/API layer.
- No real database connection — everything runs against in-memory reference stores.
- No CI pipeline configured yet (a starter GitHub Actions workflow is included but unverified against a real remote).
- No frontend of any kind.
- No Client/Engagement/Care Plan/Service Plan/Work Activity/ledger domain modules yet (Phases 2, 4, 5, 7 of the original roadmap) — only the Phase 1 identity/audit/time/leave/mileage/payroll-export/classification slice exists.
- No AI agent runtime.
- Align, payroll, and accounting adapters are not implemented — only referenced as a future integration boundary.
- The full PRD / Blueprint v2 / Workforce Addendum / two compliance validation passes exist in the originating design conversation but were **not** copied into this repo verbatim (would have consumed the entire bootstrap budget on file-copying rather than working code). `docs/COMPLIANCE-ASSUMPTIONS.md` and `docs/IMPLEMENTATION-DECISIONS.md` carry the operative content. **If full verbatim source documents are wanted in `/docs`, paste them in during the first real Claude Code session.**

## Known risks
- This environment's filesystem does not persist between chat turns/sessions. Everything above exists only in this session's container until exported and pushed to a real git remote.
- Golden Test 4's day-count math uses simple UTC millisecond subtraction (`src/domain/mileage.ts` `daysBetween`) — fine for the 2026 date range tested, but should be reviewed for DST/timezone edge cases before relying on it near a DST transition.

## Next actions (in order)
1. Export this repository (see README.md "Getting this out of the sandbox").
2. Push to a real git remote.
3. Open in Claude Code with this repo as the working directory.
4. Resolve OPEN-QUESTIONS #2 and #3 (RLS strategy, target payroll vendor) — both are DECISION_REQUIRED, not implementation details.
5. Stand up a real Postgres instance; write `PostgresAuditStore` against `docs/db-schema-phase1.sql`; keep the golden tests green against both the in-memory and Postgres implementations (contract test).
6. Continue with Phase 2 (Client + Engagement) per the original roadmap.
