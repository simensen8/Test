/**
 * Workforce time model.
 *
 * Baseline (frozen per docs/COMPLIANCE-ASSUMPTIONS.md — VERIFIED):
 *  - Intra-day travel between client locations is compensable working
 *    time for NJ non-exempt HCBS workers (29 CFR 785.38; N.J.A.C.
 *    12:56-5.2's "at his or her place of work or on duty").
 *  - PTO/leave is never "hours worked" and is tracked on a separate
 *    ledger (see leave.ts) — it is NOT a TimeSegment here.
 *
 * This module intentionally has NO single "hours" field. Every
 * consumer must pick the specific quantity it needs (see the
 * `derive*` functions below) so that "paid ≠ worked ≠ productive ≠
 * revenue-bearing ≠ entitlement-consuming" (Blueprint §14) can never
 * be silently collapsed by a careless call site.
 */

export type TimeSegmentKind =
  | "direct_service"
  | "travel_client_to_client"
  | "travel_home_to_first_client"
  | "travel_last_client_to_home"
  | "documentation"
  | "client_family_communication"
  | "provider_coordination"
  | "wait_on_duty"
  | "administration"
  | "training"
  | "meal_break"; // unpaid by default; see policy

export interface TimeSegmentPolicy {
  /** Is this segment counted toward FLSA "hours worked"? Jurisdiction-configurable. */
  countsAsHoursWorked: boolean;
  /** Is this segment inherently client-facing? */
  isClientFacing: boolean;
  /** Can this segment ever be classified as revenue-bearing (subject to
   * later financial classification against a Service Plan)? */
  isRevenueEligible: boolean;
}

/**
 * Default federal/NJ baseline policy. This is a CONFIGURATION_DEFAULT
 * (Blueprint §21) — an implementing engineer may adjust per jurisdiction,
 * but every change must be recorded with an effective date and source,
 * never edited in place (see docs/COMPLIANCE-ASSUMPTIONS.md).
 */
export const DEFAULT_SEGMENT_POLICY: Readonly<Record<TimeSegmentKind, TimeSegmentPolicy>> = Object.freeze({
  direct_service: { countsAsHoursWorked: true, isClientFacing: true, isRevenueEligible: true },
  travel_client_to_client: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: true },
  travel_home_to_first_client: { countsAsHoursWorked: false, isClientFacing: false, isRevenueEligible: false },
  travel_last_client_to_home: { countsAsHoursWorked: false, isClientFacing: false, isRevenueEligible: false },
  documentation: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: true },
  client_family_communication: { countsAsHoursWorked: true, isClientFacing: true, isRevenueEligible: true },
  provider_coordination: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: true },
  wait_on_duty: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: false },
  administration: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: false },
  training: { countsAsHoursWorked: true, isClientFacing: false, isRevenueEligible: false },
  meal_break: { countsAsHoursWorked: false, isClientFacing: false, isRevenueEligible: false },
});

export interface TimeEntry {
  id: string;
  employeeId: string;
  clientId?: string;
  kind: TimeSegmentKind;
  startsAt: Date;
  endsAt: Date;
  /** Actual minutes are always stored; rounding happens only downstream
   * in financial/payroll derivation, never here (Blueprint §11). */
  minutes: number;
}

export function deriveHoursWorked(
  entries: readonly TimeEntry[],
  policy: Readonly<Record<TimeSegmentKind, TimeSegmentPolicy>> = DEFAULT_SEGMENT_POLICY,
): number {
  return sumMinutes(entries.filter((e) => policy[e.kind].countsAsHoursWorked)) / 60;
}

export function deriveClientFacingHours(
  entries: readonly TimeEntry[],
  policy: Readonly<Record<TimeSegmentKind, TimeSegmentPolicy>> = DEFAULT_SEGMENT_POLICY,
): number {
  return sumMinutes(entries.filter((e) => policy[e.kind].isClientFacing)) / 60;
}

function sumMinutes(entries: readonly TimeEntry[]): number {
  return entries.reduce((total, e) => total + e.minutes, 0);
}
