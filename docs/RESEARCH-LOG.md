# Research Log

Format: Question | Date checked | Source | Source type | Finding | Confidence | Implementation impact | Follow-up required.

Entries below summarize research already completed in the design conversation that produced this repo (two full validation passes). Full citation trails (exact URLs, quoted statutory/regulatory text) live in those documents, not duplicated here — this log carries the operative conclusion and where to look for more.

---

**Q:** Is intra-day travel between client homes compensable working time for NJ non-exempt HCBS employees?
**Date checked:** 2026-09-25 · **Source:** eCFR 29 CFR 785.35/785.38; N.J.A.C. 12:56-5.2; 3d Cir., *Sec'y of Labor v. Prestige Home Care*, 128 F.4th 146 (2025), cert. denied Oct. 2025 · **Source type:** primary (federal regulation, primary NJ regulation, federal appellate decision)
**Finding:** Yes. Federal 785.38 is explicit; NJ's own "at his or her place of work *or on duty*" language (785-5.2) supports at least the federal floor; the Third Circuit (which covers NJ) affirmed a ~$7M willful-violation judgment on exactly this fact pattern.
**Confidence:** VERIFIED · **Impact:** `src/domain/time.ts` `travel_client_to_client` defaults `countsAsHoursWorked: true`. **Follow-up:** none — this is the most solidly grounded finding in the whole compliance set.

---

**Q:** Does New Jersey have a specific adult (non-minor) travel-time regulation beyond the general "hours worked" definition?
**Date checked:** 2026-09-25 · **Source:** targeted search of N.J.A.C. Title 12, NJDOL guidance pages · **Source type:** primary regulation search (negative result)
**Finding:** No NJ regulation specifically addressing adult mobile-worker travel time was located. The only explicit NJ travel-time pay provision found applies to minors in a laundry-industry wage order.
**Confidence:** UNRESOLVED (negative finding from a bounded search, not proof of absence) · **Impact:** do not represent NJ as having a specific, more lenient rule than federal law. **Follow-up:** NJ employment counsel confirmation before scaling to additional NJ-specific behavior beyond the federal baseline.

---

**Q:** Is the NJ Earned Sick Leave "per diem health care employee" exemption a general healthcare-industry carve-out?
**Date checked:** 2026-09-25 · **Source:** N.J.S.A. 34:11D-2/-3 (P.L. 2018, c.10) · **Source type:** primary statute
**Finding:** No. It applies only to NJ-licensed health professionals (or applicants) at DOH-licensed facilities (plus hospital-system EMS) who work only when available, and who either have a richer PTO alternative or waived ESL. Certified homemaker-home health aides are explicitly excluded by statute.
**Confidence:** VERIFIED · **Impact:** `isEligibleForNjPerDiemExemption()` requires all four affirmative conditions plus a CHHA exclusion; never inferred from job title. **Follow-up:** none for the statutory text; per-employee eligibility determinations remain an HR/legal call, recorded via `NjPerDiemExemptionAttestation`.

---

**Q:** What are the current (2026) IRS standard business mileage rates and effective dates?
**Date checked:** 2026-09-25 · **Source:** IRS.gov IR-2025-128, Notice 2026-10, Announcement 2026-11 (IRB 2026-29) · **Source type:** primary (IRS)
**Finding:** $0.725/mile Jan 1–Jun 30, 2026; $0.76/mile Jul 1–Dec 31, 2026 (a mid-year increase citing fuel price increases — the first such mid-year change since 2022).
**Confidence:** VERIFIED · **Impact:** `SEED_FEDERAL_BUSINESS_RATES` in `src/domain/mileage.ts`; resolved strictly by date of travel. **Follow-up:** add the 2027 rate as a new data row (not a code change) once IRS publishes it — expect a Notice in December 2026.

---

**Q:** What are the accountable-plan (26 CFR 1.62-2) fixed-date safe-harbor deadlines?
**Date checked:** 2026-09-25 · **Source:** eCFR 26 CFR 1.62-2(g)(2)(i) · **Source type:** primary regulation
**Finding:** 30 days to advance, 60 days to substantiate, 120 days to return excess — resolving an earlier secondary-source discrepancy that claimed 60 days for the excess-return step.
**Confidence:** VERIFIED · **Impact:** `ACCOUNTABLE_PLAN_DEFAULTS` in `src/domain/mileage.ts`. **Follow-up:** none.

