import datetime

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    GrantRuleType,
    RateRule,
    Upload,
    UploadKind,
)
from app.parsers.attendance import parse_weekly_attendance
from app.parsers.pcc_batch import parse_pcc_batch
from app.parsers.rate_master import parse_rate_master
from app.render import render
from app.security import get_current_user, log_audit
from app.storage import save_encrypted

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXCEL_TYPES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"}
ALLOWED_PDF_TYPES = {"application/pdf"}


async def _read_limited(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds the 20 MB upload limit.")
    return data


@router.get("/upload")
def upload_form(request: Request, user=Depends(get_current_user)):
    return render(request, "upload.html", {"today": datetime.date.today().isoformat()}, user=user)


@router.post("/upload/attendance")
async def upload_attendance(
    request: Request,
    week_start: str = Form(...),
    file: UploadFile = None,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        week_start_date = datetime.date.fromisoformat(week_start)
    except ValueError:
        resp = RedirectResponse(url="/upload", status_code=303)
        set_flash(resp, "Invalid week start date.", "error")
        return resp

    raw = await _read_limited(file)
    stored_path = save_encrypted("weekly_attendance", file.filename, raw)

    result = parse_weekly_attendance(raw, week_start_date)

    upload = Upload(
        kind=UploadKind.WEEKLY_ATTENDANCE,
        original_filename=file.filename,
        stored_path=stored_path,
        uploaded_by_id=user.id,
        week_start=week_start_date,
        parse_warnings="\n".join(result.warnings) or None,
    )
    db.add(upload)
    db.flush()

    created_count = 0
    for row in result.rows:
        participant, created = get_or_create_participant(db, row.participant_name)
        if created:
            created_count += 1
        for day, attended in row.attendance_by_date.items():
            existing = db.scalar(
                select(AttendanceRecord).where(
                    AttendanceRecord.participant_id == participant.id,
                    AttendanceRecord.date == day,
                )
            )
            if existing:
                existing.attended = attended
                existing.source_upload_id = upload.id
            else:
                db.add(AttendanceRecord(
                    participant_id=participant.id, date=day, attended=attended, source_upload_id=upload.id,
                ))
    db.commit()

    log_audit(db, user=user, action="upload_weekly_attendance", resource=upload.id, request=request,
               detail=f"{len(result.rows)} participant rows, week of {week_start_date}")

    resp = RedirectResponse(url="/dashboard?week=" + week_start_date.isoformat(), status_code=303)
    msg = f"Weekly attendance uploaded: {len(result.rows)} participants ({created_count} new)."
    if result.warnings:
        msg += " Warnings: " + "; ".join(result.warnings)
    set_flash(resp, msg, "success" if not result.warnings else "error")
    return resp


@router.post("/upload/rate-master")
async def upload_rate_master(
    request: Request,
    file: UploadFile = None,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    raw = await _read_limited(file)
    stored_path = save_encrypted("rate_master", file.filename, raw)
    result = parse_rate_master(raw)

    upload = Upload(
        kind=UploadKind.RATE_MASTER,
        original_filename=file.filename,
        stored_path=stored_path,
        uploaded_by_id=user.id,
        parse_warnings="\n".join(result.warnings) or None,
    )
    db.add(upload)
    db.flush()

    # A fresh rate master supersedes prior auto-imported rules so stale
    # exceptions don't linger; manually-added rules (source_upload_id is
    # None) are left untouched.
    for rule in db.scalars(select(RateRule).where(RateRule.source_upload_id.isnot(None))):
        rule.active = False

    for row in result.rows:
        participant, _ = get_or_create_participant(db, row.participant_name)
        db.add(RateRule(
            participant_id=participant.id,
            payer_source=row.payer_source,
            rate=row.rate,
            grant_rule_type=GrantRuleType(row.grant_rule_type),
            grant_cycle_length=row.grant_cycle_length,
            grant_payer=row.grant_payer,
            notes=row.notes,
            source_upload_id=upload.id,
        ))
    db.commit()

    log_audit(db, user=user, action="upload_rate_master", resource=upload.id, request=request,
               detail=f"{len(result.rows)} rate rules")

    resp = RedirectResponse(url="/rate-master", status_code=303)
    msg = f"Rate Master uploaded: {len(result.rows)} rules loaded."
    if result.warnings:
        msg += " Please review: " + "; ".join(result.warnings)
    set_flash(resp, msg, "success" if not result.warnings else "error")
    return resp


@router.post("/upload/pcc-batch")
async def upload_pcc_batch(
    request: Request,
    batch_date: str = Form(...),
    file: UploadFile = None,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        batch_date_val = datetime.date.fromisoformat(batch_date)
    except ValueError:
        resp = RedirectResponse(url="/upload", status_code=303)
        set_flash(resp, "Invalid batch date.", "error")
        return resp

    raw = await _read_limited(file)
    stored_path = save_encrypted("pcc_batch", file.filename, raw)
    result = parse_pcc_batch(raw)

    upload = Upload(
        kind=UploadKind.PCC_BATCH,
        original_filename=file.filename,
        stored_path=stored_path,
        uploaded_by_id=user.id,
        batch_date=batch_date_val,
        parse_warnings="\n".join(result.warnings) or None,
    )
    db.add(upload)
    db.flush()

    for row in result.rows:
        db.add(BillingRecord(
            raw_name=row.raw_name,
            date=batch_date_val,
            payer_source=row.payer_source,
            amount=row.amount,
            units=row.units,
            raw_line=row.raw_line,
            source_upload_id=upload.id,
            verified=False,
        ))
    db.commit()

    log_audit(db, user=user, action="upload_pcc_batch", resource=upload.id, request=request,
               detail=f"{len(result.rows)} rows extracted via {result.extraction_method} for {batch_date_val}")

    resp = RedirectResponse(url=f"/batches/{batch_date_val.isoformat()}/review", status_code=303)
    msg = f"PCC batch uploaded: {len(result.rows)} rows extracted. Please verify each row before reconciling."
    set_flash(resp, msg, "success")
    return resp
