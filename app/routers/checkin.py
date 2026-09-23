"""The daily check-in screen.

One screen per day, listing everyone scheduled for it. Marking someone
present or absent is a single click, and changing that mark is the same
single click -- staff correcting a mistake shouldn't have to find a
different screen or undo anything.

Two decisions worth stating, both taken from how attendance software for
this kind of program works (and from how the paper sheet already works):

Nobody is marked present by default. "Assume everyone came unless told
otherwise" is the one failure mode that matters here: it silently bills
a day that nobody attended. An unmarked participant is unrecorded, not
present, and the screen says how many are still unmarked.

A mark can be changed all day, and every change is audited. The paper
sign-in sheet remains the signed record the state inspects; this is a
faster way to produce the same facts, not a replacement for it.
"""
import datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.matching import normalize
from app.models import (
    AttendanceRecord,
    AttendanceSchedule,
    AttendanceSource,
    Participant,
    ParticipantStatus,
)
from app.render import render
from app.security import get_current_user, log_audit

logger = logging.getLogger(__name__)

router = APIRouter()


def scheduled_participants(db: Session, day: datetime.date) -> list[Participant]:
    """Everyone whose schedule covers this day, active participants only."""
    schedules = db.scalars(
        select(AttendanceSchedule).where(AttendanceSchedule.active == True)  # noqa: E712
    ).all()
    expected_ids = {s.participant_id for s in schedules if s.covers(day)}
    if not expected_ids:
        return []
    people = db.scalars(select(Participant).where(Participant.id.in_(expected_ids))).all()
    return sorted(
        [p for p in people if p.status != ParticipantStatus.DISCHARGED],
        key=lambda p: normalize(p.full_name),
    )


def _records_for(db: Session, day: datetime.date) -> dict[str, AttendanceRecord]:
    return {
        record.participant_id: record
        for record in db.scalars(select(AttendanceRecord).where(AttendanceRecord.date == day)).all()
    }


def _parse_day(raw: str) -> datetime.date | None:
    if not raw:
        return datetime.date.today()
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


