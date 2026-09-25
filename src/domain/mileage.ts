/**
 * Mileage rate engine. Resolves strictly by DATE OF TRAVEL, never by
 * submission or payment date (26 CFR 1.62-2; IRS Notice 2026-10 /
 * Announcement 2026-11). Rates are data, not code — adding 2027 rates
 * must never require a code change (Blueprint §9 requirement).
 */

export interface MileageRate {
  jurisdiction: string; // e.g. "US-FEDERAL"
  purpose: "business";
  ratePerMile: number;
  effectiveStart: string; // YYYY-MM-DD, inclusive
  effectiveEnd: string; // YYYY-MM-DD, inclusive
  source: string;
  sourceReference: string;
}

/** Seed data only — an implementing team must load this from a
 * configuration table, not this file, so future rate changes are a
 * data migration, not a deploy. */
export const SEED_FEDERAL_BUSINESS_RATES: readonly MileageRate[] = [
  {
    jurisdiction: "US-FEDERAL",
    purpose: "business",
    ratePerMile: 0.725,
    effectiveStart: "2026-01-01",
    effectiveEnd: "2026-06-30",
    source: "IRS Notice 2026-10",
    sourceReference: "IR-2025-128",
  },
  {
    jurisdiction: "US-FEDERAL",
    purpose: "business",
    ratePerMile: 0.76,
    effectiveStart: "2026-07-01",
    effectiveEnd: "2026-12-31",
    source: "IRS Announcement 2026-11",
    sourceReference: "IRB 2026-29",
  },
];

export class NoApplicableMileageRateError extends Error {}

export function resolveMileageRate(
  tripDate: string,
  jurisdiction: string,
  rates: readonly MileageRate[] = SEED_FEDERAL_BUSINESS_RATES,
): MileageRate {
  const match = rates.find(
    (r) => r.jurisdiction === jurisdiction && tripDate >= r.effectiveStart && tripDate <= r.effectiveEnd,
  );
  if (!match) {
    throw new NoApplicableMileageRateError(
      `No mileage rate configured for ${jurisdiction} on ${tripDate}`,
    );
  }
  return match;
}

export function calculateReimbursement(
  tripDate: string,
  businessMiles: number,
  jurisdiction = "US-FEDERAL",
  rates?: readonly MileageRate[],
): { amount: number; rate: MileageRate } {
  const rate = resolveMileageRate(tripDate, jurisdiction, rates);
  return { amount: round2(businessMiles * rate.ratePerMile), rate };
}

/**
 * Accountable plan (26 CFR 1.62-2(g)(2)(i)) fixed-date safe harbor:
 * 30 days to advance, 60 days to substantiate, 120 days to return
 * excess. These are CONFIGURATION_DEFAULTs, not hard business logic —
 * exposed here as named constants so a future jurisdiction override
 * never requires touching call sites.
 */
export const ACCOUNTABLE_PLAN_DEFAULTS = Object.freeze({
  advanceDays: 30,
  substantiationDays: 60,
  excessReturnDays: 120,
});

export function isSubstantiationTimely(tripDate: string, submittedDate: string): boolean {
  return daysBetween(tripDate, submittedDate) <= ACCOUNTABLE_PLAN_DEFAULTS.substantiationDays;
}

export function isExcessReturnTimely(paidDate: string, returnedDate: string): boolean {
  return daysBetween(paidDate, returnedDate) <= ACCOUNTABLE_PLAN_DEFAULTS.excessReturnDays;
}

function daysBetween(isoA: string, isoB: string): number {
  const msPerDay = 86_400_000;
  return Math.round((Date.parse(isoB) - Date.parse(isoA)) / msPerDay);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
