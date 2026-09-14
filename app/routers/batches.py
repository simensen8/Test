import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.matching import fuzzy_find_participant
from app.models import BillingRecord, Upload, UploadKind
from app.render import render
from app.security import get_current_user, log_audit

router = APIRouter()


@router.get("/batches/{date}/review")
def review_batch(date: str, request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    try:
        the_date = datetime.date.fromisoformat(date)
    except ValueError:
        resp = RedirectResponse(url="/dashboard", status_code=303)
        set_flash(resp, "Invalid date.", "error")
        return resp

    rows = db.scalars(
        select(BillingRecord).where(BillingRecord.date == the_date).order_by(BillingRecord.raw_name)
    ).all()

    suggestions = {}
    for row in rows:
        if row.participant_id is None:
            match = fuzzy_find_participant(db, row.raw_name)
            if match.participant is not None:
                suggestions[row.id] = f"{match.candidate_name} ({match.score:.0f}% match)"

    uploads = db.scalars(
        select(Upload).where(Upload.kind == UploadKind.PCC_BATCH, Upload.batch_date == the_date)
    ).all()
    warnings = [w for u in uploads if u.parse_warnings for w in u.parse_warnings.split("\n")]

    log_audit(db, user=user, action="view_batch_review", resource=the_date.isoformat(), request=request)

    return render(request, "batch_review.html", {
        "date": the_date,
        "rows": rows,
        "suggestions": suggestions,
        "warnings": warnings,
        "all_verified": all(r.verified for r in rows) if rows else False,
    }, user=user)


@router.post("/batches/{date}/review/save")
async def save_batch_review(
    date: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    the_date = datetime.date.fromisoformat(date)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token", ""))

    row_ids = form.getlist("row_id")
    updated = 0
    for row_id in row_ids:
        row = db.get(BillingRecord, row_id)
        if row is None or row.date != the_date:
            continue
        row.raw_name = form.get(f"name_{row_id}", row.raw_name).strip()
        row.payer_source = (form.get(f"payer_{row_id}", "") or "").strip() or None
        amount_raw = form.get(f"amount_{row_id}", "")
        try:
            row.amount = float(amount_raw) if amount_raw not in (None, "") else None
        except ValueError:
            pass
        row.units = (form.get(f"units_{row_id}", "") or "").strip() or None
        row.verified = True
        row.participant_id = None  # re-resolve on next reconciliation run
        updated += 1

    db.commit()
    log_audit(db, user=user, action="save_batch_review", resource=the_date.isoformat(), request=request,
               detail=f"{updated} rows verified/updated")

    resp = RedirectResponse(url=f"/batches/{date}/review", status_code=303)
    set_flash(resp, f"Saved {updated} row(s) as verified.", "success")
    return resp


@router.post("/batches/{date}/review/add-row")
def add_row(
    date: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    the_date = datetime.date.fromisoformat(date)
    upload = db.scalar(
        select(Upload).where(Upload.kind == UploadKind.PCC_BATCH, Upload.batch_date == the_date)
        .order_by(Upload.uploaded_at.desc())
    )
    if upload is None:
        upload = Upload(
            kind=UploadKind.PCC_BATCH, original_filename="(manually added)", stored_path="",
            uploaded_by_id=user.id, batch_date=the_date,
        )
        db.add(upload)
        db.flush()
    db.add(BillingRecord(raw_name="", date=the_date, source_upload_id=upload.id, verified=False))
    db.commit()
    log_audit(db, user=user, action="add_manual_billing_row", resource=the_date.isoformat(), request=request)
    return RedirectResponse(url=f"/batches/{date}/review", status_code=303)


@router.post("/batches/{date}/review/delete/{row_id}")
def delete_row(
    date: str,
    row_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = db.get(BillingRecord, row_id)
    if row and row.date == datetime.date.fromisoformat(date):
        db.delete(row)
        db.commit()
        log_audit(db, user=user, action="delete_billing_row", resource=row_id, request=request)
    return RedirectResponse(url=f"/batches/{date}/review", status_code=303)
