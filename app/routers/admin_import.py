"""The one-time import that seeds the roster before the program goes live.

Two workbooks describe the program as it exists on paper, and between
them they hold everything the app needs to start from:

  Weekly Attendance -- every participant's name and facility ID. It is
  the only complete roster; the PCC batch deliberately never creates a
  participant (an unrecognised name becomes an exception for a human
  rather than a new identity minted from a typo).

  Rate Master -- the payer exceptions. Only the minority of
  participants appear on it, because anyone absent from it is Private
  Pay by default; what it carries that nothing else does is the
  rotation rules ("three days private pay, then one on the grant"),
  which drive what reconciliation expects.

After this has run, a participant's profile is the single place their
payer arrangement lives, and neither workbook is part of the daily
routine again. That is why this sits in Admin rather than on the upload
screen: it is setup, not operations, and running it a second time by
accident would rewrite the rules someone has since corrected by hand.
"""
import datetime
import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.dates import parse_iso_date
from app.db import get_db
from app.flash import set_flash
from app.matching import fuzzy_find_participant, get_or_create_participant
from app.models import (
    AttendanceRecord,
    AttendanceSource,
    BillingRecord,
    GrantRuleType,
    Participant,
    RateRule,
    Upload,
    UploadKind,
)
from app.parsers.attendance import parse_weekly_attendance
from app.parsers.rate_master import parse_rate_master
from app.render import render
from app.security import log_audit, require_admin
from app.storage import save_encrypted

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_IMPORT_BYTES = 20 * 1024 * 1024


async def _read_workbook(file: UploadFile | None) -> bytes | None:
    if file is None or not file.filename:
        return None
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError("That file is over the 20 MB limit.")
    return data


def _roster_state(db: Session) -> dict:
    """What the roster looks like right now, so the screen can say
    whether the import has already been done."""
    participants = db.scalar(select(func.count()).select_from(Participant)) or 0
    with_rules = len({
        rule.participant_id
        for rule in db.scalars(select(RateRule).where(RateRule.active == True)).all()  # noqa: E712
    })
    rotations = db.scalar(
        select(func.count()).select_from(RateRule).where(
            RateRule.active == True,  # noqa: E712
            RateRule.grant_rule_type == GrantRuleType.ROTATING_GRANT,
        )
    ) or 0
    attendance_days = len({
        record.date for record in db.scalars(select(AttendanceRecord)).all()
    })
    return {
        "participants": participants,
        "with_rules": with_rules,
        "rotations": rotations,
        "default_private_pay": max(participants - with_rules, 0),
        "attendance_days": attendance_days,
    }


def _unmatched_billing_names(db: Session) -> list[str]:
    """Names that have been billed but match nobody on the roster.

    Worth seeing straight after an import: each one is a participant
    who will reconcile as an exception every day they are billed until
    somebody adds or corrects them.
    """
    unmatched = []
    for row in db.scalars(select(BillingRecord)).all():
        if row.participant_id is not None:
            continue
        if fuzzy_find_participant(db, row.raw_name).participant is None:
            unmatched.append(row.raw_name)
    return sorted(set(unmatched))


def _render_result(request: Request, db: Session, user, outcome: dict):
    """The import screen, with what just happened above it."""
    return render(request, "admin_import.html", {
        "state": _roster_state(db),
        "unmatched": _unmatched_billing_names(db),
        "today": datetime.date.today().isoformat(),
        "outcome": outcome,
    }, user=user)


