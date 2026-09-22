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
    AttendanceSource,
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
# A week of batches is five files; the cap leaves room for a fortnight
# while keeping the whole submission small enough to hold in memory.
MAX_BATCH_FILES = 14
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
                # The uploaded sheet is the transcription of the signed
                # paper record, so it wins over a check-in mark for the
                # same day -- and the row records that it came from the
                # sheet, not from whoever last touched the screen.
                existing.attended = attended
                existing.source = AttendanceSource.UPLOAD
                existing.source_upload_id = upload.id
                existing.recorded_by_id = user.id
                existing.recorded_at = datetime.datetime.utcnow()
            else:
                db.add(AttendanceRecord(
                    participant_id=participant.id, date=day, attended=attended,
                    source=AttendanceSource.UPLOAD, source_upload_id=upload.id,
                    recorded_by_id=user.id, recorded_at=datetime.datetime.utcnow(),
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
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Import one or more PCC batch exports, each filed under the service
    date read out of the file itself.

    The date is never asked for: a director uploading a week at a time
    would have to pair five files with five dates by hand, and a single
    slip files a day's billing under the wrong date -- which reads as
    dozens of false exceptions on two different days. A file whose date
    can't be read is refused rather than guessed at.
    """
    # Read the file parts off the form rather than declaring them: an
    # empty file input arrives as an empty string part, which a
    # list[UploadFile] parameter rejects with an unhelpful 422 instead of
    # "no batch file was selected".
    # (Parts come back as Starlette's UploadFile, not FastAPI's subclass,
    # so they're identified by having a filename rather than by isinstance.)
    form = await request.form()
    parts = form.getlist("files") + form.getlist("file")  # "file" = a cached copy of the old single-file form
    incoming = [part for part in parts if getattr(part, "filename", "")]
    if not incoming:
        resp = RedirectResponse(url="/upload", status_code=303)
        set_flash(resp, "No batch file was selected.", "error")
        return resp
    if len(incoming) > MAX_BATCH_FILES:
        resp = RedirectResponse(url="/upload", status_code=303)
        set_flash(resp, f"Please upload at most {MAX_BATCH_FILES} batch files at a time.", "error")
        return resp

    # Parse everything before writing anything: a submission that pairs
    # two files with the same day is a mistake worth catching whole,
    # rather than half-importing and leaving the reviewer to work out
    # which file won.
    parsed: list[dict] = []
    for upload_file in incoming:
        try:
            raw = await _read_limited(upload_file)
        except ValueError as exc:
            log_audit(db, user=user, action="upload_pcc_batch_rejected", request=request, success=False,
                      detail=f"{upload_file.filename}: {exc}")
            parsed.append({"filename": upload_file.filename, "raw": None, "result": None, "error": str(exc)})
            continue
        parsed.append({
            "filename": upload_file.filename,
            "raw": raw,
            "result": parse_pcc_batch(raw),
            "error": None,
        })

    dated: dict[datetime.date, list[str]] = {}
    for item in parsed:
        result = item["result"]
        if result and result.batch_date:
            dated.setdefault(result.batch_date, []).append(item["filename"])
    collisions = {day: names for day, names in dated.items() if len(names) > 1}
    if collisions:
        detail = "; ".join(
            f"{day.isoformat()}: " + ", ".join(names) for day, names in sorted(collisions.items())
        )
        resp = RedirectResponse(url="/upload", status_code=303)
        set_flash(resp, f"Two or more of these files are for the same billing date ({detail}). "
                        "Nothing was imported -- upload one file per day.", "error")
        return resp

    summary: list[dict] = []
    for item in parsed:
        filename, raw, result = item["filename"], item["raw"], item["result"]
        if item["error"]:
            summary.append({"filename": filename, "date": None, "row_count": 0,
                            "replaced_rows": 0, "replaced_exceptions": 0,
                            "warnings": [], "error": item["error"]})
            continue

        stored_path = save_encrypted("pcc_batch", filename, raw)
        batch_date_val = result.batch_date

        if batch_date_val is None:
            error = ("No service date could be read from this file. The batch should carry a "
                     "\"Services for M/D/YYYY\" header, or dated rows. Nothing was imported from it.")
            # The upload is still recorded: it was received and stored, and
            # the audit log has to show that whether or not it parsed.
            failed_upload = Upload(
                kind=UploadKind.PCC_BATCH, original_filename=filename, stored_path=stored_path,
                uploaded_by_id=user.id, parse_warnings="\n".join(result.warnings + [error]) or None,
            )
            db.add(failed_upload)
            db.flush()
            log_audit(db, user=user, action="upload_pcc_batch_no_date", resource=failed_upload.id,
                      request=request, success=False,
                      detail=f"{filename}: stored but not imported -- no service date could be read")
            summary.append({"filename": filename, "date": None, "row_count": 0,
                            "replaced_rows": 0, "replaced_exceptions": 0,
                            "warnings": result.warnings, "error": error})
            continue

        if result.batch_date_source == "rows":
            result.warnings.append(
                f"This file has no \"Services for ...\" header; the date {batch_date_val.isoformat()} "
                "was taken from the rows' Eff. Date column."
            )

        replaced_rows, replaced_exceptions = clear_batch_for_date(db, batch_date_val)

        upload = Upload(
            kind=UploadKind.PCC_BATCH,
            original_filename=filename,
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

        summary.append({"filename": filename, "date": batch_date_val, "row_count": len(result.rows),
                        "replaced_rows": replaced_rows, "replaced_exceptions": replaced_exceptions,
                        "warnings": result.warnings, "error": None})

        log_audit(db, user=user, action="upload_pcc_batch", resource=upload.id, request=request,
                  detail=(f"{len(result.rows)} rows extracted via {result.extraction_method} for "
                          f"{batch_date_val} (date from {result.batch_date_source}); replaced "
                          f"{replaced_rows} prior row(s), {replaced_exceptions} exception(s)"))

    db.commit()

    imported = [entry for entry in summary if entry["error"] is None]
    # One clean file is the everyday case -- go straight to its review
    # screen instead of making the reviewer click through a summary of one.
    if len(summary) == 1 and imported and not imported[0]["warnings"]:
        only = imported[0]
        resp = RedirectResponse(url=f"/batches/{only['date'].isoformat()}/review", status_code=303)
        msg = (f"PCC batch for {only['date'].strftime('%A, %B %-d, %Y')}: {only['row_count']} rows extracted. "
               "Please verify each row before reconciling.")
        if only["replaced_rows"]:
            msg = (f"Replaced the existing batch for {only['date'].strftime('%A, %B %-d, %Y')} "
                   f"({only['replaced_rows']} row(s)"
                   + (f" and {only['replaced_exceptions']} reconciliation exception(s)"
                      if only["replaced_exceptions"] else "")
                   + f" removed). {only['row_count']} rows extracted from the new file -- please verify "
                   "each row before reconciling.")
        set_flash(resp, msg, "success")
        return resp

    return render(request, "batch_upload_summary.html", {"summary": summary}, user=user)
