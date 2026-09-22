"""Month views of attendance.

Two of them, because they answer different questions. The program view
("was Tuesday reconciled, and how many were here?") is what a director
scans; the per-participant view ("is Mrs Arocho actually coming on the
days she's scheduled for?") is what a care plan review or a payer
question needs.
"""
import calendar as calendar_module
import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.flash import set_flash
from app.models import (
    AttendanceRecord,
    Exception_,
    ExceptionStatus,
    Participant,
    ReconciliationRun,
    Upload,
    UploadKind,
)
from app.render import render
from app.routers.checkin import scheduled_participants
from app.routers.participants import active_schedule
from app.security import get_current_user, log_audit

router = APIRouter()


def _month_from(raw: str) -> tuple[int, int] | None:
    """Parse "2026-09" into (2026, 9); today's month when empty."""
    if not raw:
        today = datetime.date.today()
        return today.year, today.month
    try:
        year, month = raw.split("-")
        parsed = datetime.date(int(year), int(month), 1)
    except (ValueError, TypeError):
        return None
    return parsed.year, parsed.month


def _month_bounds(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last_day = calendar_module.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


def _shift_month(year: int, month: int, delta: int) -> str:
    index = (year * 12 + (month - 1)) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@router.get("/calendar")
def month_view(
    request: Request,
    month: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    parsed = _month_from(month)
    if parsed is None:
        resp = RedirectResponse(url="/calendar", status_code=303)
        set_flash(resp, "That isn't a valid month.", "error")
        return resp
    year, month_number = parsed
    first, last = _month_bounds(year, month_number)

    attendance = db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.date >= first, AttendanceRecord.date <= last,
    )).all()
    present_by_day: dict[datetime.date, int] = {}
    absent_by_day: dict[datetime.date, int] = {}
    for record in attendance:
        bucket = present_by_day if record.attended else absent_by_day
        bucket[record.date] = bucket.get(record.date, 0) + 1

    batch_days = {
        up.batch_date for up in db.scalars(select(Upload).where(
            Upload.kind == UploadKind.PCC_BATCH, Upload.batch_date >= first, Upload.batch_date <= last,
        )).all() if up.batch_date
    }

    runs: dict[datetime.date, ReconciliationRun] = {}
    for run in db.scalars(select(ReconciliationRun).where(
        ReconciliationRun.date >= first, ReconciliationRun.date <= last,
    ).order_by(ReconciliationRun.run_at)).all():
        runs[run.date] = run  # the latest run for each day wins

    open_by_day = {}
    for day, run in runs.items():
        open_by_day[day] = len(db.scalars(select(Exception_).where(
            Exception_.run_id == run.id, Exception_.status == ExceptionStatus.OPEN,
        )).all())

    today = datetime.date.today()
    cells = {}
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month_number):
        for day in week:
            if day.month != month_number:
                continue
            cells[day] = {
                "present": present_by_day.get(day, 0),
                "absent": absent_by_day.get(day, 0),
                "expected": len(scheduled_participants(db, day)),
                "has_batch": day in batch_days,
                "run": runs.get(day),
                "open_exceptions": open_by_day.get(day, 0),
                "is_today": day == today,
                "is_future": day > today,
            }

    log_audit(db, user=user, action="view_calendar", request=request, detail=f"{year}-{month_number:02d}")
    return render(request, "calendar.html", {
        "weeks": calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month_number),
        "month_number": month_number,
        "cells": cells,
        "title": first.strftime("%B %Y"),
        "previous_month": _shift_month(year, month_number, -1),
        "next_month": _shift_month(year, month_number, 1),
        "weekday_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }, user=user)


@router.get("/participants/{participant_id}/calendar")
def participant_month_view(
    participant_id: str,
    request: Request,
    month: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    participant = db.get(Participant, participant_id)
    if participant is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp

    parsed = _month_from(month)
    if parsed is None:
        resp = RedirectResponse(url=f"/participants/{participant_id}/calendar", status_code=303)
        set_flash(resp, "That isn't a valid month.", "error")
        return resp
    year, month_number = parsed
    first, last = _month_bounds(year, month_number)

    records = {
        record.date: record for record in db.scalars(select(AttendanceRecord).where(
            AttendanceRecord.participant_id == participant_id,
            AttendanceRecord.date >= first, AttendanceRecord.date <= last,
        )).all()
    }
    schedule = active_schedule(db, participant_id)

    today = datetime.date.today()
    cells = {}
    attended_days = missed_days = 0
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month_number):
        for day in week:
            if day.month != month_number:
                continue
            record = records.get(day)
            expected = bool(schedule and schedule.covers(day))
            if record and record.attended:
                attended_days += 1
            elif expected and day <= today and record is not None:
                missed_days += 1
            cells[day] = {
                "record": record,
                "expected": expected,
                "is_today": day == today,
                "is_future": day > today,
            }

    log_audit(db, user=user, action="view_participant_calendar", resource=participant_id, request=request)
    return render(request, "participant_calendar.html", {
        "participant": participant,
        "weeks": calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month_number),
        "month_number": month_number,
        "cells": cells,
        "schedule": schedule,
        "attended_days": attended_days,
        "missed_days": missed_days,
        "title": first.strftime("%B %Y"),
        "previous_month": _shift_month(year, month_number, -1),
        "next_month": _shift_month(year, month_number, 1),
        "weekday_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }, user=user)
