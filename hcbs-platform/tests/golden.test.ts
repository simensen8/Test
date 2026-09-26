import { describe, it, expect } from "vitest";
import { tenantId, actorId, assertPermission, AuthorizationError, type Role } from "../src/domain/identity.js";
import { InMemoryAuditStore } from "../src/domain/audit.js";
import { deriveHoursWorked, type TimeEntry } from "../src/domain/time.js";
import {
  computeProductivity,
  accrueNjEarnedSickLeave,
  isEligibleForNjPerDiemExemption,
  type NjPerDiemExemptionAttestation,
  type TimeOffDay,
} from "../src/domain/leave.js";
import { calculateReimbursement, isSubstantiationTimely, isExcessReturnTimely } from "../src/domain/mileage.js";
import { PayrollExportClaimStore, DuplicateExportError } from "../src/domain/payrollExport.js";
import { assertClassificationComplete, IncompleteClassificationError, type ClassificationRecord } from "../src/domain/classification.js";

describe("Golden Test 1 — client-to-client travel is compensable", () => {
  it("counts inter-client travel toward hours worked", () => {
    const entries: TimeEntry[] = [
      { id: "1", employeeId: "e1", kind: "direct_service", startsAt: new Date("2026-09-14T08:00:00"), endsAt: new Date("2026-09-14T09:00:00"), minutes: 60, clientId: "A" },
      { id: "2", employeeId: "e1", kind: "travel_client_to_client", startsAt: new Date("2026-09-14T09:00:00"), endsAt: new Date("2026-09-14T09:30:00"), minutes: 30 },
      { id: "3", employeeId: "e1", kind: "direct_service", startsAt: new Date("2026-09-14T10:00:00"), endsAt: new Date("2026-09-14T11:00:00"), minutes: 60, clientId: "B" },
    ];
    expect(deriveHoursWorked(entries)).toBe(2.5); // 60 + 30 + 60 minutes
  });

  it("does NOT count home-to-first-client or last-client-to-home travel", () => {
    const entries: TimeEntry[] = [
      { id: "1", employeeId: "e1", kind: "travel_home_to_first_client", startsAt: new Date(), endsAt: new Date(), minutes: 20 },
      { id: "2", employeeId: "e1", kind: "direct_service", startsAt: new Date(), endsAt: new Date(), minutes: 60, clientId: "A" },
      { id: "3", employeeId: "e1", kind: "travel_last_client_to_home", startsAt: new Date(), endsAt: new Date(), minutes: 20 },
    ];
    expect(deriveHoursWorked(entries)).toBe(1); // only the 60-minute direct service
  });
});

describe("Golden Test 2 — PTO never inflates or deflates productivity unfairly", () => {
  it("40 scheduled - 8 PTO -> 32 available; 75% target -> 24 expected; 22 productive -> 91.7%", () => {
    const leave: TimeOffDay[] = [
      { id: "l1", employeeId: "e1", date: "2026-09-15", category: "pto", hours: 8, isPaid: true, approvedBy: "sup1", approvedAt: new Date() },
    ];
    const snap = computeProductivity(40, leave, 0.75, 22);
    expect(snap.availableHours).toBe(32);
    expect(snap.expectedProductiveHours).toBe(24);
    expect(snap.productivityPercent).toBeCloseTo(0.9167, 3);
  });

  it("never divides by zero into a false 0% when expected hours are zero", () => {
    const fullWeekLeave: TimeOffDay[] = [
      { id: "l1", employeeId: "e1", date: "2026-09-14", category: "pto", hours: 40, isPaid: true, approvedBy: "sup1", approvedAt: new Date() },
    ];
    const snap = computeProductivity(40, fullWeekLeave, 0.75, 0);
    expect(snap.productivityPercent).toBe("N/A");
  });
});

describe("Golden Test 3 — mileage rate resolves by date of travel", () => {
  it("prices a June 30 trip at 72.5 cents", () => {
    const { amount, rate } = calculateReimbursement("2026-06-30", 37.4);
    expect(rate.ratePerMile).toBe(0.725);
    expect(amount).toBeCloseTo(27.12, 2); // 37.4 * 0.725 = 27.115, rounded to nearest cent
  });

  it("prices a July 1 trip at 76 cents", () => {
    const { amount, rate } = calculateReimbursement("2026-07-01", 37.4);
    expect(rate.ratePerMile).toBe(0.76);
    expect(amount).toBeCloseTo(28.424, 2);
  });

  it("a June trip submitted late in July is still priced at 72.5 cents (date of travel, not submission)", () => {
    const { rate } = calculateReimbursement("2026-06-28", 10);
    expect(rate.ratePerMile).toBe(0.725);
  });
});

describe("Golden Test 4 — accountable plan timing (26 CFR 1.62-2(g)(2)(i))", () => {
  it("treats substantiation exactly 60 days after travel as timely", () => {
    expect(isSubstantiationTimely("2026-01-01", "2026-03-02")).toBe(true); // 60 days
  });

  it("flags substantiation 61 days after travel as untimely", () => {
    expect(isSubstantiationTimely("2026-01-01", "2026-03-03")).toBe(false); // 61 days
  });

  it("treats an excess-reimbursement return exactly 120 days after payment as timely", () => {
    expect(isExcessReturnTimely("2026-01-01", "2026-05-01")).toBe(true); // 120 days
  });

  it("flags an excess-reimbursement return 121 days after payment as untimely", () => {
    expect(isExcessReturnTimely("2026-01-01", "2026-05-02")).toBe(false); // 121 days
  });
});

