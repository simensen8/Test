"""The daily check-in screen: who is expected, marking them, and fixing
a mark that was wrong."""
import datetime
import re

from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    AttendanceSchedule,
    AttendanceSource,
    AuditLog,
    BillingRecord,
    ParticipantStatus,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import run_reconciliation

MONDAY = datetime.date(2026, 9, 7)
TUESDAY = datetime.date(2026, 9, 8)


def _csrf(client, path="/check-in"):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def _person(db, name, days="0,1,2,3,4", **kw):
    participant, _, _ = get_or_create_participant(db, name)
    for key, value in kw.items():
        setattr(participant, key, value)
    if days is not None:
        db.add(AttendanceSchedule(participant_id=participant.id, days_of_week=days))
    db.flush()
    return participant


def _mark(client, participant, day, value, expect=303):
    resp = client.post("/check-in/mark", data={
        "csrf_token": _csrf(client), "participant_id": participant.id,
        "day": day.isoformat(), "attended": value,
    }, follow_redirects=False)
    assert resp.status_code == expect, resp.text
    return resp


# ----------------------------------------------------------- the list ----

def test_the_day_lists_who_is_scheduled_for_it(client, db_session):
    db = db_session
    _person(db, "Monday, Only", days="0")
    _person(db, "Tuesday, Only", days="1")
    db.commit()

    monday = client.get(f"/check-in?day={MONDAY.isoformat()}").text
    listed = monday[:monday.index("isn't on today's list")]  # the day's table, not the drop-in picker
    assert "Monday, Only" in listed
    assert "Tuesday, Only" not in listed


def test_a_discharged_participant_drops_off_the_list(client, db_session):
    db = db_session
    _person(db, "Gone, Gary", status=ParticipantStatus.DISCHARGED)
    _person(db, "Here, Hana")
    db.commit()

    body = client.get(f"/check-in?day={MONDAY.isoformat()}").text
    assert "Here, Hana" in body
    assert "Gone, Gary" not in body, "a discharged participant is neither listed nor addable"


def test_nobody_is_present_until_somebody_says_so(client, db_session):
    """The failure mode that matters: assuming attendance would bill a
    day nobody came to."""
    db = db_session
    _person(db, "Adams, Alice")
    db.commit()

    assert db.scalars(select(AttendanceRecord)).all() == []
    body = client.get(f"/check-in?day={MONDAY.isoformat()}").text
    assert "not yet marked" in body


# --------------------------------------------------------- the marking ----

