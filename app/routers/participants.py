"""The participant roster: who the program serves, and how each of them
is billed.

Until now identities existed only as a side effect of uploading
documents -- created by the parsers, never visible or editable. That
left the two things a human most needs to do (confirm a same-surname
split was right, and correct one that wasn't) with nowhere to happen.
"""
import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto_types import blind_index
from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.matching import extract_last_name, get_or_create_participant, normalize
from app.models import (
    AttendanceRecord,
    AttendanceSchedule,
    BillingRecord,
    Exception_,
    GrantRuleType,
    Participant,
    ParticipantStatus,
    RateRule,
)
from app.payer_categories import CANONICAL_CATEGORIES
from app.render import render
from app.security import get_current_user, log_audit

router = APIRouter()

RECENT_DAYS = 30


def _roster(db: Session) -> list[Participant]:
    """Every participant, newest name order. Names are encrypted, so
    sorting and searching happen in Python -- the roster is a few
    hundred people at most, and the database must not hold a sortable
    plaintext copy of them."""
    return sorted(db.scalars(select(Participant)).all(), key=lambda p: normalize(p.full_name))


def active_schedule(db: Session, participant_id: str) -> AttendanceSchedule | None:
    """The schedule in force for a participant, if any. A participant
    with none is simply not expected on any particular day -- they can
    still be checked in, they just aren't on the day's list."""
    schedules = db.scalars(
        select(AttendanceSchedule).where(
            AttendanceSchedule.participant_id == participant_id,
            AttendanceSchedule.active == True,  # noqa: E712
        )
    ).all()
    return schedules[-1] if schedules else None


def _active_rules(db: Session, participant_id: str) -> list[RateRule]:
    return db.scalars(
        select(RateRule).where(RateRule.participant_id == participant_id, RateRule.active == True)  # noqa: E712
    ).all()