@router.get("/admin/import")
def import_screen(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    log_audit(db, user=user, action="view_import", request=request)
    return render(request, "admin_import.html", {
        "state": _roster_state(db),
        "unmatched": _unmatched_billing_names(db),
        "today": datetime.date.today().isoformat(),
    }, user=user)


@router.post("/admin/import/roster")
async def import_roster(
    request: Request,
    week_start: str = Form(...),
    file: UploadFile | None = None,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Seed participants (and that week's attendance) from the workbook."""
    back = RedirectResponse(url="/admin/import", status_code=303)

    week_start_date = parse_iso_date(week_start)
    if week_start_date is None:
        set_flash(back, "Invalid week start date.", "error")
        return back

    try:
        raw = await _read_workbook(file)
    except ValueError as exc:
        set_flash(back, str(exc), "error")
        return back
    if raw is None:
        set_flash(back, "No attendance workbook was selected.", "error")
        return back
    assert file is not None

    try:
        result = parse_weekly_attendance(raw, week_start_date)
    except Exception:
        logger.exception("Roster import could not be parsed")
        set_flash(back, "That file couldn't be read as a weekly attendance workbook. "
                        "Please check it opens in Excel and is the right file.", "error")
        return back

    stored_path = save_encrypted("weekly_attendance", file.filename or "attendance", raw)
    upload = Upload(
        kind=UploadKind.WEEKLY_ATTENDANCE,
        original_filename=file.filename or "(unnamed)",
        stored_path=stored_path,
        uploaded_by_id=user.id,
        week_start=week_start_date,
    )
    db.add(upload)
    db.flush()

    created = 0
    warnings: list[str] = []
    for row in result.rows:
        participant, was_new, ambiguity = get_or_create_participant(db, row.participant_name)
        if was_new:
            created += 1
        if ambiguity:
            warnings.append(ambiguity)
        for day, attended in row.attendance_by_date.items():
            existing = db.scalar(select(AttendanceRecord).where(
                AttendanceRecord.participant_id == participant.id,
                AttendanceRecord.date == day,
            ))
            if existing:
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

    all_warnings = result.warnings + warnings
    upload.parse_warnings = "\n".join(all_warnings) or None
    db.commit()

    log_audit(db, user=user, action="import_roster", resource=upload.id, request=request,
              detail=f"{len(result.rows)} rows, {created} new participants, week of {week_start_date}")

    return _render_result(request, db, user, {
        "title": "Roster imported",
        "counts": [
            (len(result.rows), "rows read from the workbook"),
            (created, "participants added"),
            (len(result.rows) - created, "already on the roster"),
        ],
        "warnings": all_warnings,
    })


@router.post("/admin/import/rates")
async def import_rates(
    request: Request,
    file: UploadFile | None = None,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Load the payer arrangements onto participant profiles.

    Rules that came from a previous import are superseded; anything
    entered by hand on a profile is left alone, so a correction someone
    made after the last import is not quietly undone by running this
    again.
    """
    back = RedirectResponse(url="/admin/import", status_code=303)

    try:
        raw = await _read_workbook(file)
    except ValueError as exc:
        set_flash(back, str(exc), "error")
        return back
    if raw is None:
        set_flash(back, "No Rate Master workbook was selected.", "error")
        return back
    assert file is not None

    try:
        result = parse_rate_master(raw)
    except Exception:
        logger.exception("Rate import could not be parsed")
        set_flash(back, "That file couldn't be read as a Rate Master workbook. "
                        "Please check it opens in Excel and is the right file.", "error")
        return back

    stored_path = save_encrypted("rate_master", file.filename or "rate_master", raw)
    upload = Upload(
        kind=UploadKind.RATE_MASTER,
        original_filename=file.filename or "(unnamed)",
        stored_path=stored_path,
        uploaded_by_id=user.id,
    )
    db.add(upload)
    db.flush()

    superseded = 0
    for rule in db.scalars(select(RateRule).where(RateRule.source_upload_id.isnot(None))):
        if rule.active:
            rule.active = False
            superseded += 1

    created_participants = 0
    rotations = 0
    warnings: list[str] = []
    for row in result.rows:
        participant, was_new, ambiguity = get_or_create_participant(db, row.participant_name)
        if was_new:
            created_participants += 1
        if ambiguity:
            warnings.append(ambiguity)
        rule_type = GrantRuleType(row.grant_rule_type)
        if rule_type == GrantRuleType.ROTATING_GRANT:
            rotations += 1
        db.add(RateRule(
            participant_id=participant.id,
            payer_source=row.payer_source,
            rate=row.rate,
            grant_rule_type=rule_type,
            grant_cycle_length=row.grant_cycle_length,
            grant_cycle_secondary_days=row.grant_cycle_secondary_days,
            grant_payer=row.grant_payer,
            notes=row.notes,
            source_upload_id=upload.id,
        ))

    all_warnings = result.warnings + warnings
    upload.parse_warnings = "\n".join(all_warnings) or None
    db.commit()

    log_audit(db, user=user, action="import_rates", resource=upload.id, request=request,
              detail=(f"{len(result.rows)} rules ({rotations} rotations), "
                      f"{created_participants} new participants, {superseded} superseded"))

    return _render_result(request, db, user, {
        "title": "Payer rules imported onto profiles",
        "counts": [
            (len(result.rows), "payer rules loaded"),
            (rotations, "with a rotation"),
            (created_participants, "participants added (on the Rate Master, not the roster)"),
            (superseded, "rules from a previous import superseded"),
        ],
        "warnings": all_warnings,
    })
