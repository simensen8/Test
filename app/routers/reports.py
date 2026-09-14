import csv
import datetime
import io

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.models import BillingRecord, Exception_, ExceptionStatus, ReconciliationRun
from app.reconcile import run_reconciliation
from app.render import render
from app.security import get_current_user, log_audit

router = APIRouter()


@router.post("/reports/{date}/reconcile")
def reconcile_date(
    date: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    the_date = datetime.date.fromisoformat(date)
    unverified = db.scalar(
        select(BillingRecord.id).where(BillingRecord.date == the_date, BillingRecord.verified == False)  # noqa: E712
    )
    if unverified:
        resp = RedirectResponse(url=f"/batches/{date}/review", status_code=303)
        set_flash(resp, "All PCC batch rows must be verified before reconciling.", "error")
        return resp

    run_reconciliation(db, the_date, user.id)
    log_audit(db, user=user, action="run_reconciliation", resource=the_date.isoformat(), request=request)

    resp = RedirectResponse(url=f"/reports/{date}", status_code=303)
    set_flash(resp, "Reconciliation complete.", "success")
    return resp


@router.get("/reports/{date}")
def report_day(date: str, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    the_date = datetime.date.fromisoformat(date)
    run = db.scalar(
        select(ReconciliationRun).where(ReconciliationRun.date == the_date).order_by(ReconciliationRun.run_at.desc())
    )
    exceptions = []
    if run:
        exceptions = db.scalars(
            select(Exception_).where(Exception_.run_id == run.id).order_by(Exception_.reason, Exception_.participant_name_snapshot)
        ).all()

    log_audit(db, user=user, action="view_reconciliation_report", resource=the_date.isoformat(), request=request)

    return render(request, "report_day.html", {
        "date": the_date,
        "run": run,
        "exceptions": exceptions,
        "statuses": list(ExceptionStatus),
    }, user=user)


@router.post("/exceptions/{exception_id}/resolve")
def resolve_exception(
    exception_id: str,
    request: Request,
    status: str = Form(...),
    resolution_notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    exc = db.get(Exception_, exception_id)
    if exc is None:
        resp = RedirectResponse(url="/dashboard", status_code=303)
        set_flash(resp, "Exception not found.", "error")
        return resp

    exc.status = ExceptionStatus(status)
    exc.resolution_notes = resolution_notes or None
    exc.resolved_by_id = user.id
    exc.resolved_at = datetime.datetime.utcnow()
    db.commit()

    log_audit(db, user=user, action="resolve_exception", resource=exception_id, request=request,
               detail=f"status={status}")

    resp = RedirectResponse(url=f"/reports/{exc.date.isoformat()}", status_code=303)
    set_flash(resp, "Exception updated.", "success")
    return resp


@router.get("/reports/{date}/export.csv")
def export_csv(date: str, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    the_date = datetime.date.fromisoformat(date)
    run = db.scalar(
        select(ReconciliationRun).where(ReconciliationRun.date == the_date).order_by(ReconciliationRun.run_at.desc())
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Participant", "Reason", "Expected Billing", "Actual Billing", "Detail", "Status"])
    if run:
        exceptions = db.scalars(select(Exception_).where(Exception_.run_id == run.id)).all()
        for exc in exceptions:
            writer.writerow([
                exc.date.isoformat(), exc.participant_name_snapshot, exc.reason.value,
                exc.expected_billing or "", exc.actual_billing or "", exc.detail or "", exc.status.value,
            ])
    if not run or not run.exception_count:
        writer.writerow([the_date.isoformat(), "", "Reconciled / No Exceptions", "", "", "", ""])

    log_audit(db, user=user, action="export_report_csv", resource=the_date.isoformat(), request=request)

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reconciliation_{the_date.isoformat()}.csv"},
    )
