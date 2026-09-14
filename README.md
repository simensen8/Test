# Billing Reconciliation Assistant

Automates the daily/weekly PCC ancillary billing batch review described by
Alexander Jost (Adult Day Program): it compares the **Weekly Attendance
Excel**, the daily **PCC ancillary batch PDF**, and the **Rate Master
Excel**, and surfaces only the discrepancies that need a human to look at
them. A day or week with nothing wrong is simply marked
**"Reconciled / No Exceptions."**

## What it checks, per billing day

- Participant attended but is missing from the PCC batch
- Participant did not attend but appears on the PCC batch
- Incorrect payer source (vs. the Rate Master; unlisted participants
  default to Private Pay)
- A rotating grant arrangement (e.g. "3 paid days, 4th day to the Parker
  Grant") not applied on the day it should have been
- A name on one source that can't be matched to the others
- Duplicate billing entries for the same participant/day

Sliding-scale private-pay rate adjustments are intentionally **out of
scope** (per the source process, those are reconciled by Finance and PCC
has no mechanism to reflect them) -- this tool only flags *incorrect
payer source*, never *incorrect rate*.

## How the workflow maps to the app

1. **Upload** the Weekly Attendance workbook, the Rate Master workbook,
   and each day's PCC batch PDF (`/upload`).
2. **Review** the PDF's auto-extracted rows (`/batches/<date>/review`)
   and correct/confirm them -- PDF extraction is a best-effort parser,
   not OCR ground truth, so every row must be verified before it can
   feed reconciliation.
3. **Reconcile** the day. The exception report (`/reports/<date>`) shows
   participant, date, expected billing, actual PCC billing, and the
   reason for every discrepancy, with room to record how it was
   resolved. Zero exceptions renders as "Reconciled / No Exceptions."
4. **Export** a CSV of the exception report for finance/compliance
   recordkeeping.

## Local setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in APP_ENCRYPTION_KEY / SESSION_SECRET_KEY for anything beyond local testing
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` -- the first visit walks you through
creating the initial administrator account. From there, the admin can
add accounts for other reviewers under **Users**.

Run the test suite with:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/pytest
```

## Design notes / known limitations

- **PDF parsing is heuristic.** The exact layout PCC exports was not
  available while building this, so `app/parsers/pcc_batch.py` tries
  structured table extraction first and falls back to text-pattern
  matching. This is why every batch upload lands on a **Review** screen
  before it can be reconciled -- treat that screen as mandatory, not
  optional. If your PCC export format is consistent, it's worth having
  someone adjust the parser's header/keyword matching once real exports
  are available, to cut down on manual correction.
- **Participant identity is keyed on last name.** The Weekly Attendance
  sheet only has last names, so that's the strongest join key all three
  sources can support. Two participants sharing a surname will be
  treated as the same person; if that ever comes up, disambiguate by
  editing the participant's stored name (contact an admin) and confirm
  the resulting exceptions by hand.
- **Grant/rotation rules require a human to confirm the parameters.**
  The Rate Master parser tries to detect a "N paid days, next day to the
  grant" pattern from free-text exception notes, but wording varies
  enough that the cycle length and grant payer name should always be
  checked (and can be edited) on the **Rate Master** screen.

## HIPAA -- what this app does, and what you still have to do

This application handles Protected Health Information (attendance,
billing, payer source tied to named participants) and was built with
that in mind, but **HIPAA compliance is a property of your whole
operation, not of a single codebase.** Here's the split:

### Built into the app

- **Encryption at rest.** Participant names, notes, and other PHI text
  fields are encrypted at the database-column level (Fernet/AES) before
  they're written, not just relying on disk encryption. Uploaded source
  documents (the Excel/PDF files themselves) are encrypted on disk too.
  Exact-match lookups on encrypted names use a keyed HMAC blind index,
  so the database never stores or indexes plaintext PHI.
- **Access control & authentication.** Password login with bcrypt
  hashing, account lockout after repeated failures, role-based access
  (admin vs. reviewer) gating user management and the audit log,
  per-user accounts (no shared logins).
- **Session security.** Signed, HttpOnly, SameSite=Lax session cookies;
  a 20-minute idle timeout and 10-hour absolute timeout (configurable);
  CSRF tokens on every state-changing form.
- **Audit logging.** Every login (success/failure), upload, view of a
  report or the batch review screen, edit, export, and admin action is
  recorded with who/when/what/IP in an append-only audit log
  (`/admin/audit-log`), satisfying the Security Rule's audit-controls
  expectation (45 CFR 164.312(b)).
- **Minimum necessary by default.** Reviewers can do the reconciliation
  workflow; only admins see the user list and audit log.
- **No PHI in error responses.** Auth/session failures redirect rather
  than leaking details; the UI never emails or otherwise pushes PHI
  outside the app.

### Still required from you / your organization (this app cannot do these)

1. **Business Associate Agreements (BAAs).** Wherever you host this
   (a cloud VM, a managed database, a backup service), you need a
   signed BAA with that provider before any real participant data
   touches it. Don't deploy to a host without one.
2. **Transport encryption (TLS).** Run this behind HTTPS in any
   real deployment -- e.g. a reverse proxy (Caddy, nginx, or your
   cloud load balancer) terminating TLS with a real certificate. The
   app sets cookies as `Secure`, which requires HTTPS to actually work;
   don't set `COOKIE_SECURE=false` outside of local development.
3. **Key management.** `APP_ENCRYPTION_KEY` and `SESSION_SECRET_KEY`
   must be generated once and then stored in a real secrets manager
   (not the `.env` file, and not the auto-generated dev fallback in
   `data/.encryption_key`) -- losing the encryption key makes existing
   PHI unrecoverable, and leaking it defeats the at-rest encryption
   entirely.
4. **Backups.** Back up the database and the encrypted uploads
   directory on a schedule appropriate to your recovery requirements,
   and store backups somewhere covered by the same BAA and encryption
   expectations as production.
5. **Workforce access management.** Deactivate accounts (`/admin/users`)
   promptly when someone leaves or changes roles; review the audit log
   periodically; don't share logins.
6. **Device/network security around the app**, e.g. don't run this on
   an unmanaged laptop with no disk encryption, don't expose the admin
   audit log or database port to the public internet.
7. **Risk analysis, policies, and breach procedures.** A HIPAA Security
   Rule risk assessment, a documented incident-response/breach
   notification process, and workforce training are organizational
   requirements this software can support (via its audit log and access
   controls) but cannot substitute for.
8. **Retention/disposal policy.** Decide how long exception reports and
   underlying source documents should be retained, and periodically
   purge what's past that window (not automated by this app today).

If you don't yet have a BAA-covered host to run this on, start there --
everything else in this README assumes that piece is already decided.
