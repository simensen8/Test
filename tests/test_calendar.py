"""Month views: the program's, and one participant's."""
import datetime

from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    AttendanceSchedule,
    AttendanceSource,
    BillingRecord,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import run_reconciliation

SEPT = datetime.date(2026, 9, 1)
MONDAY = datetime.date(2026, 9, 7)


def _person(db, name, days="0,1,2,3,4"):
    participant, _, _ = get_or_create_participant(db, name)
    if days:
        db.add(AttendanceSchedule(participant_id=participant.id, days_of_week=days))
    db.flush()
    return participant


def _attend(db, participant, day, attended=True):
    db.add(AttendanceRecord(participant_id=participant.id, date=day, attended=attended,
                            source=AttendanceSource.CHECK_IN))
    db.flush()


def test_the_month_shows_how_many_were_here_each_day(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice")
    bob = _person(db, "Brown, Bob")
    _attend(db, alice, MONDAY)
    _attend(db, bob, MONDAY, attended=False)
    db.commit()

    body = client.get("/calendar?month=2026-09").text
    assert "September 2026" in body
    assert "1</strong> here, 1 out" in body


def test_a_reconciled_day_is_marked_as_such(client, db_session):
    db = db_session
    user = db.scalar(select(User))
    alice = _person(db, "Adams, Alice (PAM-1)")
    _attend(db, alice, MONDAY)
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=MONDAY)
    db.add(upload)
    db.flush()
    db.add(BillingRecord(raw_name="Adams, Alice (PAM-1)", date=MONDAY, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    def grid(body: str) -> str:
        return body[:body.index("</tbody>")]  # the month itself, not the legend below it

    # Before reconciling: a batch waiting to be reviewed.
    assert "batch to review" in grid(client.get("/calendar?month=2026-09").text)

    run_reconciliation(db, MONDAY, user.id)
    db.commit()

    cells = grid(client.get("/calendar?month=2026-09").text)
    assert "reconciled" in cells
    assert "batch to review" not in cells


def test_open_exceptions_are_counted_on_the_day(client, db_session):
    db = db_session
    user = db.scalar(select(User))
    alice = _person(db, "Adams, Alice (PAM-1)")
    _attend(db, alice, MONDAY, attended=False)
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=MONDAY)
    db.add(upload)
    db.flush()
    db.add(BillingRecord(raw_name="Adams, Alice (PAM-1)", date=MONDAY, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    run_reconciliation(db, MONDAY, user.id)
    db.commit()

    assert "1 open" in client.get("/calendar?month=2026-09").text


def test_months_are_navigable_and_a_bad_one_is_refused(client, db_session):
    body = client.get("/calendar?month=2026-09").text
    assert "2026-08" in body and "2026-10" in body

    resp = client.get("/calendar?month=nonsense", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/calendar"


def test_a_year_boundary_steps_correctly(client, db_session):
    body = client.get("/calendar?month=2026-01").text
    assert "2025-12" in body
    assert "December 2025" not in body  # still showing January
    assert "January 2026" in body

    december = client.get("/calendar?month=2026-12").text
    assert "2027-01" in december


def test_a_participants_month_shows_their_pattern(client, db_session):
    db = db_session
    alice = _person(db, "Adams, Alice", days="0,2")  # Mondays and Wednesdays
    _attend(db, alice, MONDAY)
    _attend(db, alice, MONDAY + datetime.timedelta(days=2), attended=False)
    db.commit()

    body = client.get(f"/participants/{alice.id}/calendar?month=2026-09").text
    assert "Adams, Alice" in body
    assert "attended 1 day" in body
    assert "out on 1 scheduled day" in body
    assert "expected Mon, Wed" in body


def test_an_unmarked_scheduled_day_in_the_past_is_visible(client, db_session):
    """A day someone was expected and nobody recorded anything is the
    gap worth seeing -- it's what produces a missing billing line."""
    db = db_session
    past_monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday() + 7)
    alice = _person(db, "Adams, Alice", days=str(past_monday.weekday()))
    db.commit()

    body = client.get(f"/participants/{alice.id}/calendar?month={past_monday.strftime('%Y-%m')}").text
    assert "expected, not marked" in body
