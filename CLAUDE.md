# CLAUDE.md — Operating Contract

**Read `docs/PROJECT-STATE.md` before touching anything else.** This file governs *how* you work; PROJECT-STATE.md tells you *where things stand*.

## Non-negotiables (see `docs/COMPLIANCE-ASSUMPTIONS.md` and `docs/OPEN-QUESTIONS.md` for the full basis of each)

1. Read `docs/PROJECT-STATE.md`, then any doc relevant to the area you're touching, before changing architecture.
2. Preserve existing functionality. Run `npm run typecheck && npm test` before and after every meaningful change. All 20 golden tests in `tests/golden.test.ts` must stay green unless you are deliberately changing the behavior they test — and if you are, say why in the commit and in `IMPLEMENTATION-DECISIONS.md` first.
3. Never silently resolve legal/compliance uncertainty. If `docs/COMPLIANCE-ASSUMPTIONS.md` marks something `UNRESOLVED`, `LIKELY`, or `COUNSEL_REVIEW`, don't upgrade it to hard-coded logic on your own judgment — implement it as configuration, and log the gap in `docs/OPEN-QUESTIONS.md` if it's now blocking.
4. Never claim vendor API behavior without evidence. See `docs/COMPLIANCE-ASSUMPTIONS.md` item 15 and `docs/RESEARCH-LOG.md`. Anything not confirmed from official docs/sandbox gets an `UNVERIFIED` adapter boundary and a contract test, never a direct implementation.
5. Never weaken tenant isolation. Every tenant-owned entity carries `tenantId`; every read/write path is checked (see `src/domain/identity.ts` `assertPermission` for the current pattern — extend it, don't bypass it).
6. Never bypass audit requirements. If it's a material action, it goes through `AuditStore.record` (or its future Postgres-backed implementation) before you consider the work done.
7. Never overwrite immutable ledger history. Corrections are reversal + adjustment entries, never in-place edits (see `src/domain/payrollExport.ts` for the current pattern).
8. Never introduce an architectural dependency (new library, new external service, new framework) without a dated entry in `docs/IMPLEMENTATION-DECISIONS.md` explaining why.
9. Commit coherent milestones, not partial or broken states. Every commit should typecheck and pass tests.
10. Update documentation in the same commit as the decision it describes, not "later."

## Before you code
- Read the relevant `/docs` file(s) for the area you're touching.
- Check `docs/OPEN-QUESTIONS.md` — if your task depends on a `BLOCKER` or `DECISION_REQUIRED` item, stop and surface it rather than guessing.
- Check `docs/COMPLIANCE-ASSUMPTIONS.md` for anything touching wage/hour, leave, mileage, EVV, or HIPAA — don't re-derive these from scratch.

## After you code
- `npm run typecheck && npm test`
- Update `docs/PROJECT-STATE.md` "Completed" / "Next actions" sections.
- Update `docs/IMPLEMENTATION-DECISIONS.md` for any judgment call.
- Commit.

## When to stop and ask a human, rather than deciding and continuing
Per the source specification: a material product decision, a compliance interpretation that would materially change system behavior, an architecture change, a major scope change, needing credentials/access, or an external vendor commitment. Everything else: decide, document, test, continue.