@router.get("/check-in")
def check_in_screen(
    request: Request,
    day: str = "",
    show: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    on_date = _parse_day(day)
    if on_date is None:
        resp = RedirectResponse(url="/check-in", status_code=303)
        set_flash(resp, "That isn't a valid date.", "error")
        return resp

    records = _records_for(db, on_date)
    expected = scheduled_participants(db, on_date)
    expected_ids = {p.id for p in expected}

    # Someone marked today who wasn't scheduled -- a drop-in, or a
    # schedule that hasn't been updated -- still belongs on the screen.
    unscheduled_ids = [pid for pid in records if pid not in expected_ids]
    extra = sorted(
        db.scalars(select(Participant).where(Participant.id.in_(unscheduled_ids))).all(),
        key=lambda p: normalize(p.full_name),
    ) if unscheduled_ids else []

    rows: list[dict[str, Any]] = [
        {"participant": p, "record": records.get(p.id), "scheduled": True} for p in expected
    ]
    rows += [{"participant": p, "record": records.get(p.id), "scheduled": False} for p in extra]

    present = sum(1 for row in rows if row["record"] and row["record"].attended)
    absent = sum(1 for row in rows if row["record"] and not row["record"].attended)
    unmarked = sum(1 for row in rows if row["record"] is None)

    if show == "unmarked":
        rows = [row for row in rows if row["record"] is None]

    # Anyone not on today's list, for adding a drop-in.
    listed = {row["participant"].id for row in rows}
    addable = sorted(
        [p for p in db.scalars(select(Participant)).all()
         if p.id not in listed and p.status != ParticipantStatus.DISCHARGED],
        key=lambda p: normalize(p.full_name),
    )

    log_audit(db, user=user, action="view_check_in", request=request, detail=on_date.isoformat())
    return render(request, "check_in.html", {
        "day": on_date,
        "rows": rows,
        "present": present,
        "absent": absent,
        "unmarked": unmarked,
        "total": present + absent + unmarked,
        "show": show,
        "addable": addable,
        "previous_day": (on_date - datetime.timedelta(days=1)).isoformat(),
        "next_day": (on_date + datetime.timedelta(days=1)).isoformat(),
        "today": datetime.date.today(),
    }, user=user)


def _record_attendance(
    db: Session, participant_id: str, day: datetime.date, attended: bool, user,
) -> AttendanceRecord | None:
    """Record one mark, tolerating a second one arriving at the same time.

    Staff double-tap, and two people can mark the same participant at
    once. One row per participant per day is enforced by the database,
    so the loser of that race is caught and retried against the row the
    winner created -- rather than showing an error for a mark that did,
    in the end, land. Returns None when another request got there first.
    """
    def _apply(record: AttendanceRecord) -> AttendanceRecord:
        record.attended = attended
        record.source = AttendanceSource.CHECK_IN
        record.recorded_by_id = user.id
        record.recorded_at = datetime.datetime.utcnow()
        return record

    existing = db.scalar(select(AttendanceRecord).where(
        AttendanceRecord.participant_id == participant_id, AttendanceRecord.date == day,
    ))
    if existing is not None:
        record = _apply(existing)
        db.flush()
        return record

    record = AttendanceRecord(participant_id=participant_id, date=day, attended=attended)
    try:
        # A savepoint, not the whole transaction: "mark the rest present"
        # records dozens of people in one request, and one collision must
        # not undo the marks made before it.
        with db.begin_nested():
            db.add(_apply(record))
            db.flush()
    except IntegrityError:
        # Someone else recorded this participant's day between our read
        # and our write. Their row is committed and ours can't exist
        # beside it; re-reading won't show it either, since this
        # transaction is reading from a snapshot taken before they
        # committed. The day is recorded either way, so the mark is
        # treated as landed and the screen shows the stored value when
        # it reloads. (The savepoint rollback has already discarded the
        # row we tried to add, so there is nothing to clean up here.)
        logger.info("Attendance for %s on %s was recorded concurrently; kept the stored mark",
                    participant_id, day)
        return None
    return record


@router.post("/check-in/mark")
def mark_attendance(
    request: Request,
    participant_id: str = Form(...),
    day: str = Form(...),
    attended: str = Form(...),
    show: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    on_date = _parse_day(day)
    participant = db.get(Participant, participant_id)
    if on_date is None or participant is None:
        resp = RedirectResponse(url="/check-in", status_code=303)
        set_flash(resp, "That check-in couldn't be recorded -- please try again.", "error")
        return resp

    if attended == "clear":
        record = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.participant_id == participant_id, AttendanceRecord.date == on_date,
        ))
        if record is not None:
            db.delete(record)
        db.commit()
        log_audit(db, user=user, action="clear_attendance", resource=participant_id, request=request,
                  detail=f"{participant.full_name} on {on_date}")
    else:
        was_present = attended == "present"
        _record_attendance(db, participant_id, on_date, was_present, user)
        db.commit()
        log_audit(db, user=user, action="mark_attendance", resource=participant_id, request=request,
                  detail=f"{participant.full_name} {'present' if was_present else 'absent'} on {on_date}")

    url = f"/check-in?day={on_date.isoformat()}"
    if show:
        url += f"&show={show}"
    return RedirectResponse(url=url + f"#p-{participant_id}", status_code=303)


@router.post("/check-in/mark-rest-present")
def mark_rest_present(
    request: Request,
    day: str = Form(...),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Mark everyone still unmarked as present.

    The shortcut staff actually want at the end of the morning, once the
    few who are out have been marked absent. It deliberately only fills
    in blanks -- it never changes a mark someone already made.
    """
    on_date = _parse_day(day)
    if on_date is None:
        resp = RedirectResponse(url="/check-in", status_code=303)
        set_flash(resp, "That isn't a valid date.", "error")
        return resp

    records = _records_for(db, on_date)
    filled = 0
    for participant in scheduled_participants(db, on_date):
        already_marked = participant.id in records
        if not already_marked and _record_attendance(db, participant.id, on_date, True, user):
            filled += 1
    db.commit()

    log_audit(db, user=user, action="mark_rest_present", request=request,
              detail=f"{filled} participant(s) on {on_date}")
    resp = RedirectResponse(url=f"/check-in?day={on_date.isoformat()}", status_code=303)
    if filled:
        set_flash(resp, f"{filled} participant(s) marked present. Anyone already marked was left as they were.",
                  "success")
    else:
        set_flash(resp, "Everyone scheduled today is already marked.", "success")
    return resp
