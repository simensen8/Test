# Billing Reconciliation Assistant

Automates the daily/weekly PCC ancillary billing batch review described by
Alexander Jost (Adult Day Program): it compares the **Weekly Attendance**
workbook, the daily **PCC ancillary batch** export, and the **Rate Master**
workbook, and surfaces only the discrepancies that need a human to look at
them. A day or week with nothing wrong is simply marked
**"Reconciled / No Exceptions."**

This has been tested end-to-end against real sample files from the
program (a `.ods` attendance sheet, the consolidated Rate Master `.xlsx`,
and an actual PointClickCare batch detail report saved as `.html`), not
just synthetic data -- see "Built and verified against real files" below
for what that surfaced and fixed.

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

1. **Upload** the Weekly Attendance workbook (`.xlsx` or `.ods`), the
   Rate Master workbook (`.xlsx`), and each day's PCC batch export
   (`.html` -- save the PCC batch detail report page as a webpage; a
   `.pdf` export also works as a fallback) at `/upload`. Batch exports
   can be uploaded several at a time (a whole week in one go): each file
   is filed under the service date printed on it -- the "Services for
   M/D/YYYY" line, falling back to the rows' Eff. Date -- so there is no
   date to key in. A file whose date can't be read is refused rather
   than guessed at, and uploading a day again replaces that day.
2. **Check the roster** (`/participants`) when a name looks wrong. Every
   participant uploads have seen has a profile: canonical name, PCC ID,
   status (active/trial/discharged), notes, and how they're billed. Two
   records that turn out to be one person are merged there, which moves
   their attendance, billing and payer rule onto a single record.
3. **Review** the auto-extracted batch rows (`/batches/<date>/review`)
   and correct/confirm them -- extraction is a best-effort parser, not
   ground truth, so every row must be verified before it can feed
   reconciliation.
4. **Reconcile** the day. The exception report (`/reports/<date>`) shows
   participant, date, expected billing, actual PCC billing, and the
   reason for every discrepancy, with room to record how it was
   resolved. Zero exceptions renders as "Reconciled / No Exceptions."
5. **Export** a CSV of the exception report for finance/compliance
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

## Deploying it for real use

This handles PHI, so **it needs to run somewhere covered by a signed
Business Associate Agreement (BAA) before any real participant data
touches it.** The steps below target a small, single-location deployment
on AWS, since AWS lets any account accept their BAA for free and
self-serve (no sales process) via **AWS Artifact** in the console --
that's the main reason it's the simplest starting point if you don't
already have a cloud provider.

### 1. One-time account setup (you do this)

1. Create an AWS account.
2. In the AWS Console, go to **AWS Artifact > Agreements**, find the
   **Business Associate Addendum**, and accept it. Free, immediate, no
   approval wait.
3. Register a domain (any registrar, ~$12/year). A trusted TLS
   certificate can't be issued for a bare IP address, so this is
   required, not optional -- and the app marks its session cookie
   `Secure`, which needs real HTTPS to work at all.
4. Create a **Lightsail** instance: Ubuntu 22.04, the smallest paid
   plan is enough for one location's traffic (~$5/mo). Note its public
   IP, then point your domain's DNS `A` record at it.
5. In the Lightsail networking tab, open ports **80** and **443**
   (443 is not open by default).

### 2. Deploy (on the server, or hand me SSH access and I'll run these)

```bash
# On the fresh Ubuntu instance:
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

git clone -b claude/hipaa-billing-reconciliation-qty4bc https://github.com/simensen8/test.git
cd test

cp .env.example .env
# Edit .env: set APP_ENCRYPTION_KEY and SESSION_SECRET_KEY to freshly
# generated values (see the comment above each in .env.example) --
# generate them once and store the values somewhere safe outside the
# server (a password manager), since losing APP_ENCRYPTION_KEY makes
# all stored PHI unrecoverable.

# Edit Caddyfile: replace "your-domain.example.org" with your real domain.

docker compose up -d --build
```

Then open `https://your-domain.example.org` and create the admin
account on first visit.

### 3. Ongoing

- **Backups**: `docker compose exec app tar czf - /app/data | ...` (pipe
  to wherever your backup destination is) on a schedule -- this directory
  holds the encrypted database and uploaded source documents.
- **Updates**: `git pull && docker compose up -d --build`
- **Forgotten passwords**: an admin issues a new temporary password from
  `/admin/users` and hands it over in person or by phone (not email).
  The user is required to change it when they next sign in.
- **Logs**: `docker compose logs -f app`
- Caddy renews its own TLS certificate automatically; nothing to do there.

## Built and verified against real files

`app/parsers/pcc_batch.py`, `attendance.py`, and `rate_master.py`, plus
the participant-matching logic in `app/matching.py`, were built against
(and are unit-tested with fixtures mirroring) real sample files, not
just an assumed format. That surfaced several things worth knowing:

