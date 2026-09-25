/**
 * Leave / PTO. Deliberately its own module: an approved TimeOffDay must
 * NEVER produce a TimeEntry (Blueprint §37 Invariant 6: "paid time,
 * hours worked, productive time and revenue-bearing time are distinct").
 */

export type LeaveCategory =
  | "pto"
  | "nj_earned_sick_leave"
  | "holiday"
  | "bereavement"
  | "jury_duty"
  | "unpaid_leave";

export interface TimeOffDay {
  id: string;
  employeeId: string;
  date: string; // YYYY-MM-DD
  category: LeaveCategory;
  hours: number;
  isPaid: boolean;
  approvedBy: string;
  approvedAt: Date;
}

/** Available capacity = basis hours − approved, capacity-reducing leave,
 * floored at zero per day (Blueprint §12 / Workforce Addendum §D). */
export function deriveAvailableHours(basisHours: number, leaveDays: readonly TimeOffDay[]): number {
  const leaveHours = leaveDays.reduce((t, d) => t + d.hours, 0);
  return Math.max(0, basisHours - leaveHours);
}

export interface ProductivitySnapshot {
  availableHours: number;
  expectedProductiveHours: number;
  productiveHours: number;
  productivityPercent: number | "N/A";
}

/**
 * Golden invariant: an employee is never scored as underperforming
 * merely because they used approved leave. Example from the Workforce
 * Addendum: 40 scheduled − 8 PTO = 32 available; 32 × 75% = 24 expected;
 * 22 productive ÷ 24 expected = 91.7%.
 */
export function computeProductivity(
  basisHours: number,
  leaveDays: readonly TimeOffDay[],
  productivityTargetPercent: number,
  productiveHours: number,
): ProductivitySnapshot {
  const availableHours = deriveAvailableHours(basisHours, leaveDays);
  const expectedProductiveHours = availableHours * productivityTargetPercent;
  const productivityPercent = expectedProductiveHours === 0 ? "N/A" : productiveHours / expectedProductiveHours;
  return { availableHours, expectedProductiveHours, productiveHours, productivityPercent };
}

/** NJ Earned Sick Leave accrual: 1 hour per 30 hours worked (N.J.S.A.
 * 34:11D-2), capped for accrual/use/carryover at 40 hours/benefit year
 * — the cap applies whether or not the employer frontloads (do NOT
 * relax it on a frontload flag; see docs/COMPLIANCE-ASSUMPTIONS.md,
 * item 5, correction re: frontload framing). */
export const NJ_ESL_CAP_HOURS = 40;
export function accrueNjEarnedSickLeave(hoursWorkedThisPeriod: number, priorAccruedHours: number): number {
  const accruedFromWork = hoursWorkedThisPeriod / 30;
  return Math.min(NJ_ESL_CAP_HOURS, priorAccruedHours + accruedFromWork);
}

/**
 * The NJ "per diem health care employee" exemption is NARROW
 * (validated finding — see docs/COMPLIANCE-ASSUMPTIONS.md item 5) and
 * must be an explicit, per-employee, employer-attested flag — never
 * inferred from job title, license, or "works in healthcare."
 * Certified homemaker-home health aides are EXCLUDED by statute.
 */
export interface NjPerDiemExemptionAttestation {
  employeeId: string;
  isNjLicensedHealthProfessionalOrApplicant: boolean;
  employedByDohLicensedFacility: boolean;
  worksOnlyWhenAvailable: boolean;
  hasRicherPtoAlternativeOrWaivedEsl: boolean;
  isCertifiedHomemakerHomeHealthAide: boolean; // if true, exemption CANNOT apply
  attestedBy: string;
  attestedAt: Date;
}

export function isEligibleForNjPerDiemExemption(a: NjPerDiemExemptionAttestation): boolean {
  if (a.isCertifiedHomemakerHomeHealthAide) return false;
  return (
    a.isNjLicensedHealthProfessionalOrApplicant &&
    a.employedByDohLicensedFacility &&
    a.worksOnlyWhenAvailable &&
    a.hasRicherPtoAlternativeOrWaivedEsl
  );
}
