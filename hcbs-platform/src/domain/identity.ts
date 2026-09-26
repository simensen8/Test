/**
 * Identity & tenancy primitives.
 *
 * Non-negotiable invariants enforced by this module (Blueprint §37):
 *  - Every human and AI actor has a unique identity (Invariant 13).
 *  - Tenant isolation is enforced in code, never left to callers to remember.
 *  - RBAC/ABAC checks are deny-by-default.
 */

export type TenantId = string & { readonly __brand: "TenantId" };
export type ActorId = string & { readonly __brand: "ActorId" };

export function tenantId(value: string): TenantId {
  if (!value) throw new Error("tenantId must be non-empty");
  return value as TenantId;
}

export function actorId(value: string): ActorId {
  if (!value) throw new Error("actorId must be non-empty");
  return value as ActorId;
}

/** Every record that belongs to a tenant must carry this. */
export interface TenantScoped {
  tenantId: TenantId;
}

export type ActorKind = "human" | "ai_agent";

export interface Actor extends TenantScoped {
  id: ActorId;
  kind: ActorKind;
  displayName: string;
  /** For AI actors: the named human owner accountable for this actor (Blueprint §19). */
  ownerActorId?: ActorId;
  active: boolean;
}

export type Permission =
  | "client.read"
  | "client.write"
  | "documentation.write"
  | "documentation.review" // performer cannot hold this on their own work — enforced at call site
  | "rate.write"
  | "compensation.read"
  | "compensation.write"
  | "payroll.export"
  | "period.close"
  | "ai.configure";

export interface Role extends TenantScoped {
  name: string;
  permissions: ReadonlySet<Permission>;
}

export interface RoleAssignment extends TenantScoped {
  actorId: ActorId;
  roleName: string;
}

export class AuthorizationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthorizationError";
  }
}

/**
 * Deny-by-default authorization check. There is intentionally no "isAdmin"
 * bypass here — administrative roles must be granted the specific
 * permissions they need, so every grant is visible in the audit trail.
 */
export function assertPermission(
  roles: readonly Role[],
  actorTenant: TenantId,
  targetTenant: TenantId,
  permission: Permission,
): void {
  if (actorTenant !== targetTenant) {
    throw new AuthorizationError(
      `Tenant isolation violation: actor tenant ${actorTenant} !== target tenant ${targetTenant}`,
    );
  }
  const granted = roles.some((r) => r.permissions.has(permission));
  if (!granted) {
    throw new AuthorizationError(`Permission denied: ${permission}`);
  }
}