describe("Golden Test 5 — NJ Earned Sick Leave", () => {
  it("accrues 1 hour per 30 hours worked", () => {
    expect(accrueNjEarnedSickLeave(30, 0)).toBe(1);
    expect(accrueNjEarnedSickLeave(300, 0)).toBe(10);
  });

  it("caps accrual at 40 hours regardless of frontloading", () => {
    expect(accrueNjEarnedSickLeave(600, 39)).toBe(40);
  });

  it("does NOT grant the per-diem exemption merely for working in healthcare", () => {
    const notEligible: NjPerDiemExemptionAttestation = {
      employeeId: "e1",
      isNjLicensedHealthProfessionalOrApplicant: false,
      employedByDohLicensedFacility: true,
      worksOnlyWhenAvailable: true,
      hasRicherPtoAlternativeOrWaivedEsl: true,
      isCertifiedHomemakerHomeHealthAide: false,
      attestedBy: "hr1",
      attestedAt: new Date(),
    };
    expect(isEligibleForNjPerDiemExemption(notEligible)).toBe(false);
  });

  it("excludes certified homemaker-home health aides even if other criteria are met", () => {
    const chha: NjPerDiemExemptionAttestation = {
      employeeId: "e2",
      isNjLicensedHealthProfessionalOrApplicant: true,
      employedByDohLicensedFacility: true,
      worksOnlyWhenAvailable: true,
      hasRicherPtoAlternativeOrWaivedEsl: true,
      isCertifiedHomemakerHomeHealthAide: true,
      attestedBy: "hr1",
      attestedAt: new Date(),
    };
    expect(isEligibleForNjPerDiemExemption(chha)).toBe(false);
  });
});

describe("Golden Test 6 — payroll export idempotency", () => {
  it("rejects claiming the same source entry into two batches", () => {
    const store = new PayrollExportClaimStore();
    store.claimForBatch("batch-1", [{ ledger: "time", entryId: "t-1" }]);
    expect(() => store.claimForBatch("batch-2", [{ ledger: "time", entryId: "t-1" }])).toThrow(DuplicateExportError);
  });

  it("allows re-export only after the original claim is reversed", () => {
    const store = new PayrollExportClaimStore();
    const entry = { ledger: "time" as const, entryId: "t-2" };
    store.claimForBatch("batch-1", [entry]);
    store.reverse([entry]);
    expect(() => store.claimForBatch("batch-2", [entry])).not.toThrow();
  });
});

describe("Golden Test 7 — audit trail on a rate change", () => {
  it("records who/what/when/why/before/after/tenant", () => {
    const audit = new InMemoryAuditStore();
    const tid = tenantId("tenant-1");
    const aid = actorId("finance-user-1");
    audit.record({
      tenantId: tid,
      actorId: aid,
      entityType: "MileageRate",
      entityId: "US-FEDERAL-2026H1",
      action: "update",
      reason: "IRS Announcement 2026-11 mid-year rate change",
      previousValue: { ratePerMile: 0.725 },
      newValue: { ratePerMile: 0.76 },
    });
    const [event] = audit.findByEntity(tid, "MileageRate", "US-FEDERAL-2026H1");
    expect(event).toBeDefined();
    expect(event?.actorId).toBe(aid);
    expect(event?.previousValue).toEqual({ ratePerMile: 0.725 });
    expect(event?.newValue).toEqual({ ratePerMile: 0.76 });
    expect(event?.reason).toContain("2026-11");
  });
});

describe("Golden Test 8 — tenant isolation", () => {
  it("denies a cross-tenant access attempt even with a valid permission", () => {
    const tenantA = tenantId("tenant-A");
    const tenantB = tenantId("tenant-B");
    const roles: Role[] = [{ tenantId: tenantA, name: "clinician", permissions: new Set(["client.read"]) }];
    expect(() => assertPermission(roles, tenantA, tenantB, "client.read")).toThrow(AuthorizationError);
    expect(() => assertPermission(roles, tenantA, tenantA, "client.read")).not.toThrow();
  });
});

describe("Golden Test 9 — FLSA classification cannot be inferred", () => {
  it("refuses an exempt classification with an incomplete duties analysis", () => {
    const record: ClassificationRecord = {
      employeeId: "e1",
      classification: "exempt",
      exemptionCategory: "learned_professional",
      effectiveDate: "2026-09-01",
      reviewedBy: "hr-director-1",
      reviewedAt: new Date(),
      salaryBasisMet: true,
      salaryLevelMet: true,
      dutiesAnalysisCompleted: false, // <-- selecting "licensed professional" alone is not enough
      jurisdiction: "NJ",
    };
    expect(() => assertClassificationComplete(record)).toThrow(IncompleteClassificationError);
  });
});