---

**Q:** Does the federal EVV mandate (21st Century Cures Act) apply to HCBS care/case management services?
**Date checked:** 2026-09-25 · **Source:** CMS EVV FAQ (5/16/2018); NJ DMAHS EVV service list · **Source type:** primary (CMS) + primary (NJ DMAHS), interpretive
**Finding:** Likely not, as an inference from CMS's service-category framing (personal care and home health categories) and NJ DMAHS's own EVV-required service list, which does not include case/care management. This is an inference, not an explicit CMS carve-out — and a service that bundles hands-on ADL/IADL support into an in-home visit could still be captured.
**Confidence:** LIKELY (not VERIFIED as an explicit exemption) · **Impact:** model as `evv_required | not_required | conditionally_required | pending_determination` keyed to service code, never a blanket `care_management = exempt` rule. **Follow-up:** confirm directly with NJ DMAHS and check each MCO contract for MLTSS-specific visit-verification duties before any Medicaid-billed service line goes live.

---

**Q:** What is the current status of the HIPAA Security Rule NPRM?
**Date checked:** 2026-09-25 · **Source:** HHS OCR NPRM (Jan 6, 2025, 90 FR 898); OMB Unified Agenda RIN 0945-AA22; industry legal-alert coverage · **Source type:** primary (Federal Register, OMB agenda) + secondary (law firm summaries of the agenda)
**Finding:** Still proposed, not final. The 2026 Unified Agenda moved final action to the "Long-Term Actions" category with a projected date around July 2027 (moved back from an earlier May 2026 target). A December 2025 industry letter (100+ organizations) asked HHS to withdraw it entirely; no withdrawal confirmed.
**Confidence:** LIKELY (agenda dates are non-binding estimates) · **Impact:** current HIPAA Security Rule is the legal baseline; NPRM provisions (MFA, encryption mandates, asset inventories) are forward-looking design inputs only — see `docs/SECURITY-ARCHITECTURE.md`. **Follow-up:** re-check before each major release; this timeline has already slipped once.

---

**Q:** Do ADP, Paychex, and QuickBooks Online Payroll APIs support idempotent hours/PTO/reimbursement submission the way Gusto does?
**Date checked:** 2026-09-25 · **Source:** ADP marketplace-hosted PDF developer guides; Paychex and Intuit developer portals (JavaScript-rendered, partially inaccessible); Gusto's published API docs · **Source type:** primary where accessible, otherwise unconfirmed
**Finding:** Gusto documents idempotency keys and version-based optimistic concurrency (409 on conflict). ADP's documented behavior is cycle-state gating ("Entering Payroll Information"/"Correcting Input" only) with no documented idempotency key — retried submissions are inferred, not confirmed, to create duplicate batches. Paychex and QuickBooks idempotency/reimbursement-pay-item-creation behavior could not be confirmed from accessible primary documentation.
**Confidence:** VERIFIED (Gusto only) / UNVERIFIED (ADP, Paychex, QuickBooks) · **Impact:** the platform is the dedup authority regardless of vendor (`src/domain/payrollExport.ts`) — this is a design decision, not contingent on any vendor's behavior. **Follow-up:** sandbox-confirm actual behavior for whichever vendor is selected (`OPEN-QUESTIONS.md` #3) before writing that vendor's real adapter; treat every claim above as `UNVERIFIED` for that vendor until then.

---

**Q:** What is the identity of the "Align" CRM referenced as the intake/referral system of record?
**Date checked:** 2026-09-25 · **Source:** general web search for products named "Align" in senior-care/home-care CRM contexts; Aline CRM (formed from the 2022 Enquire/Glennis/Sherpa merger) as the most plausible candidate · **Source type:** secondary, inconclusive
**Finding:** No confirmed match. Aline CRM is the closest plausible candidate given its home-care/senior-living CRM positioning, but this has not been confirmed with the organization or the vendor.
**Confidence:** UNRESOLVED · **Impact:** integration adapter interface exists conceptually; no implementation should proceed against assumed Aline (or any other) API behavior. **Follow-up:** confirm vendor identity directly with the organization before any Align-specific code is written (`OPEN-QUESTIONS.md` #1).