@router.get("/participants")
def roster(
    request: Request,
    q: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    people = _roster(db)
    if q:
        needle = normalize(q)
        people = [p for p in people if needle in normalize(p.full_name)
                  or needle in (p.external_id or "").lower()]
    if status:
        people = [p for p in people if p.status.value == status]

    rules_by_participant = {}
    schedules_by_participant = {}
    duplicate_surnames: dict[str, int] = {}
    for person in people:
        rules_by_participant[person.id] = _active_rules(db, person.id)
        schedules_by_participant[person.id] = active_schedule(db, person.id)
    for person in _roster(db):
        key = extract_last_name(person.full_name)
        duplicate_surnames[key] = duplicate_surnames.get(key, 0) + 1

    log_audit(db, user=user, action="view_roster", request=request)
    return render(request, "participants.html", {
        "people": people,
        "rules_by_participant": rules_by_participant,
        "schedules_by_participant": schedules_by_participant,
        "shared_surnames": {k for k, count in duplicate_surnames.items() if count > 1},
        "statuses": list(ParticipantStatus),
        "q": q,
        "status_filter": status,
        "total": len(_roster(db)),
    }, user=user)


@router.post("/participants/new")
def add_participant(
    request: Request,
    full_name: str = Form(...),
    external_id: str = Form(""),
    status: str = Form("active"),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    raw = full_name.strip()
    if not raw:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "A name is required.", "error")
        return resp
    if external_id.strip():
        raw = f"{raw} ({external_id.strip()})"

    participant, created, ambiguity = get_or_create_participant(db, raw)
    participant.status = ParticipantStatus(status)
    db.commit()

    log_audit(db, user=user, action="add_participant" if created else "match_existing_participant",
              resource=participant.id, request=request)

    resp = RedirectResponse(url=f"/participants/{participant.id}", status_code=303)
    if created:
        msg = f"{participant.full_name} added to the roster."
    else:
        msg = f"That name already belongs to {participant.full_name} on the roster -- opened their profile."
    if ambiguity:
        msg += " " + ambiguity
    set_flash(resp, msg, "success")
    return resp


@router.get("/participants/{participant_id}")
def profile(
    participant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    participant = db.get(Participant, participant_id)
    if participant is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp

    since = datetime.date.today() - datetime.timedelta(days=RECENT_DAYS)
    attendance = db.scalars(
        select(AttendanceRecord)
        .where(AttendanceRecord.participant_id == participant_id, AttendanceRecord.date >= since)
        .order_by(AttendanceRecord.date.desc())
    ).all()
    billing = db.scalars(
        select(BillingRecord)
        .where(BillingRecord.participant_id == participant_id, BillingRecord.date >= since)
        .order_by(BillingRecord.date.desc())
    ).all()
    exceptions = db.scalars(
        select(Exception_)
        .where(Exception_.participant_id == participant_id)
        .order_by(Exception_.date.desc())
        .limit(20)
    ).all()

    # Anyone sharing this surname: the people this participant could
    # plausibly be a duplicate of, offered as merge targets.
    same_surname = [
        p for p in db.scalars(
            select(Participant).where(
                Participant.last_name_index == blind_index(extract_last_name(participant.full_name))
            )
        ).all() if p.id != participant.id
    ]

    log_audit(db, user=user, action="view_participant", resource=participant_id, request=request)
    return render(request, "participant_profile.html", {
        "participant": participant,
        "statuses": list(ParticipantStatus),
        "rules": _active_rules(db, participant_id),
        "attendance": attendance,
        "billing": billing,
        "exceptions": exceptions,
        "same_surname": same_surname,
        "recent_days": RECENT_DAYS,
        "schedule": active_schedule(db, participant_id),
        "weekday_names": AttendanceSchedule.WEEKDAY_NAMES[:5],
        "payer_options": CANONICAL_CATEGORIES,
        "grant_types": list(GrantRuleType),
        "today": datetime.date.today().isoformat(),
    }, user=user)


@router.post("/participants/{participant_id}/edit")
def edit_participant(
    participant_id: str,
    request: Request,
    full_name: str = Form(...),
    external_id: str = Form(""),
    status: str = Form("active"),
    notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    participant = db.get(Participant, participant_id)
    if participant is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp

    new_name = full_name.strip()
    if not new_name:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, "A name is required.", "error")
        return resp

    new_external = external_id.strip().upper() or None
    if new_external and new_external != participant.external_id:
        clash = db.scalar(select(Participant).where(
            Participant.external_id == new_external, Participant.id != participant_id,
        ))
        if clash:
            resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
            set_flash(resp, f"{new_external} already belongs to {clash.full_name}.", "error")
            return resp

    participant.full_name = new_name
    participant.external_id = new_external
    # The surname index has to follow the name, or a corrected spelling
    # would stop matching the batch rows it was corrected to match.
    participant.last_name_index = blind_index(extract_last_name(new_name))
    participant.status = ParticipantStatus(status)
    participant.notes = notes.strip() or None
    db.commit()

    log_audit(db, user=user, action="edit_participant", resource=participant_id, request=request,
              detail=f"status={participant.status.value}")
    resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
    set_flash(resp, "Profile updated.", "success")
    return resp


@router.post("/participants/{participant_id}/merge")
def merge_participant(
    participant_id: str,
    request: Request,
    target_id: str = Form(...),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Fold this participant into another and delete the empty one.

    Two records for one person is the expected failure mode of matching
    people by name: the sources spell them differently, or one carries a
    facility ID and the other doesn't. Both halves then read as half
    attending and half billed, which is exactly the kind of false
    exception this tool exists to avoid.
    """
    source = db.get(Participant, participant_id)
    target = db.get(Participant, target_id)
    if source is None or target is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp
    if source.id == target.id:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, "Pick a different participant to merge into.", "error")
        return resp

    moved_attendance = 0
    kept_attendance = 0
    for record in db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.participant_id == source.id
    )).all():
        clash = db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.participant_id == target.id, AttendanceRecord.date == record.date,
        ))
        if clash:
            # Same day recorded on both halves: present wins, since a
            # mark of attendance is a positive observation and a blank
            # is only the absence of one.
            clash.attended = clash.attended or record.attended
            db.delete(record)
            kept_attendance += 1
        else:
            record.participant_id = target.id
            moved_attendance += 1
    db.flush()

    moved_billing = 0
    for record in db.scalars(select(BillingRecord).where(BillingRecord.participant_id == source.id)).all():
        record.participant_id = target.id
        moved_billing += 1

    moved_rules = 0
    for rule in db.scalars(select(RateRule).where(RateRule.participant_id == source.id)).all():
        rule.participant_id = target.id
        moved_rules += 1

    for exception in db.scalars(select(Exception_).where(Exception_.participant_id == source.id)).all():
        exception.participant_id = target.id

    if not target.external_id and source.external_id:
        # Free the ID on the record being dissolved before handing it
        # over -- it's unique across the roster, so both can't hold it
        # even for the length of one flush.
        carried_id, source.external_id = source.external_id, None
        db.flush()
        target.external_id = carried_id
    if source.notes:
        target.notes = "\n".join(filter(None, [target.notes, source.notes]))
    db.flush()

    source_name = source.full_name
    db.delete(source)
    db.commit()

    log_audit(db, user=user, action="merge_participants", resource=target.id, request=request,
              detail=(f"merged '{source_name}' into '{target.full_name}': {moved_attendance} attendance "
                      f"day(s) moved, {kept_attendance} already present, {moved_billing} billing row(s), "
                      f"{moved_rules} payer rule(s)"))

    resp = RedirectResponse(url=f"/participants/{target.id}", status_code=303)
    set_flash(resp, f"Merged {source_name} into {target.full_name}: {moved_attendance + kept_attendance} "
                    f"attendance day(s) and {moved_billing} billing row(s) now belong to one person. "
                    "Re-run reconciliation for any affected day to clear exceptions raised against the old split.",
              "success")
    return resp


# --------------------------------------------------------------- rules ----
# How a participant is billed belongs on their profile, next to their
# name: the Rate Master spreadsheet is a snapshot of these rules, not
# the place they should have to be edited.


def _rule_fields(
    payer_source: str, rate: str, grant_rule_type: str, grant_cycle_length: str,
    grant_cycle_secondary_days: str, grant_payer: str, notes: str,
) -> tuple[dict, str | None]:
    """Validate and coerce the rule form. Returns (fields, error)."""
    payer = payer_source.strip()
    if not payer:
        return {}, "A payer source is required."

    try:
        rate_value = float(rate) if rate.strip() else None
    except ValueError:
        return {}, "The rate must be a number, e.g. 95.00."
    if rate_value is not None and rate_value < 0:
        return {}, "The rate can't be negative."

    rule_type = GrantRuleType(grant_rule_type)
    cycle_length = None
    secondary_days = 1
    secondary_payer = grant_payer.strip() or None

    if rule_type == GrantRuleType.ROTATING_GRANT:
        try:
            cycle_length = int(grant_cycle_length)
            secondary_days = int(grant_cycle_secondary_days or 1)
        except ValueError:
            return {}, "A rotation needs whole numbers of days on each side."
        if cycle_length < 1 or secondary_days < 1:
            return {}, "A rotation needs at least one day on each side."
        if not secondary_payer:
            return {}, "A rotation needs the payer it rotates to."

    return {
        "payer_source": payer,
        "rate": rate_value,
        "grant_rule_type": rule_type,
        "grant_cycle_length": cycle_length,
        "grant_cycle_secondary_days": secondary_days,
        "grant_payer": secondary_payer,
        "notes": notes.strip() or None,
    }, None


@router.post("/participants/{participant_id}/rules/add")
def add_rule(
    participant_id: str,
    request: Request,
    payer_source: str = Form(...),
    rate: str = Form(""),
    grant_rule_type: str = Form("none"),
    grant_cycle_length: str = Form(""),
    grant_cycle_secondary_days: str = Form("1"),
    grant_payer: str = Form(""),
    effective_start: str = Form(""),
    effective_end: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    participant = db.get(Participant, participant_id)
    if participant is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp

    fields, error = _rule_fields(payer_source, rate, grant_rule_type, grant_cycle_length,
                                grant_cycle_secondary_days, grant_payer, notes)
    dates, date_error = _rule_dates(effective_start, effective_end)
    error = error or date_error
    if error:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, error, "error")
        return resp

    # One arrangement at a time: a new rule supersedes the ones already
    # in force, rather than leaving two to race in reconciliation.
    for existing in _active_rules(db, participant_id):
        if existing.effective_end is None:
            existing.active = False

    db.add(RateRule(participant_id=participant_id, **fields, **dates))
    db.commit()

    log_audit(db, user=user, action="add_rate_rule", resource=participant_id, request=request,
              detail=fields["payer_source"])
    resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
    set_flash(resp, f"{participant.full_name} is now billed as {fields['payer_source']}.", "success")
    return resp


def _rule_dates(effective_start: str, effective_end: str) -> tuple[dict, str | None]:
    start = end = None
    try:
        if effective_start.strip():
            start = datetime.date.fromisoformat(effective_start.strip())
        if effective_end.strip():
            end = datetime.date.fromisoformat(effective_end.strip())
    except ValueError:
        return {}, "Those effective dates aren't valid dates."
    if start and end and end < start:
        return {}, "The end date can't be before the start date."
    return {"effective_start": start, "effective_end": end}, None


@router.post("/participants/{participant_id}/rules/{rule_id}/edit")
def edit_rule(
    participant_id: str,
    rule_id: str,
    request: Request,
    payer_source: str = Form(...),
    rate: str = Form(""),
    grant_rule_type: str = Form("none"),
    grant_cycle_length: str = Form(""),
    grant_cycle_secondary_days: str = Form("1"),
    grant_payer: str = Form(""),
    effective_start: str = Form(""),
    effective_end: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rule = db.get(RateRule, rule_id)
    if rule is None or rule.participant_id != participant_id:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, "That billing rule no longer exists.", "error")
        return resp

    fields, error = _rule_fields(payer_source, rate, grant_rule_type, grant_cycle_length,
                                grant_cycle_secondary_days, grant_payer, notes)
    dates, date_error = _rule_dates(effective_start, effective_end)
    error = error or date_error
    if error:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, error, "error")
        return resp

    for key, value in {**fields, **dates}.items():
        setattr(rule, key, value)
    db.commit()

    log_audit(db, user=user, action="edit_rate_rule", resource=rule_id, request=request)
    resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
    set_flash(resp, "Billing updated. Re-run reconciliation for any day this affects.", "success")
    return resp


@router.post("/participants/{participant_id}/rules/{rule_id}/remove")
def remove_rule(
    participant_id: str,
    rule_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rule = db.get(RateRule, rule_id)
    if rule and rule.participant_id == participant_id:
        # Deactivated, not deleted: reconciliations already run against
        # it stay explicable.
        rule.active = False
        db.commit()
        log_audit(db, user=user, action="deactivate_rate_rule", resource=rule_id, request=request)
    resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
    set_flash(resp, "Billing rule removed -- this participant now defaults to Private Pay.", "success")
    return resp


# ------------------------------------------------------------ schedule ----

@router.post("/participants/{participant_id}/schedule")
async def set_schedule(
    participant_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """Set which days of the week a participant is expected.

    The form posts a checkbox per weekday, so the days are read off the
    form directly -- an unticked box sends nothing, which is how a day
    gets removed.
    """
    participant = db.get(Participant, participant_id)
    if participant is None:
        resp = RedirectResponse(url="/participants", status_code=303)
        set_flash(resp, "That participant no longer exists.", "error")
        return resp

    form = await request.form()
    selected = sorted({int(day) for day in form.getlist("weekday") if day.isdigit() and 0 <= int(day) <= 6})
    starts, ends = form.get("effective_start", ""), form.get("effective_end", "")

    try:
        start = datetime.date.fromisoformat(starts) if starts else None
        end = datetime.date.fromisoformat(ends) if ends else None
    except ValueError:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, "Those schedule dates aren't valid dates.", "error")
        return resp
    if start and end and end < start:
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, "The schedule's end date can't be before its start date.", "error")
        return resp

    schedule = active_schedule(db, participant_id)
    if not selected:
        if schedule:
            schedule.active = False
            db.commit()
            log_audit(db, user=user, action="clear_schedule", resource=participant_id, request=request)
        resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
        set_flash(resp, f"{participant.full_name} is no longer on a set schedule.", "success")
        return resp

    if schedule is None:
        schedule = AttendanceSchedule(participant_id=participant_id)
        db.add(schedule)
    schedule.days_of_week = ",".join(str(day) for day in selected)
    schedule.effective_start = start
    schedule.effective_end = end
    schedule.active = True
    schedule.notes = (form.get("notes") or "").strip() or None
    db.commit()

    log_audit(db, user=user, action="set_schedule", resource=participant_id, request=request,
              detail=schedule.describe())
    resp = RedirectResponse(url=f"/participants/{participant_id}", status_code=303)
    set_flash(resp, f"{participant.full_name} is expected on {schedule.describe()}.", "success")
    return resp
