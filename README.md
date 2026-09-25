# HCBS Workforce / Care Management Platform

**Status: Phase 1 (Foundation) bootstrap, built in a non-persistent sandbox.** Read `docs/PROJECT-STATE.md` before doing anything else — it is the authoritative statement of what exists and what doesn't.

## What's actually here
- A TypeScript/Node 22 domain layer implementing the highest-compliance-risk logic first: tenant-scoped RBAC, append-only audit, a time-segment taxonomy that never collapses travel/PTO/direct-service into one "hours" field, a date-effective mileage rate engine, NJ Earned Sick Leave accrual with the narrow per-diem exemption gate, platform-side payroll-export idempotency, and an FLSA classification record that refuses to let the system auto-derive exemption status.
- 20 passing "golden" tests (`tests/golden.test.ts`) exercising the above against the specific compliance scenarios in the source specification (client-to-client travel, PTO productivity math, the 2026 mid-year mileage rate change including late-submitted trips, accountable-plan day-count boundaries, NJ ESL, duplicate payroll export rejection, audit shape, tenant isolation, classification completeness).
- A canonical Postgres target schema (`docs/db-schema-phase1.sql`) — not yet wired to a live database.
- Decision, open-question, and compliance-assumption registers in `docs/`.

## What's NOT here yet
No API, no frontend, no database connection, no AI agents, no Client/Care Plan/Service Plan/ledger domain modules beyond Phase 1, no real payroll/Align/accounting integration. See `docs/PROJECT-STATE.md` → "NOT yet done."

## Getting this out of the sandbox
This was built in an ephemeral container with no persistent storage across sessions. To continue in Claude Code:
1. Download the repository (ask for it to be packaged as a zip/tarball).
2. `git remote add origin <your-repo-url> && git push -u origin main`
3. Clone/open that remote in Claude Code.
4. Read `docs/PROJECT-STATE.md`, then `docs/OPEN-QUESTIONS.md` items 2–3 before Phase 2.

## Running it
```
npm install
npm run typecheck
npm test
```
