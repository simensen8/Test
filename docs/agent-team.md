# Engineering Workstreams

One architectural source of truth (this repo's `docs/` + code), reviewed from these lenses. In a Claude Code session, these are prompts/roles to adopt per task, not separate long-running processes — the source spec's own instruction is "parallel expertise with a single architectural source of truth," not more agents for their own sake.

| Workstream | Owns | Must never |
|---|---|---|
| Product/Requirements | Acceptance criteria, requirement→test traceability | Silently resolve a business rule that changes behavior |
| Architecture | Domain boundaries, ledger/event integrity, module coupling | Let two modules reach into each other's tables directly |
| Compliance/Workforce Rules | `docs/COMPLIANCE-ASSUMPTIONS.md`, jurisdiction rules | State a legal conclusion as settled fact; skip the confidence label |
| Backend/Domain | Models, services, state machines, ledgers (this bootstrap's `src/domain/`) | Bypass the deny-by-default RBAC check |
| Frontend/UX | Operational UI, accessibility | — (not started; no frontend exists yet) |
| Data/Database | Schema, migrations, tenant isolation, indexing | Rely on UI-only tenant filtering |
| Security | AuthN/AuthZ, RBAC/ABAC, secrets, encryption | Treat NPRM HIPAA provisions as current law |
| QA/Test | Unit/integration/e2e, golden compliance tests | Mark a phase done because it compiles |
| DevOps/Infra | Repo, CI/CD, environments, backups | — (starter CI only; not yet deployed anywhere) |
| Integration | Payroll/accounting/EVV/Align adapters | Assume vendor idempotency without confirming (see COMPLIANCE-ASSUMPTIONS #15) |

Escalate to the owner only per source spec §24: material product decision, compliance interpretation that changes behavior, architecture change, major scope change, credentials/access needed, or an external vendor decision. Everything else: decide, document in `IMPLEMENTATION-DECISIONS.md`, test, continue.
