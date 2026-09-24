"""The Billing week and the operational dashboard.

Both screens exist to answer "what is waiting on me", so the tests are
mostly about that: for a given state of a day, does the app name the
right next step, and does it stop naming one once the day is done.

The figures on the dashboard are checked against what was actually
loaded rather than against a fixture constant, so a test fails if a
number starts being derived differently.
"""
import datetime

import pytest
from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    AttendanceSchedule,
    BillingRecord,
    Exception_,
    ExceptionReason,
    ExceptionStatus,
    Participant,
    ReconciliationRun,
    Upload,
    UploadKind,
    User,
)

DAY = datetime.date(2026, 9, 10)


def _upload(db, day=DAY) -> Upload:
    user = db.scalar(select(User))
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="batch.html", stored_path="",
                    uploaded_by_id=user.id, batch_date=day)
    db.add(upload)
    db.flush()
    return upload


def _row(db, name="Adams, Alice", day=DAY, *, verified=True, amount=95.00) -> BillingRecord:
    upload = _upload(db, day)
    person, _, _ = get_or_create_participant(db, name)
    row = BillingRecord(raw_name=name, date=day, payer_source="Private Pay", amount=amount,
                        participant_id=person.id, source_upload_id=upload.id, verified=verified)
    db.add(row)
    db.flush()
    return row


def _run(db, day=DAY, *, open_exceptions=0) -> ReconciliationRun:
    user = db.scalar(select(User))
    run = ReconciliationRun(date=day, run_by_id=user.id, exception_count=open_exceptions)
    db.add(run)
    db.flush()
    for i in range(open_exceptions):
        db.add(Exception_(
            run_id=run.id, date=day, participant_name_snapshot=f"Person {i}",
            reason=ExceptionReason.WRONG_PAYER, status=ExceptionStatus.OPEN,
        ))
    db.flush()
    return run


# ------------------------------------------------------- the next step ----

def test_a_day_with_no_batch_asks_for_the_batch(client, db_session):
    from app.routers.billing import day_state
    db_session.commit()
    assert day_state(db_session, DAY)["next_action"]["href"] == "/upload"


def test_a_day_with_unchecked_rows_asks_for_the_review_screen(client, db_session):
    from app.routers.billing import day_state
    _row(db_session, verified=False)
    db_session.commit()

    action = day_state(db_session, DAY)["next_action"]
    assert action["href"] == f"/batches/{DAY.isoformat()}/review"
    assert "1 row" in action["label"]


def test_a_checked_but_unreconciled_day_asks_to_reconcile(client, db_session):
    from app.routers.billing import day_state
    _row(db_session, verified=True)
    db_session.commit()

    assert day_state(db_session, DAY)["next_action"]["label"] == "Reconcile"


def test_a_reconciled_day_with_findings_asks_to_resolve_them(client, db_session):
    from app.routers.billing import day_state
    _row(db_session, verified=True)
    _run(db_session, open_exceptions=3)
    db_session.commit()

    action = day_state(db_session, DAY)["next_action"]
    assert action["href"] == f"/reports/{DAY.isoformat()}"
    assert "3" in action["label"]


def test_a_finished_day_asks_for_nothing(client, db_session):
    from app.routers.billing import day_state
    _row(db_session, verified=True)
    _run(db_session, open_exceptions=0)
    db_session.commit()

    assert day_state(db_session, DAY)["next_action"] is None


def test_a_resolved_exception_no_longer_counts_as_open(client, db_session):
    from app.routers.billing import day_state
    _row(db_session, verified=True)
    run = _run(db_session, open_exceptions=2)
    db_session.commit()

    settled = db_session.scalars(select(Exception_).where(Exception_.run_id == run.id)).first()
    settled.status = ExceptionStatus.RESOLVED
    db_session.commit()

    assert day_state(db_session, DAY)["open_exceptions"] == 1


def test_only_the_newest_run_for_a_day_is_reported(client, db_session):
    """Re-running a day leaves the earlier run behind in some paths; the
    grid must describe the latest one, not whichever came back first."""
    from app.routers.billing import day_state
    _row(db_session, verified=True)
    old = _run(db_session, open_exceptions=5)
    old.run_at = datetime.datetime(2026, 9, 10, 8, 0, 0)
    db_session.flush()
    new = _run(db_session, open_exceptions=1)
    new.run_at = datetime.datetime(2026, 9, 10, 17, 0, 0)
    db_session.commit()

    state = day_state(db_session, DAY)
    assert state["run"].id == new.id
    assert state["open_exceptions"] == 1


def _count_queries(db_session, work):
    """Run `work` and return every SQL statement it caused."""
    from sqlalchemy import event

    seen = []

    def record(conn, cursor, statement, *rest):
        seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


def test_a_week_is_read_in_a_handful_of_queries(client, db_session):
    """Answering a day at a time made ninety round trips for one page."""
    from app.routers.billing import day_states
    _row(db_session, verified=True)
    _run(db_session, open_exceptions=1)
    db_session.commit()

    seen = _count_queries(db_session, lambda: day_states(
        db_session, [DAY + datetime.timedelta(days=n) for n in range(14)]))
    assert len(seen) <= 8, f"{len(seen)} queries for a fortnight"


