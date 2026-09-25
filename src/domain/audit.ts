/**
 * Audit ledger. Append-only by construction: the store exposes no update
 * or delete method, only `record` and read queries (Blueprint §37
 * Invariant 2: "Approved financial records are never silently
 * overwritten"; Invariant 14: "AI actions are auditable").
 */

import type { ActorId, TenantId } from "./identity.js";

export interface AuditEvent {
  id: string;
  tenantId: TenantId;
  actorId: ActorId;
  /** What kind of thing changed, e.g. "ReimbursementRate", "TimeOffRequest". */
  entityType: string;
  entityId: string;
  action: "create" | "update" | "approve" | "reverse" | "export" | "close_period";
  /** Why: required free-text reason, or a policy/rule reference. */
  reason: string;
  previousValue: unknown;
  newValue: unknown;
  /** Which workflow/process initiated it, e.g. "payroll_export_batch:PEB-2026-W38-01". */
  causedBy?: string;
  occurredAt: Date;
}

export interface AuditStore {
  record(event: Omit<AuditEvent, "id" | "occurredAt">): AuditEvent;
  findByEntity(tenantId: TenantId, entityType: string, entityId: string): readonly AuditEvent[];
}

let counter = 0;
function nextId(): string {
  counter += 1;
  return `audit_${Date.now()}_${counter}`;
}

/** In-memory reference implementation. Production adapter targets an
 * append-only Postgres table with no UPDATE/DELETE grants for the
 * application role (see docs/db-schema.sql). */
export class InMemoryAuditStore implements AuditStore {
  private readonly events: AuditEvent[] = [];

  record(event: Omit<AuditEvent, "id" | "occurredAt">): AuditEvent {
    const full: AuditEvent = { ...event, id: nextId(), occurredAt: new Date() };
    this.events.push(Object.freeze(full));
    return full;
  }

  findByEntity(tenantId: TenantId, entityType: string, entityId: string): readonly AuditEvent[] {
    return this.events.filter(
      (e) => e.tenantId === tenantId && e.entityType === entityType && e.entityId === entityId,
    );
  }
}
