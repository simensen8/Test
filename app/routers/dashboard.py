import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AttendanceRecord, Exception_, ExceptionStatus, ReconciliationRun, Upload, UploadKind
from app.render import render
from app.security import get_current_user, log_audit

router = APIRouter()


def monday_of(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


@router.get("/dashboard")
def dashboard(
    request: Request,
    week: str | None = Query(None),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    if week:
        try:
            week_start = datetime.date.fromisoformat(week)
        except ValueError:
            week_start = monday_of(datetime.date.today())
    else:
        week_start = monday_of(datetime.date.today())
    week_start = monday_of(week_start)

    days = []
    for offset in range(5):
        d = week_start + datetime.timedelta(days=offset)
        has_attendance = db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.date == d)) is not None
        has_batch = db.scalar(
            select(Upload.id).where(Upload.kind == UploadKind.PCC_BATCH, Upload.batch_date == d)
        ) is not None
        run = db.scalar(
            select(ReconciliationRun).where(ReconciliationRun.date == d).order_by(ReconciliationRun.run_at.desc())
        )
        open_exceptions = 0
        if run:
            open_exceptions = db.scalar(
                select(Exception_.id).where(Exception_.run_id == run.id, Exception_.status == ExceptionStatus.OPEN)
            )
            open_exceptions = len(db.scalars(
                select(Exception_).where(Exception_.run_id == run.id, Exception_.status == ExceptionStatus.OPEN)
            ).all())
        days.append({
            "date": d,
            "has_attendance": has_attendance,
            "has_batch": has_batch,
            "run": run,
            "open_exceptions": open_exceptions,
        })

    log_audit(db, user=user, action="view_dashboard", resource=str(week_start), request=request)

    return render(request, "dashboard.html", {
        "week_start": week_start,
        "prev_week": (week_start - datetime.timedelta(days=7)).isoformat(),
        "next_week": (week_start + datetime.timedelta(days=7)).isoformat(),
        "days": days,
    }, user=user)
