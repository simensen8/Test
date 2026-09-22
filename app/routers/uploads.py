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
    Exception_,
    GrantRuleType,
    RateRule,
    ReconciliationRun,
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


def clear_batch_for_date(db: Session, on_date: datetime.date) -> tuple[int, int]:
    """Remove a date's billing rows and any reconciliation built on them,
    returning (billing rows removed, exceptions removed).

    Re-uploading a batch has to REPLACE that day rather than add to it.
    Appending instead means the same participants appear twice and every
    one of them reconciles as a duplicate-billing exception, which reads
    as the tool being broken rather than as a double upload.

    Prior reconciliation runs for the date go too: they describe a batch
    that no longer exists, and their exceptions carry foreign keys to the
    rows being deleted. Any resolution notes on those exceptions are lost
    with them, so callers should say how much was cleared. The Upload
    rows and their encrypted files are kept for audit either way.
    """
    exceptions = db.scalars(select(Exception_).where(Exception_.date == on_date)).all()
    for exc in exceptions:
        db.delete(exc)
    db.flush()

    for run in db.scalars(select(ReconciliationRun).where(ReconciliationRun.date == on_date)):
        db.delete(run)
    db.flush()

    rows = db.scalars(select(BillingRecord).where(BillingRecord.date == on_date)).all()
    for row in rows:
        db.delete(row)
    db.flush()

    return len(rows), len(exceptions)


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
    )
    db.add(upload)
    db.flush()

    created_count = 0
    ambiguous_warnings: list[str] = []
    for row in result.rows:
        participant, created, ambiguity = get_or_create_participant(db, row.participant_name)
        if created:
            created_count += 1
        if ambiguity:
            ambiguous_warnings.append(ambiguity)
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

    all_warnings = result.warnings + ambiguous_warnings
    upload.parse_warnings = "\n".join(all_warnings) or None
    db.commit()

    log_audit(db, user=user, action="upload_weekly_attendance", resource=upload.id, request=request,
               detail=f"{len(result.rows)} participant rows, week of {week_start_date}")

    resp = RedirectResponse(url="/dashboard?week=" + week_start_date.isoformat(), status_code=303)
    msg = f"Weekly attendance uploaded: {len(result.rows)} participants ({created_count} new)."
    if all_warnings:
        msg += " Warnings: " + "; ".join(all_warnings)
    set_flash(resp, msg, "success" if not all_warnings else "error")
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
    )
    db.add(upload)
    db.flush()

    # A fresh rate master supersedes prior auto-imported rules so stale
    # exceptions don't linger; manually-added rules (source_upload_id is
    # None) are left untouched.
    for rule in db.scalars(select(RateRule).where(RateRule.source_upload_id.isnot(None))):
        rule.active = False

    ambiguous_warnings: list[str] = []
    for row in result.rows:
        participant, _, ambiguity = get_or_create_participant(db, row.participant_name)
        if ambiguity:
            ambiguous_warnings.append(ambiguity)
        db.add(RateRule(
            participant_id=participant.id,
            payer_source=row.payer_source,
            rate=row.rate,
            grant_rule_type=GrantRuleType(row.grant_rule_type),
            grant_cycle_length=row.grant_cycle_length,
            grant_cycle_secondary_days=row.grant_cycle_secondary_days,
            grant_payer=row.grant_payer,
            notes=row.notes,
            source_upload_id=upload.id,
        ))

    all_warnings = result.warnings + ambiguous_warnings
    upload.parse_warnings = "\n".join(all_warnings) or None
    db.commit()

    log_audit(db, user=user, action="upload_rate_master", resource=upload.id, request=request,
               detail=f"{len(result.rows)} rate rules")

    resp = RedirectResponse(url="/rate-master", status_code=303)
    msg = f"Rate Master uploaded: {len(result.rows)} rules loaded."
    if all_warnings:
        msg += " Please review: " + "; ".join(all_warnings)
    set_flash(resp, msg, "success" if not all_warnings else "error")
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

    mismatched_dates = {
        row.row_date for row in result.rows
        if row.row_date and row.row_date != batch_date_val.strftime("%-m/%-d/%Y")
    }
    if mismatched_dates:
        result.warnings.append(
            f"This file's own date(s) ({', '.join(sorted(mismatched_dates))}) don't match the "
            f"billing date you selected ({batch_date_val.isoformat()}) -- double check you uploaded the right file."
        )

    replaced_rows, replaced_exceptions = clear_batch_for_date(db, batch_date_val)

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
               detail=(f"{len(result.rows)} rows extracted via {result.extraction_method} for {batch_date_val}; "
                       f"replaced {replaced_rows} prior row(s), {replaced_exceptions} exception(s)"))

    resp = RedirectResponse(url=f"/batches/{batch_date_val.isoformat()}/review", status_code=303)
    msg = f"PCC batch uploaded: {len(result.rows)} rows extracted. Please verify each row before reconciling."
    if replaced_rows:
        msg = (f"Replaced the existing batch for this date ({replaced_rows} row(s)"
               + (f" and {replaced_exceptions} reconciliation exception(s)" if replaced_exceptions else "")
               + f" removed). {len(result.rows)} rows extracted from the new file -- please verify each row "
               f"before reconciling.")
    set_flash(resp, msg, "success")
    return resp
