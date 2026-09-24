"""Checking the rate on a participant's profile against what PCC charged.

The profile is the single source of truth for how someone is billed, so
a charge that disagrees with it is worth a look: either the charge is
wrong or the profile is out of date. Both happen -- on the program's own
files, two Medicaid participants are on file at $94.66 while PCC bills
$95.96, which is a rate that moved and a workbook that didn't.

What this does not do is flag cases where the two figures aren't
comparable. Those are the tests at the bottom, and they matter more than
the positive cases: a check that cries wolf on every rotation day would
be turned off within a week.
"""
import datetime

from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    AttendanceSource,
    BillingRecord,
    Exception_,
    ExceptionReason,
    GrantRuleType,
    RateRule,
    Role,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import run_reconciliation
from app.security import hash_password

DAY = datetime.date(2026, 9, 10)


def _setup(db, *, rate, billed, payer="Private Pay", billed_payer=None, rule_kwargs=None):
    """One participant who attended, with a rate on file and a charge."""
    user = User(email="t@example.org", display_name="T",
                password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(user)
    db.flush()
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=DAY)
    db.add(upload)
    db.flush()

    person, _, _ = get_or_create_participant(db, "Adams, Alice")
    if rate is not None or rule_kwargs:
        db.add(RateRule(participant_id=person.id, payer_source=payer, rate=rate,
                        **(rule_kwargs or {})))
    db.add(AttendanceRecord(participant_id=person.id, date=DAY, attended=True,
                            source=AttendanceSource.CHECK_IN))
    db.add(BillingRecord(raw_name="Adams, Alice", date=DAY,
                         payer_source=billed_payer or payer, amount=billed,
                         source_upload_id=upload.id, verified=True))
    db.commit()
    return user, person


def _rate_exceptions(db):
    return db.scalars(
        select(Exception_).where(Exception_.reason == ExceptionReason.WRONG_RATE)
    ).all()


# --------------------------------------------------------- it flags ----

def test_a_charge_that_differs_from_the_profile_is_flagged(db_session):
    db = db_session
    user, _ = _setup(db, rate=94.66, billed=95.96, payer="Medicaid")

    run_reconciliation(db, DAY, user.id)

    found = _rate_exceptions(db)
    assert len(found) == 1
    assert found[0].expected_billing == "$94.66"
    assert found[0].actual_billing == "$95.96"
    assert "out of date" in found[0].detail


def test_a_charge_that_matches_raises_nothing(db_session):
    db = db_session
    user, _ = _setup(db, rate=95.00, billed=95.00)

    run = run_reconciliation(db, DAY, user.id)

    assert run.exception_count == 0


def test_a_difference_of_one_cent_is_still_a_difference(db_session):
    db = db_session
    user, _ = _setup(db, rate=95.00, billed=95.01)

    run_reconciliation(db, DAY, user.id)

    assert len(_rate_exceptions(db)) == 1


# ------------------------------------------------- it stays quiet ----

def test_no_rate_on_file_means_nothing_to_compare(db_session):
    """Most participants have no rate recorded -- they are the Private
    Pay default. They must not all become exceptions."""
    db = db_session
    user, _ = _setup(db, rate=None, billed=95.00)

    run = run_reconciliation(db, DAY, user.id)

    assert run.exception_count == 0
    assert _rate_exceptions(db) == []


def test_a_batch_line_with_no_amount_is_not_flagged(db_session):
    db = db_session
    user, _ = _setup(db, rate=95.00, billed=None)

    assert _rate_exceptions(db) == []
    run_reconciliation(db, DAY, user.id)
    assert _rate_exceptions(db) == []


def test_a_rotation_day_is_not_flagged_on_the_primary_rate(db_session):
    """On a grant day the correct charge is the secondary payer's, which
    the profile doesn't carry. Comparing the primary rate would flag
    every rotation, every cycle."""
    db = db_session
    user, person = _setup(
        db, rate=95.00, billed=46.00, payer="Private Pay", billed_payer="Title III",
        rule_kwargs={"grant_rule_type": GrantRuleType.ROTATING_GRANT,
                     "grant_cycle_length": 1, "grant_cycle_secondary_days": 1,
                     "grant_payer": "Title III"},
    )
    # Two attended days puts this one at position 2 of the cycle: a
    # secondary-payer day, billed correctly as Title III.
    db.add(AttendanceRecord(participant_id=person.id, date=DAY - datetime.timedelta(days=1),
                            attended=True, source=AttendanceSource.CHECK_IN))
    db.commit()

    run_reconciliation(db, DAY, user.id)

    assert _rate_exceptions(db) == [], "a rotation day is not a rate discrepancy"


def test_a_wrong_payer_is_not_also_reported_as_a_wrong_rate(db_session):
    """The payer exception already describes the problem; the rate is
    wrong only because of it."""
    db = db_session
    user, _ = _setup(db, rate=95.00, billed=46.00, payer="Private Pay", billed_payer="Title III")

    run_reconciliation(db, DAY, user.id)

    reasons = [e.reason for e in db.scalars(select(Exception_)).all()]
    assert ExceptionReason.WRONG_PAYER in reasons
    assert ExceptionReason.WRONG_RATE not in reasons


def test_a_settled_rate_finding_survives_a_re_run(db_session):
    """Sliding-scale private pay is reconciled by hand, so a reviewer
    marking one 'not an error' must not see it reopen."""
    from app.models import ExceptionStatus

    db = db_session
    user, _ = _setup(db, rate=78.75, billed=95.00)

    run_reconciliation(db, DAY, user.id)
    finding = _rate_exceptions(db)[0]
    finding.status = ExceptionStatus.NOT_AN_ERROR
    finding.resolution_notes = "Sliding scale -- Sharelle applies the discount separately."
    db.commit()

    run_reconciliation(db, DAY, user.id)
    db.expire_all()

    settled = _rate_exceptions(db)
    assert len(settled) == 1
    assert settled[0].status == ExceptionStatus.NOT_AN_ERROR
    assert "Sharelle" in settled[0].resolution_notes