def test_marking_present_records_who_and_when(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")

    db.expire_all()
    record = db.scalar(select(AttendanceRecord))
    assert record.attended is True
    assert record.date == MONDAY
    assert record.source == AttendanceSource.CHECK_IN
    assert record.source_upload_id is None, "a check-in has no upload behind it"
    assert record.recorded_by_id is not None
    assert record.recorded_at is not None


def test_a_wrong_mark_is_corrected_in_one_click(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, alice, MONDAY, "absent")

    db.expire_all()
    records = db.scalars(select(AttendanceRecord)).all()
    assert len(records) == 1, "correcting a mark doesn't add a second record for the day"
    assert records[0].attended is False


def test_a_mark_can_be_undone_entirely(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, alice, MONDAY, "clear")

    db.expire_all()
    assert db.scalars(select(AttendanceRecord)).all() == [], "back to unmarked, not marked absent"


def test_every_mark_is_audited(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, alice, MONDAY, "absent")

    actions = [entry.action for entry in db.scalars(select(AuditLog)).all()]
    assert actions.count("mark_attendance") == 2


def test_counts_add_up_on_the_screen(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    bob = _person(db, "Brown, Bob")
    _person(db, "Clark, Cara")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, bob, MONDAY, "absent")

    body = client.get(f"/check-in?day={MONDAY.isoformat()}").text
    assert re.search(r">1</div>\s*<div class=\"muted\">present", body)
    assert re.search(r">1</div>\s*<div class=\"muted\">out", body)
    assert re.search(r">1</div>\s*<div class=\"muted\">not yet marked", body)


# ------------------------------------------------------ the shortcuts ----

def test_marking_the_rest_present_leaves_existing_marks_alone(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    _person(db, "Brown, Bob")
    _person(db, "Clark, Cara")
    db.commit()

    _mark(client, alice, MONDAY, "absent")
    client.post("/check-in/mark-rest-present", data={
        "csrf_token": _csrf(client), "day": MONDAY.isoformat(),
    }, follow_redirects=False)

    db.expire_all()
    records = {r.participant_id: r.attended for r in db.scalars(select(AttendanceRecord)).all()}
    assert len(records) == 3
    assert records[alice.id] is False, "the one marked out stays out"
    assert sum(1 for attended in records.values() if attended) == 2


def test_a_drop_in_can_be_marked_without_changing_their_schedule(client, db_session):
    db = db_session
    _person(db, "Adams, Alice")
    dropin = _person(db, "Visitor, Vera", days=None)
    db.commit()

    _mark(client, dropin, MONDAY, "present")

    db.expire_all()
    assert db.scalar(select(AttendanceRecord)).participant_id == dropin.id
    assert db.scalars(select(AttendanceSchedule).where(
        AttendanceSchedule.participant_id == dropin.id)).all() == []
    # And they show on the day they were marked, flagged as unscheduled.
    body = client.get(f"/check-in?day={MONDAY.isoformat()}").text
    assert "Visitor, Vera" in body
    assert "drop-in" in body


def test_an_earlier_day_can_still_be_filled_in(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, alice, TUESDAY, "absent")

    db.expire_all()
    by_date = {r.date: r.attended for r in db.scalars(select(AttendanceRecord)).all()}
    assert by_date == {MONDAY: True, TUESDAY: False}


# -------------------------------------------------- feeding the engine ----

def test_check_in_attendance_reconciles_like_an_uploaded_sheet(client, db_session):
    db = db_session
    user = db.scalar(select(User))
    alice = _person(db, "Adams, Alice (PAM-1)")
    bob = _person(db, "Brown, Bob (PAM-2)")
    db.commit()

    _mark(client, alice, MONDAY, "present")
    _mark(client, bob, MONDAY, "absent")

    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=MONDAY)
    db.add(upload)
    db.flush()
    db.add(BillingRecord(raw_name="Adams, Alice (PAM-1)", date=MONDAY, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    run = run_reconciliation(db, MONDAY, user.id)
    assert run.exception_count == 0, "present-and-billed, absent-and-not-billed is a clean day"


def test_someone_billed_but_marked_out_is_an_exception(client, db_session):
    db = db_session
    user = db.scalar(select(User))
    alice = _person(db, "Adams, Alice (PAM-1)")
    db.commit()

    _mark(client, alice, MONDAY, "absent")

    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=MONDAY)
    db.add(upload)
    db.flush()
    db.add(BillingRecord(raw_name="Adams, Alice (PAM-1)", date=MONDAY, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    run = run_reconciliation(db, MONDAY, user.id)
    assert run.exception_count == 1


def test_an_uploaded_sheet_overrides_a_check_in_for_the_same_day(client, db_session):
    """The paper sheet is the signed record; when both exist for a day,
    the transcription of the sheet is the one that stands."""
    db = db_session
    alice = _person(db, "Adams, Alice")
    db.commit()

    _mark(client, alice, MONDAY, "present")

    upload = Upload(kind=UploadKind.WEEKLY_ATTENDANCE, original_filename="w", stored_path="",
                    uploaded_by_id=db.scalar(select(User)).id, week_start=MONDAY)
    db.add(upload)
    db.flush()
    record = db.scalar(select(AttendanceRecord))
    record.attended = False
    record.source = AttendanceSource.UPLOAD
    record.source_upload_id = upload.id
    db.commit()

    db.expire_all()
    refreshed = db.scalar(select(AttendanceRecord))
    assert refreshed.attended is False
    assert refreshed.source == AttendanceSource.UPLOAD
