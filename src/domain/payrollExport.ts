/**
 * Payroll export idempotency. The platform is the dedup authority —
 * it never assumes a vendor API is idempotent (validated finding:
 * ADP/Paychex/QuickBooks idempotency behavior is unconfirmed; only
 * Gusto documents it). See docs/COMPLIANCE-ASSUMPTIONS.md item 15.
 *
 * Rule: a given source ledger entry may be claimed by at most one
 * *live* (non-reversed, non-rejected) export line, ever. Retrying an
 * export finds zero unclaimed entries and produces an empty batch
 * rather than a duplicate.
 */

export interface SourceLedgerEntry {
  ledger: "time" | "leave" | "reimbursement";
  entryId: string;
}

export interface ExportClaim {
  entryId: string;
  ledger: SourceLedgerEntry["ledger"];
  batchId: string;
  status: "claimed" | "reversed";
}

export class DuplicateExportError extends Error {}

export class PayrollExportClaimStore {
  private readonly claims = new Map<string, ExportClaim>();

  private key(e: SourceLedgerEntry): string {
    return `${e.ledger}:${e.entryId}`;
  }

  /** Throws if any entry already has a live claim. All-or-nothing so a
   * batch never partially claims entries. */
  claimForBatch(batchId: string, entries: readonly SourceLedgerEntry[]): void {
    for (const e of entries) {
      const existing = this.claims.get(this.key(e));
      if (existing && existing.status === "claimed") {
        throw new DuplicateExportError(
          `${this.key(e)} already claimed by batch ${existing.batchId}`,
        );
      }
    }
    for (const e of entries) {
      this.claims.set(this.key(e), { entryId: e.entryId, ledger: e.ledger, batchId, status: "claimed" });
    }
  }

  /** Releases claims so the entries can be re-exported in a later,
   * corrective batch (Blueprint §16: corrections via reversal). */
  reverse(entries: readonly SourceLedgerEntry[]): void {
    for (const e of entries) {
      const existing = this.claims.get(this.key(e));
      if (existing) existing.status = "reversed";
    }
  }

  isClaimed(e: SourceLedgerEntry): boolean {
    return this.claims.get(this.key(e))?.status === "claimed";
  }
}
