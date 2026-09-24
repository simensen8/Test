"""The operational overview.

What the program looks like today and this month, in figures that are
read straight off the records rather than derived from assumptions.
Every number here answers a question somebody actually asks:

  Is today's attendance recorded yet, and who is still unmarked?
  What is waiting on me?
  How much have we billed this month, and is it reconciled?

Deliberately absent: anything that would need an assumption to compute.
Revenue is the sum of what the PCC batches actually charged, not a
projection from rates and schedules; a number that looks like money but
is really a forecast is worse than no number, because it will be read as
money. Where a figure can't be trusted yet -- no batches loaded, nobody
on a schedule -- the tile says so instead of showing a confident zero.
"""
import datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AttendanceRecord,
    BillingRecord,
    Exception_,
    ExceptionStatus,
    Participant,
    ParticipantStatus,
    ReconciliationRun,
    Upload,
    UploadKind,
)
from app.render import render
from app.routers.billing import day_states, monday_of
from app.routers.checkin import scheduled_participants
from app.security import get_current_user, log_audit

router = APIRouter()

# How far back the "needs attention" list looks, counting today. Two
# weeks is long enough to catch a day that slipped during a holiday
# week, and short enough that the list stays something a person can
# actually clear.
ATTENTION_WINDOW_DAYS = 14


def _month_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    start = today.replace(day=1)
    if start.month == 12:
        next_start = start.replace(year=start.year + 1, month=1)
    else:
        next_start = start.replace(month=start.month + 1)
    return start, next_start - datetime.timedelta(days=1)


def _today_panel(db: Session, today: datetime.date) -> dict:
    """Check-in progress for today, counted the same way the check-in
    screen counts it -- same schedule query, so the two screens cannot
    disagree about who was expected."""
    expected = scheduled_participants(db, today)
    expected_ids = {person.id for person in expected}
    marks = db.scalars(select(AttendanceRecord).where(AttendanceRecord.date == today)).all()
    present = sum(1 for m in marks if m.attended and m.participant_id in expected_ids)
    absent = sum(1 for m in marks if not m.attended and m.participant_id in expected_ids)
    return {
        "expected": len(expected),
        "present": present,
        "absent": absent,
        "unmarked": len(expected) - present - absent,
        "batch_in": db.scalar(
            select(Upload.id).where(Upload.kind == UploadKind.PCC_BATCH, Upload.batch_date == today)
        ) is not None,
    }


def _needs_attention(db: Session, today: datetime.date) -> list[dict]:
    """Weekdays in the recent past with something still outstanding.

    Only days that have a batch or attendance behind them: a Tuesday the
    program was closed has no billing to do, and listing it as
    outstanding would teach people to ignore the list.
    """
    weekdays = [
        day for day in (
            today - datetime.timedelta(days=offset)
            for offset in range(ATTENTION_WINDOW_DAYS - 1, -1, -1)
        )
        if day.weekday() <= 4
    ]
    return [
        state for state in day_states(db, weekdays)
        if state["next_action"] is not None
        and (state["has_batch"] or state["has_attendance"])
    ]


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    today = datetime.date.today()
    month_start, month_end = _month_bounds(today)

    billed_total = db.scalar(
        select(func.sum(BillingRecord.amount)).where(
            BillingRecord.date >= month_start, BillingRecord.date <= month_end
        )
    )
    billed_days = db.scalar(
        select(func.count(func.distinct(BillingRecord.date))).where(
            BillingRecord.date >= month_start, BillingRecord.date <= month_end
        )
    ) or 0
    reconciled_days = db.scalar(
        select(func.count(func.distinct(ReconciliationRun.date))).where(
            ReconciliationRun.date >= month_start, ReconciliationRun.date <= month_end
        )
    ) or 0
    open_exceptions = db.scalar(
        select(func.count(Exception_.id)).where(
            Exception_.date >= month_start,
            Exception_.date <= month_end,
            Exception_.status == ExceptionStatus.OPEN,
        )
    ) or 0
    settled_exceptions = db.scalar(
        select(func.count(Exception_.id)).where(
            Exception_.date >= month_start,
            Exception_.date <= month_end,
            Exception_.status != ExceptionStatus.OPEN,
        )
    ) or 0

    # What the open findings are actually about. A day with eleven
    # unmatched names is a different morning's work from one with eleven
    # wrong rates, and the reason is the only thing that says which.
    by_reason = db.execute(
        select(Exception_.reason, func.count(Exception_.id))
        .where(
            Exception_.date >= month_start,
            Exception_.date <= month_end,
            Exception_.status == ExceptionStatus.OPEN,
        )
        .group_by(Exception_.reason)
        .order_by(func.count(Exception_.id).desc())
    ).all()

    active_participants = db.scalar(
        select(func.count(Participant.id)).where(Participant.status == ParticipantStatus.ACTIVE)
    ) or 0

    log_audit(db, user=user, action="view_dashboard", resource=str(today), request=request)

    return render(request, "dashboard.html", {
        "today": today,
        "month_label": month_start.strftime("%B %Y"),
        "week_start": monday_of(today).isoformat(),
        "today_panel": _today_panel(db, today),
        "attention": _needs_attention(db, today),
        "billed_total": billed_total,
        "billed_days": billed_days,
        "reconciled_days": reconciled_days,
        # Reconciling a day that has attendance but no batch is a
        # legitimate thing to do -- it is how "attended but never
        # billed" is found -- so the count of reconciled days can run
        # ahead of the days with billing on them. The denominator is
        # whichever is larger, or the tile reads "1 / 0".
        "billing_days_total": max(billed_days, reconciled_days),
        "open_exceptions": open_exceptions,
        "settled_exceptions": settled_exceptions,
        "by_reason": [(reason.value.replace("_", " "), count) for reason, count in by_reason],
        "active_participants": active_participants,
    }, user=user)
