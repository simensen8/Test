# Implementation Decisions

Living record. Each entry: decision, reason, alternatives considered, date.

## 1. Language/runtime: TypeScript on Node.js 22
**Reason:** strong typing catches the "collapsed status field" and "mixed-up rate" class of bugs this domain is prone to; broad hiring pool; first-class tooling for the Postgres/testing stack below. **Alternatives considered:** none seriously — no signal in the source documents preferring another stack. **Date:** bootstrap.

## 2. Test runner: Vitest, no framework installed yet for the HTTP layer
**Reason:** fast, native ESM/TS support, Jest-compatible API. Keeping the Phase 1 slice framework-free (no Express/Fastify yet) means the 20 golden tests exercise pure domain logic with zero infrastructure dependency — they run in <20ms and need no database, matching the "prefer mature, maintained tooling" and "avoid unnecessary dependencies" instructions. **Deferred:** HTTP framework selection — see OPEN-QUESTIONS #8.

## 3. Database: PostgreSQL is the canonical target; Phase 1 code uses in-memory reference implementations
**Reason:** this environment has no Postgres instance and no Docker available, and a real DB connection is not needed to prove out the domain invariants (tenant isolation, audit shape, append-only ledgers, idempotency, date-effective rates). `docs/db-schema-phase1.sql` is the target schema with row-level security stubbed in. The `AuditStore` interface is written so a Postgres-backed implementation is a drop-in replacement — no domain code changes when the real adapter is written. **Next action:** first real task in Claude Code with a live Postgres instance should be writing `PostgresAuditStore implements AuditStore` and a migration runner (Prisma or node-pg-migrate — not yet chosen).

## 4. No ORM chosen yet
**Reason:** Phase 1 has no live database connection to target. Choosing an ORM (Prisma vs. Drizzle vs. Kysely vs. raw `pg`) before there's a schema to migrate against would be premature; the domain layer doesn't depend on this choice (repository interfaces, not ORM models, are the boundary). **Next action:** decide when Phase 2 (Client + Engagement) needs real persistence.

## 5. RBAC is deny-by-default with no admin bypass
**Reason:** Blueprint Invariant "every human and AI actor has a unique identity" plus the audit requirement means even administrative actions must be explicit permission grants, so they show up correctly in the audit trail rather than being invisible "isAdmin" shortcuts.

## 6. FLSA/NJ ESL/mileage/classification logic lives in `src/domain/`, not behind an HTTP boundary
**Reason:** these are the highest-compliance-risk, highest-consequence-of-error pieces of the system (Golden Tests 1–9 in `tests/golden.test.ts`). Keeping them as pure, dependency-free functions means they can be exhaustively unit tested now, before any API or UI decisions are made, and re-used identically by a future API layer, a batch job, or an AI agent tool — with the agent restricted to calling these functions, never re-implementing the logic itself.