def test_the_dashboard_itself_does_not_query_day_by_day(client, db_session):
    """The regression that matters.

    An earlier version of this file only measured `day_states`, which was
    bulk all along -- while the dashboard still called it once per day
    and issued 68 queries for one page. Measuring the helper proved
    nothing about the page, so this drives the real route.
    """
    today = datetime.date.today()
    for n in range(14):
        day = today - datetime.timedelta(days=n)
        if day.weekday() > 4:
            continue
        _row(db_session, f"Person{n}, Test", day, verified=True)
    db_session.commit()

    seen = _count_queries(db_session, lambda: client.get("/dashboard"))
    assert len(seen) <= 25, f"{len(seen)} queries to render the dashboard"


# ------------------------------------------------------------- pages ------

def test_the_billing_week_shows_the_days_and_their_state(client, db_session):
    _row(db_session, verified=False)
    db_session.commit()

    page = client.get(f"/billing?week={DAY.isoformat()}").text
    assert "Thu, Sep 10" in page
    assert f"/batches/{DAY.isoformat()}/review" in page


def test_the_billing_week_falls_back_to_this_week_on_a_bad_date(client):
    assert client.get("/billing?week=not-a-date").status_code == 200


def test_the_billing_week_always_starts_on_a_monday(client):
    """Whatever day is asked for, the week shown is the one containing
    it -- a grid that started on a Wednesday would quietly hide Monday's
    unbilled day."""
    page = client.get("/billing?week=2026-09-10").text  # a Thursday
    assert "week of September 7, 2026" in page


def test_the_dashboard_totals_what_the_batches_actually_billed(client, db_session):
    today = datetime.date.today()
    _row(db_session, "Adams, Alice", today, amount=95.00)
    _row(db_session, "Brown, Bob", today, amount=78.75)
    db_session.commit()

    page = client.get("/dashboard").text
    assert "$173.75" in page


def test_the_dashboard_says_nothing_rather_than_zero_with_no_batches(client, db_session):
    db_session.commit()
    page = client.get("/dashboard").text
    assert "No PCC batch has been uploaded for this month yet" in page
    assert "$0.00" not in page


def test_the_dashboard_counts_todays_check_in_against_who_was_scheduled(client, db_session):
    today = datetime.date.today()
    people = []
    for name in ("Adams, Alice", "Brown, Bob", "Chen, Carol"):
        person, _, _ = get_or_create_participant(db_session, name)
        db_session.add(AttendanceSchedule(participant_id=person.id, days_of_week="0,1,2,3,4,5,6"))
        people.append(person)
    db_session.add(AttendanceRecord(participant_id=people[0].id, date=today, attended=True))
    db_session.add(AttendanceRecord(participant_id=people[1].id, date=today, attended=False))
    db_session.commit()

    page = client.get("/dashboard").text
    assert "/ 3" in page              # three expected
    assert "still unmarked" in page   # Carol

    from app.routers.dashboard import _today_panel
    panel = _today_panel(db_session, today)
    assert (panel["expected"], panel["present"], panel["absent"], panel["unmarked"]) == (3, 1, 1, 1)


def test_someone_not_scheduled_today_is_not_counted_as_missing(client, db_session):
    """Only people the schedule expects today are counted; otherwise the
    unmarked figure is the whole roster every morning."""
    from app.routers.dashboard import _today_panel
    today = datetime.date.today()
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    weekday = today.weekday()
    db_session.add(AttendanceSchedule(
        participant_id=person.id,
        days_of_week=",".join(str(d) for d in range(7) if d != weekday),
    ))
    db_session.commit()

    assert _today_panel(db_session, today)["expected"] == 0


def test_the_attention_list_skips_a_day_with_nothing_behind_it(client, db_session):
    """A day the program was closed has no billing to do. Listing it as
    outstanding would teach people to ignore the list."""
    from app.routers.dashboard import _needs_attention
    today = datetime.date.today()
    db_session.commit()

    assert _needs_attention(db_session, today) == []


def test_the_attention_list_surfaces_a_day_left_unreconciled(client, db_session):
    from app.routers.dashboard import _needs_attention
    today = datetime.date.today()
    day = today - datetime.timedelta(days=1)
    while day.weekday() > 4:
        day -= datetime.timedelta(days=1)
    _row(db_session, day=day, verified=True)
    db_session.commit()

    outstanding = _needs_attention(db_session, today)
    assert [d["date"] for d in outstanding] == [day]
    assert outstanding[0]["next_action"]["label"] == "Reconcile"


@pytest.mark.parametrize("path", ["/billing", "/dashboard"])
def test_both_screens_load_for_a_reviewer(path, db_session):
    """Neither is an administrator's screen: the daily job needs both."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Role
    from app.security import hash_password

    db_session.add(User(email="r@example.org", display_name="Reviewer",
                        password_hash=hash_password("a reviewer password"),
                        role=Role.REVIEWER, must_change_password=False))
    db_session.commit()

    reviewer = TestClient(app)
    reviewer.post("/login", data={"email": "r@example.org", "password": "a reviewer password"},
                  follow_redirects=False)
    assert reviewer.get(path).status_code == 200


def test_the_rate_master_is_still_reachable_from_the_roster(client, db_session):
    """It left the navigation, it was not deleted: a cross-roster read of
    every payer arrangement is still one click from the roster."""
    get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    assert '/rate-master' in client.get("/participants").text
    assert client.get("/rate-master").status_code == 200
    assert isinstance(db_session.scalar(select(Participant)), Participant)
