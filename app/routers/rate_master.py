from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.matching import get_or_create_participant
from app.models import GrantRuleType, RateRule
from app.render import render
# The profile form validates the same fields against the same table;
# sharing its coercion keeps the two screens from disagreeing about what
# a valid rule is (and stops a typed rate raising a 500 here).
from app.routers.participants import validate_rule_fields
from app.security import get_current_user, log_audit

router = APIRouter()


@router.get("/rate-master")
def list_rate_rules(request: Request, db: Session = Depends(get_db), user=Depends(get_current_user)):
    rules = db.scalars(
        select(RateRule).where(RateRule.active == True).order_by(RateRule.payer_source)  # noqa: E712
    ).all()
    log_audit(db, user=user, action="view_rate_master", request=request)
    return render(request, "rate_master.html", {"rules": rules, "grant_types": list(GrantRuleType)}, user=user)


@router.post("/rate-master/add")
def add_rule(
    request: Request,
    participant_name: str = Form(...),
    payer_source: str = Form(...),
    rate: str = Form(""),
    grant_rule_type: str = Form("none"),
    grant_cycle_length: str = Form(""),
    grant_cycle_secondary_days: str = Form("1"),
    grant_payer: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    fields, error = validate_rule_fields(
        payer_source, rate, grant_rule_type, grant_cycle_length,
        grant_cycle_secondary_days, grant_payer, notes,
    )
    if error:
        resp = RedirectResponse(url="/rate-master", status_code=303)
        set_flash(resp, error, "error")
        return resp

    participant, _, ambiguity = get_or_create_participant(db, participant_name)
    db.add(RateRule(participant_id=participant.id, **fields))
    db.commit()
    log_audit(db, user=user, action="add_rate_rule", request=request, detail=participant_name)
    resp = RedirectResponse(url="/rate-master", status_code=303)
    msg = f"Rate rule added for {participant_name}."
    if ambiguity:
        msg += " " + ambiguity
    set_flash(resp, msg, "success" if not ambiguity else "error")
    return resp


@router.post("/rate-master/{rule_id}/edit")
def edit_rule(
    rule_id: str,
    request: Request,
    payer_source: str = Form(...),
    rate: str = Form(""),
    grant_rule_type: str = Form("none"),
    grant_cycle_length: str = Form(""),
    grant_cycle_secondary_days: str = Form("1"),
    grant_payer: str = Form(""),
    notes: str = Form(""),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rule = db.get(RateRule, rule_id)
    if rule is None:
        resp = RedirectResponse(url="/rate-master", status_code=303)
        set_flash(resp, "Rule not found.", "error")
        return resp

    fields, error = validate_rule_fields(
        payer_source, rate, grant_rule_type, grant_cycle_length,
        grant_cycle_secondary_days, grant_payer, notes,
    )
    if error:
        resp = RedirectResponse(url="/rate-master", status_code=303)
        set_flash(resp, error, "error")
        return resp

    for key, value in fields.items():
        setattr(rule, key, value)
    db.commit()

    log_audit(db, user=user, action="edit_rate_rule", resource=rule_id, request=request)
    resp = RedirectResponse(url="/rate-master", status_code=303)
    set_flash(resp, "Rate rule updated.", "success")
    return resp


@router.post("/rate-master/{rule_id}/deactivate")
def deactivate_rule(
    rule_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    rule = db.get(RateRule, rule_id)
    if rule:
        rule.active = False
        db.commit()
        log_audit(db, user=user, action="deactivate_rate_rule", resource=rule_id, request=request)
    resp = RedirectResponse(url="/rate-master", status_code=303)
    set_flash(resp, "Rate rule removed.", "success")
    return resp
