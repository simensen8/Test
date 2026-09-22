"""The reported false exceptions: two participants with the same last
name and near-identical first names ("Hall, Tresea" and "Hall, Tressa")
both attended and were both billed correctly, and the day still raised
two exceptions -- one read as billed-but-absent, the other as
attended-but-unbilled.

The attendance upload split them into two records (correctly: the names
are 83% alike, below the threshold for treating them as one person), but
the billing side resolved by surname alone and put both batch rows on
whichever record happened to hold the surname slot.
"""
import datetime

from sqlalchemy import select

from app.matching import fuzzy_find_participant, get_or_create_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    Exception_,
    Role,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import run_reconciliation
from app.security import hash_password

DAY = datetime.date(2026, 9, 10)


def _fixtures(db):
    user = User(email="t@example.org", display_name="T", password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(user)
    db.flush()
    att = Upload(kind=UploadKind.WEEKLY_ATTENDANCE, original_filename="a", stored_path="",
                 uploaded_by_id=user.id, week_start=DAY)
    pcc = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                 uploaded_by_id=user.id, batch_date=DAY)
    db.add_all([att, pcc])
    db.flush()
    return user, att, pcc


def test_each_sister_gets_her_own_billing_row(db_session):
    db = db_session
    user, att, pcc = _fixtures(db)

    tresea, _, _ = get_or_create_participant(db, "Hall, Tresea")
    tressa, _, warning = get_or_create_participant(db, "Hall, Tressa")
    assert tresea.id != tressa.id, "near-identical first names stay separate people"
    assert warning, "and the split is flagged for a human to confirm"

    for participant in (tresea, tressa):
        db.add(AttendanceRecord(participant_id=participant.id, date=DAY, attended=True,
                                source_upload_id=att.id))
    for name in ("Hall, Tresea", "Hall, Tressa"):
        db.add(BillingRecord(raw_name=name, date=DAY, payer_source="Private Pay",
                             source_upload_id=pcc.id, verified=True))
    db.commit()

    run = run_reconciliation(db, DAY, user.id)
    assert run.exception_count == 0, [
        (e.reason.value, e.participant_name_snapshot) for e in db.scalars(select(Exception_)).all()
    ]

    rows = db.scalars(select(BillingRecord)).all()
    assert {r.participant_id for r in rows} == {tresea.id, tressa.id}, \
        "each batch row belongs to the person it names"


def test_a_batch_row_matches_the_sister_it_names(db_session):
    db = db_session
    tresea, _, _ = get_or_create_participant(db, "Hall, Tresea")
    tressa, _, _ = get_or_create_participant(db, "Hall, Tressa")
    db.commit()

    assert fuzzy_find_participant(db, "Hall, Tressa").participant.id == tressa.id
    assert fuzzy_find_participant(db, "Hall, Tresea").participant.id == tresea.id


def test_the_facility_id_still_wins_over_the_name(db_session):
    db = db_session
    judith, _, _ = get_or_create_participant(db, "Goldstein, Judith (PAM-20)")
    marvin, _, _ = get_or_create_participant(db, "Goldstein, Marvin (PAM-215)")
    db.commit()

    # Even with the first name misspelt, the ID settles it.
    assert fuzzy_find_participant(db, "Goldstein, Marvyn (PAM-215)").participant.id == marvin.id
    assert fuzzy_find_participant(db, "Goldstein, Judith (PAM-20)").participant.id == judith.id


def test_a_surname_with_nobody_matching_stays_unmatched(db_session):
    """A batch row for a person the roster has only under a clearly
    different first name is left for a human, and the near-miss is named
    so the review screen can show it."""
    db = db_session
    get_or_create_participant(db, "Hall, Tresea")
    db.commit()

    result = fuzzy_find_participant(db, "Hall, Bartholomew")
    assert result.participant is None
    assert result.candidate_name == "Hall, Tresea"


def test_a_nickname_still_resolves_to_one_person(db_session):
    db = db_session
    frances, created, _ = get_or_create_participant(db, "Johnson, Frances")
    again, created_again, _ = get_or_create_participant(db, "Johnson, Fran")
    assert created and not created_again
    assert again.id == frances.id
    db.commit()

    assert fuzzy_find_participant(db, "Johnson, Fran").participant.id == frances.id
