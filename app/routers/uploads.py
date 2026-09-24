import datetime
import logging

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.forms import uploaded_files
from app.models import (
    BillingRecord,
    Exception_,
    ReconciliationRun,
    Upload,
    UploadKind,
)
from app.parsers.pcc_batch import parse_pcc_batch
from app.render import render
from app.security import get_current_user, log_audit
from app.storage import save_encrypted

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# A week of batches is five files; the cap leaves room for a fortnight
# while keeping the whole submission small enough to hold in memory.
MAX_BATCH_FILES = 14
# ...and a ceiling on the submission as a whole, since every file in it
# is held in memory at once and the server this runs on has 1 GB.
MAX_BATCH_TOTAL_BYTES = 60 * 1024 * 1024


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


async def _read_limited(file: StarletteUploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds the 20 MB upload limit.")
    return data


def _missing(file: UploadFile | None) -> bool:
    """True when the form arrived without a file attached."""
    return file is None or not file.filename


@router.get("/upload")
def upload_form(request: Request, user=Depends(get_current_user)):
    return render(request, "upload.html", {"today": datetime.date.today().isoformat()}, user=user)


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
    form = await request.form()
    # "file" as well as "files": a browser showing a cached copy of the
    # older single-file form still posts under the old name.
    incoming = uploaded_files(form, "files", "file")
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
    total_bytes = 0
    for upload_file in incoming:
        try:
            raw = await _read_limited(upload_file)
        except ValueError as exc:
            log_audit(db, user=user, action="upload_pcc_batch_rejected", request=request, success=False,
                      detail=f"{upload_file.filename or '(unnamed)'}: {exc}")
            parsed.append({"filename": upload_file.filename or "(unnamed)", "raw": None, "result": None, "error": str(exc)})
            continue

        total_bytes += len(raw)
        if total_bytes > MAX_BATCH_TOTAL_BYTES:
            resp = RedirectResponse(url="/upload", status_code=303)
            set_flash(resp, f"Those files come to more than "
                            f"{MAX_BATCH_TOTAL_BYTES // (1024 * 1024)} MB together. "
                            "Nothing was imported -- please upload them in smaller groups.", "error")
            return resp

        try:
            result = parse_pcc_batch(raw)
        except Exception:
            # A file that isn't a batch export at all (the wrong PDF, a
            # truncated download) is reported per file, so the rest of
            # the week still imports.
            logger.exception("PCC batch file %s could not be parsed", upload_file.filename)
            parsed.append({
                "filename": upload_file.filename or "(unnamed)", "raw": None, "result": None,
                "error": "This file couldn't be read as a PCC batch export. Re-export the day from "
                         "PCC (Save Page As -> Webpage, HTML Only) and try again.",
            })
            continue

        parsed.append({
            "filename": upload_file.filename or "(unnamed)",
            "raw": raw,
            "result": result,
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
                uploaded_by_id=user.id, parse_warnings="\n".join([*result.warnings, error]) or None,
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
