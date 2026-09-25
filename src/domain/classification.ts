/**
 * FLSA exemption classification. Blueprint §11 / Invariant: the
 * application may support the determination but must never
 * auto-derive exemption from title, license, department, or salary
 * alone (validated finding: "learned professional" exemption turns on
 * actual primary duty, not credential — see docs/COMPLIANCE-ASSUMPTIONS.md
 * item 4).
 */

export type ExemptionCategory = "non_exempt" | "executive" | "administrative" | "learned_professional" | "other_exempt";

export interface ClassificationRecord {
  employeeId: string;
  classification: "exempt" | "non_exempt";
  exemptionCategory: ExemptionCategory;
  effectiveDate: string;
  reviewedBy: string; // human reviewer — required, never system-generated
  reviewedAt: Date;
  salaryBasisMet: boolean;
  salaryLevelMet: boolean;
  dutiesAnalysisCompleted: boolean;
  jurisdiction: string;
  supportingDocumentationRef?: string;
}

export class IncompleteClassificationError extends Error {}

/**
 * Validation gate only — this function REFUSES incomplete records, it
 * does not decide exemption for the caller. There is deliberately no
 * `deriveClassification(title, license, salary)` function anywhere in
 * this codebase.
 */
export function assertClassificationComplete(record: ClassificationRecord): void {
  if (record.classification === "exempt") {
    if (!record.reviewedBy) {
      throw new IncompleteClassificationError("Exempt classification requires a human reviewedBy");
    }
    if (!record.salaryBasisMet || !record.salaryLevelMet || !record.dutiesAnalysisCompleted) {
      throw new IncompleteClassificationError(
        "Exempt classification requires salaryBasisMet, salaryLevelMet, and dutiesAnalysisCompleted all true",
      );
    }
  }
}

/** Current federal thresholds restored 2026-05-15 (DOL WHD 2026-05-14
 * technical amendment). CONFIGURATION_DEFAULT — state thresholds may
 * be higher and must be checked per work location. */
export const FEDERAL_EAP_WEEKLY_SALARY_THRESHOLD = 684;
export const FEDERAL_HCE_ANNUAL_THRESHOLD = 107_432;
