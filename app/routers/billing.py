"""The billing week.

One screen that holds the whole billing job: for each day of a week,
whether the PCC batch is in, whether its rows have been checked, whether
the day has been reconciled, and what is still open. Every day offers
the one action that day is actually waiting for, so nobody has to know
which screen comes next.

The week grid used to be the dashboard. It was never an overview -- it
is the work itself -- so it lives under Billing now, and the dashboard
answers "how is the program doing" instead.
"""
import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AttendanceRecord,
    BillingRecord,
    Exception_,
    ExceptionStatus,
    ReconciliationRun,
    Upload,
    UploadKind,
)
from app.render import render
from app.security import get_current_user, log_audit

router = APIRouter()


def monday_of(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def _next_action(day: datetime.date, has_batch: bool, unverified: int,
                 run: ReconciliationRun | None, open_exceptions: int) -> dict | None:
    """The single thing this day is waiting for, or None if it is done.

    Worked out in the order the job actually happens: a batch has to be
    loaded before its rows can be checked, and checked before the day
    can be reconciled.
    """
    iso = day.isoformat()
    if not has_batch:
        # Every batch-less day points at the same one upload screen, so
        # this is `secondary`: five identical primary buttons down a
        # week would shout louder than the day that actually needs work.
        return {"label": "Upload batch", "href": "/upload", "style": "secondary"}
    if unverified:
        return {"label": f"Check {unverified} row(s)", "href": f"/batches/{iso}/review", "style": ""}
    if run is None:
        return {"label": "Reconcile", "href": f"/batches/{iso}/review", "style": ""}
    if open_exceptions:
        return {"label": f"Resolve {open_exceptions}", "href": f"/reports/{iso}", "style": ""}
    return None


def day_states(db: Session, days: list[datetime.date]) -> list[dict]:
    """Everything the grid says about each of a run of days.

    Fetched for the whole range at once rather than day by day: the
    dashboard asks about a fortnight, and a per-day version of this made
    ninety round trips to answer one page.
    """
    if not days:
        return []
    first, last = min(days), max(days)
    wanted = set(days)

    def in_range(column):
        return (column >= first, column <= last)

    attendance_days = set(db.scalars(
        select(AttendanceRecord.date).where(*in_range(AttendanceRecord.date)).distinct()
    ).all())
    batch_days = set(db.scalars(
        select(Upload.batch_date).where(
            Upload.kind == UploadKind.PCC_BATCH, *in_range(Upload.batch_date)
        ).distinct()
    ).all())

    billed: dict[datetime.date, int] = {}
    unverified: dict[datetime.date, int] = {}
    for day, total, unchecked in db.execute(
        select(
            BillingRecord.date,
            func.count(BillingRecord.id),
            func.sum(case((BillingRecord.verified == False, 1), else_=0)),  # noqa: E712
        ).where(*in_range(BillingRecord.date)).group_by(BillingRecord.date)
    ).all():
        billed[day] = total
        unverified[day] = unchecked or 0

    # Newest run per day: ordered oldest-first so the last one written
    # into the map is the one that counts.
    runs: dict[datetime.date, ReconciliationRun] = {}
    for run in db.scalars(
        select(ReconciliationRun).where(*in_range(ReconciliationRun.date))
        .order_by(ReconciliationRun.run_at.asc())
    ).all():
        runs[run.date] = run

    open_counts: dict[str, int] = {}
    for run_id, count in db.execute(
        select(Exception_.run_id, func.count(Exception_.id))
        .where(*in_range(Exception_.date), Exception_.status == ExceptionStatus.OPEN)
        .group_by(Exception_.run_id)
    ).all():
        open_counts[run_id] = count

    states = []
    for day in sorted(wanted):
        latest = runs.get(day)
        open_exceptions = open_counts.get(latest.id, 0) if latest is not None else 0
        has_batch = day in batch_days
        unchecked = unverified.get(day, 0)
        states.append({
            "date": day,
            "has_attendance": day in attendance_days,
            "has_batch": has_batch,
            "billed_rows": billed.get(day, 0),
            "unverified": unchecked,
            "run": latest,
            "open_exceptions": open_exceptions,
            "next_action": _next_action(day, has_batch, unchecked, latest, open_exceptions),
        })
    return states


def day_state(db: Session, day: datetime.date) -> dict:
    """One day's state. Thin wrapper so there is a single definition of
    what a day's state is."""
    return day_states(db, [day])[0]


@router.get("/billing")
def billing_week(
    request: Request,
    week: str | None = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if week:
        try:
            week_start = datetime.date.fromisoformat(week)
        except ValueError:
            week_start = datetime.date.today()
    else:
        week_start = datetime.date.today()
    week_start = monday_of(week_start)

    days = day_states(db, [week_start + datetime.timedelta(days=offset) for offset in range(5)])

    log_audit(db, user=user, action="view_billing_week", resource=str(week_start), request=request)

    return render(request, "billing_week.html", {
        "week_start": week_start,
        "prev_week": (week_start - datetime.timedelta(days=7)).isoformat(),
        "next_week": (week_start + datetime.timedelta(days=7)).isoformat(),
        "this_week": monday_of(datetime.date.today()).isoformat(),
        "days": days,
        "outstanding": sum(1 for day in days if day["next_action"]),
    }, user=user)
