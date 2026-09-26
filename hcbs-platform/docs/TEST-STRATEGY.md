# Test Strategy

## Current state
`tests/golden.test.ts` — 20 tests, all passing, zero infrastructure dependency (pure domain logic, no database, no network). Run with `npm test`; typecheck with `npm run typecheck`. Both must stay green.

## What the current suite actually covers
Golden Tests 1–9 from the source specification: client-to-client travel compensability (and non-compensability of commute legs), PTO/productivity math including a divide-by-zero guard, mileage date-of-travel pricing across the 2026 rate change (including a late-submitted trip), accountable-plan 60/120-day boundaries (exact-boundary and one-day-over cases for both), NJ ESL accrual and the narrow per-diem exemption (explicitly testing a CHHA exclusion and a "works in healthcare alone isn't enough" case), payroll-export duplicate rejection and reversal-then-reexport, audit trail shape, tenant-isolation denial, and FLSA-classification refusal on an incomplete duties analysis.

## What is NOT yet tested (because it doesn't exist yet)
- Any state machine transition (no state machines implemented).
- Any ledger write/reversal beyond the payroll-export claim layer.
- Any multi-tenant integration test against a real database (RLS policies aren't written).
- Any HTTP/API-level test (no HTTP layer exists).
- Leap-year and DST edge cases for date math. `mileage.ts`'s `daysBetween` uses simple UTC millisecond subtraction — flagged as a known risk in `PROJECT-STATE.md`, not yet stress-tested against a DST transition.
- Any AI agent behavior (no runtime exists).
- Any real vendor integration (no adapters implemented beyond the interface concept).

## Standard for every future vertical slice (per source spec §14)
Unit tests + integration tests where applicable + authorization tests + tenant-isolation tests + audit tests + regression tests + compliance golden tests where relevant. For compliance-sensitive calculations specifically: boundary dates, leap years, DST, late submissions, reversals/corrections, missing data, conflicting rules, jurisdiction changes. "Works on the happy path" is not an acceptance bar.

## Golden test cases still to be written (from the original 20-case list, not yet covered because the underlying feature doesn't exist)
Crisis override, after-hours work, work exceeding plan capacity, absorbed work, cancelled visit, missed-and-recovered visit, unresolved clinically significant care opportunity, employee overtime, reversed financial transaction, duplicate integration submission (distinct from the payroll-export dedup already tested — this one is about the Align/CRM intake boundary), AI action requiring human approval. Each should be added in the same commit as the feature it tests, not deferred.
