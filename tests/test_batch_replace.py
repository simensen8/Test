import datetime

from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    Exception_,
    ReconciliationRun,
    Role,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import run_reconciliation
from app.routers.uploads import clear_batch_for_date
from app.security import hash_password

DAY = datetime.date(2026, 9, 10)


def _user(db):
    u = User(email="t@example.org", display_name="T", password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(u)
    db.flush()
    return u


def _upload(db, user, kind, **kw):
    up = Upload(kind=kind, original_filename="f", stored_path="", uploaded_by_id=user.id, **kw)
    db.add(up)
    db.flush()
    return up


def _billing(db, upload, participant, name, payer="Private Pay"):
    row = BillingRecord(
        raw_name=name, date=DAY, payer_source=payer, source_upload_id=upload.id,
        verified=True, participant_id=participant.id if participant else None,
    )
    db.add(row)
    db.flush()
    return row


def test_clear_removes_billing_rows_for_that_date_only(db_session):
    db = db_session
    user = _user(db)
    up = _upload(db, user, UploadKind.PCC_BATCH, batch_date=DAY)
    participant, _, _ = get_or_create_participant(db, "Adams")

    _billing(db, up, participant, "Adams, Alice")
    other_day = BillingRecord(
        raw_name="Adams, Alice", date=DAY + datetime.timedelta(days=1),
        payer_source="Private Pay", source_upload_id=up.id, verified=True,
    )
    db.add(other_day)
    db.commit()

    removed_rows, removed_exceptions = clear_batch_for_date(db, DAY)
    db.commit()

    assert removed_rows == 1
    assert removed_exceptions == 0
    remaining = db.scalars(select(BillingRecord)).all()
    assert len(remaining) == 1
    assert remaining[0].date == DAY + datetime.timedelta(days=1)


def test_clear_removes_stale_reconciliation_that_referenced_those_rows(db_session):
    """An exception row carries a foreign key to the billing row it was
    raised against. Clearing the batch without clearing the prior
    reconciliation would leave those keys pointing at deleted rows."""
    db = db_session
    user = _user(db)
    att_upload = _upload(db, user, UploadKind.WEEKLY_ATTENDANCE, week_start=DAY)
    pcc_upload = _upload(db, user, UploadKind.PCC_BATCH, batch_date=DAY)

    participant, _, _ = get_or_create_participant(db, "Evans")
    db.add(AttendanceRecord(
        participant_id=participant.id, date=DAY, attended=False, source_upload_id=att_upload.id,
    ))
    _billing(db, pcc_upload, participant, "Evans, Eve")
    db.commit()

    run = run_reconciliation(db, DAY, user.id)
    assert run.exception_count == 1  # billed but absent

    removed_rows, removed_exceptions = clear_batch_for_date(db, DAY)
    db.commit()

    assert removed_rows == 1
    assert removed_exceptions == 1
    assert db.scalars(select(Exception_)).all() == []
    assert db.scalars(select(ReconciliationRun)).all() == []
    assert db.scalars(select(BillingRecord)).all() == []

    # Attendance is untouched -- only the batch side is being replaced.
    assert len(db.scalars(select(AttendanceRecord)).all()) == 1


def test_clear_keeps_the_upload_row_for_audit(db_session):
    db = db_session
    user = _user(db)
    up = _upload(db, user, UploadKind.PCC_BATCH, batch_date=DAY)
    participant, _, _ = get_or_create_participant(db, "Frost")
    _billing(db, up, participant, "Frost, Fiona")
    db.commit()

    clear_batch_for_date(db, DAY)
    db.commit()

    uploads = db.scalars(select(Upload).where(Upload.kind == UploadKind.PCC_BATCH)).all()
    assert len(uploads) == 1, "the record of what was uploaded must survive for audit"


def test_reupload_does_not_produce_duplicate_exceptions(db_session):
    """The actual reported bug: uploading a day twice made every
    participant reconcile as a duplicate billing entry."""
    db = db_session
    user = _user(db)
    att_upload = _upload(db, user, UploadKind.WEEKLY_ATTENDANCE, week_start=DAY)
    participant, _, _ = get_or_create_participant(db, "Adams")
    db.add(AttendanceRecord(
        participant_id=participant.id, date=DAY, attended=True, source_upload_id=att_upload.id,
    ))

    first = _upload(db, user, UploadKind.PCC_BATCH, batch_date=DAY)
    _billing(db, first, participant, "Adams, Alice")
    db.commit()

    # Second upload of the same day, the way the route now handles it.
    clear_batch_for_date(db, DAY)
    second = _upload(db, user, UploadKind.PCC_BATCH, batch_date=DAY)
    _billing(db, second, participant, "Adams, Alice")
    db.commit()

    assert len(db.scalars(select(BillingRecord).where(BillingRecord.date == DAY)).all()) == 1

    run = run_reconciliation(db, DAY, user.id)
    assert run.exception_count == 0, "a correctly re-uploaded day should reconcile clean"