- **The real PCC export is HTML, not PDF.** It's a "Batch Detail
  Report" saved as a webpage from PointClickCare, with a stable table
  (`#`, `Participant Name (ID)`, `Eff. Date`, `Charge Code`,
  `Description`, `Payer Code`, `Total`, ...). The parser reads this
  table directly; PDF support is kept as a fallback in case a site ever
  exports differently.
- **PCC's "Payer Code" column alone is NOT reliable for payer source.**
  In a real export, a Parker Grant day and a plain Private Pay day are
  both coded `PP-ADC` -- only the Charge Code (`ADC P GRANT` vs `ADC
  MPNT`/`ADC MPT`) or Description actually distinguishes them. Getting
  this wrong would have meant every correctly-billed Parker Grant day
  showing up as a false "wrong payer" exception, so the parser
  categorizes off Charge Code/Description, falling back to Payer Code
  only if neither is recognized (`app/payer_categories.py`).
- **Weekly Attendance is often `.ods`, not `.xlsx`**, and real rosters
  include an internal ID inline in the name, e.g. "Arocho, Leonidas
  (PAM-6)" -- present on Attendance and PCC batch rows, but usually
  *not* on Rate Master rows. When present, that ID is used as the
  primary match key (far more reliable than name matching); Rate Master
  rows without one fall back to last-name matching.
- **A "Trials:" section, if present, marks the end of the real roster**
  on the Attendance sheet -- trial participants are never billed in
  PCC, so anything listed under it is excluded from the
  MISSING_FROM_BATCH check (otherwise every trial participant would
  permanently show as a false exception).
- **Real rosters do have two different participants sharing a last
  name** (a real sample file had two "Goldstein"s, two "Johnson"s, two
  "Rivera"s, two "Murphy"s). Silently merging same-surname rows by last
  name alone risked corrupting one person's attendance with another's
  when both got written under the same identity. Matching now keeps
  them as **separate** participants unless the first names look
  compatible (exact, blank/enriching a bare record, a nickname-style
  prefix/suffix match like "Fran"/"Frances" or "Fred"/"Alfred", or a
  near-identical spelling like "Mohamed"/"Mohammed"); anything else
  splits into two records with a warning rather than guessing. Genuine
  nicknames unrelated by spelling (Joe/Joseph, Bob/Robert) aren't
  caught and will show as two records needing a manual fix (rename one
  to match, or just be aware the exceptions on both refer to one
  person) -- this is a hard limitation of last-name-only source data,
  not something a name-matching heuristic can fully solve.
- **The Rate Master's "Discount" column is where the real exception
  text lives**, not a column literally named "notes" or "exception" --
  and real workbooks carry a legacy sheet (e.g. "Old Rates") alongside
  the current one, so sheet selection actively scores candidates
  (preferring a name containing "new"/"current") rather than blindly
  using the first sheet.
- **Rotation rules are genuinely diverse free text**: "4th day is
  Parker Grant" (3-then-1), "Three days private, then two days Title
  III" (3-then-2, and not always a grant), "2nd and 3rd day are Title
  III" (position-based, no "then"), "1 day VA - 1 day private pay per
  week" (alternating between two real payers, not a private/grant
  split). The parser detects an explicit day-count pattern where one
  exists and represents it as (primary days, secondary days, secondary
  payer) rather than assuming a single grant day; text with no day
  count at all (e.g. "Full PG while appealing Medicaid denial" -- a
  temporary status note, not a rotation) is deliberately left
  unparsed rather than guessed at. Every auto-detected rule -- and every
  exception note that wasn't auto-parsed -- is surfaced as an upload
  warning and should be spot-checked on the **Rate Master** screen.
- **Rotation math needs continuous attendance history to be accurate.**
  The "day 4 of every 4 bills to the grant" count is based on
  cumulative *attended* days recorded in this system, not PCC's own
  (longer) history. On a fresh deployment with only a week or two of
  attendance loaded, a participant's real position in their rotation
  cycle (as PCC already knows it) won't yet match what this app
  computes, and GRANT_RULE_NOT_APPLIED exceptions on rotating-payer
  participants may be false positives until enough attendance history
  has accumulated (their whole cycle length's worth). This isn't a bug
  to fix in code -- it self-corrects once the system has been the
  system of record for a full cycle or more.

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
  per-user accounts (no shared logins). A temporary password issued by
  an admin only gets its holder as far as the change-password screen
  (`/account/password`); every other page redirects there until they've
  chosen their own. Admins can issue a new temporary password from
  `/admin/users` when someone is locked out or has forgotten theirs --
  there is deliberately no email-based reset, since a reset link sitting
  in a mailbox is a way into PHI.
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
