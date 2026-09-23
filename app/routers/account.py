import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import COOKIE_SECURE, SESSION_ABSOLUTE_TIMEOUT_HOURS
from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.render import render
from app.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_current_user,
    hash_password,
    log_audit,
    verify_password,
)

router = APIRouter()

MIN_PASSWORD_LENGTH = 12


@router.get("/account/password")
def change_password_form(request: Request, user=Depends(get_current_user)):
    return render(request, "change_password.html", {"min_length": MIN_PASSWORD_LENGTH}, user=user)


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    errors = []
    # The current password is required even though the session already
    # proves who this is: it stops someone changing the password (and so
    # locking the real owner out) from a screen left unattended.
    if not verify_password(current_password, user.password_hash):
        errors.append("Your current password is not correct.")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        errors.append(f"The new password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if new_password != new_password_confirm:
        errors.append("The new passwords do not match.")
    if new_password and new_password == current_password:
        errors.append("The new password must be different from the current one.")

    if errors:
        log_audit(db, user=user, action="password_change_failed", request=request, success=False,
                  detail="; ".join(errors))
        return render(
            request, "change_password.html",
            {"errors": errors, "min_length": MIN_PASSWORD_LENGTH},
            user=user, status_code=400,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    # Ends any other session on this account -- including one left open
    # on a shared screen, which is often why someone changes it.
    user.password_changed_at = datetime.datetime.utcnow()
    db.commit()

    # The password itself is never logged, here or anywhere else.
    log_audit(db, user=user, action="password_changed", resource=user.id, request=request)

    resp = RedirectResponse(url="/dashboard", status_code=303)
    # Every session older than the change is now refused, including the
    # one this request arrived on, so this browser gets a fresh one --
    # the person who just typed their new password stays signed in, and
    # anyone else holding a session does not. The sliding-expiration
    # refresh has to stand down, or it would re-sign the old payload
    # over the top of this cookie and sign this browser out too.
    request.state.needs_refresh = False
    resp.set_cookie(
        SESSION_COOKIE_NAME, create_session_token(user),
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600,
    )
    set_flash(resp, "Your password has been changed. Any other device signed in to this "
                    "account has been signed out.", "success")
    return resp
