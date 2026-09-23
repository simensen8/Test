import hmac
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import COOKIE_SECURE, SESSION_ABSOLUTE_TIMEOUT_HOURS, SETUP_TOKEN
from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.models import Role, User
from app.render import render
from app.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_current_user,
    hash_password,
    is_locked_out,
    log_audit,
    register_failed_login,
    register_successful_login,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Compared against when the email doesn't exist, so that answering takes
# as long either way. Hashed once at import; the value is never a real
# password.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-placeholder")


@router.get("/setup")
def setup_form(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login")
    return render(request, "setup.html")


@router.post("/setup")
def setup_submit(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    setup_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create the first administrator.

    This is the one route that can be reached without an account, so it
    is guarded by a token the operator reads off the server. Without
    that, whoever reaches the site first becomes its administrator --
    and the site announces itself the moment its certificate is issued,
    since certificate transparency logs are public and watched.
    """
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login", status_code=303)

    errors = []
    if not hmac.compare_digest(setup_token.strip(), SETUP_TOKEN):
        errors.append(
            "That setup token doesn't match. It's printed in the server's startup log "
            "(docker compose logs app) and stored in data/.setup_token."
        )
    if password != password_confirm:
        errors.append("Passwords do not match.")
    if len(password) < 12:
        errors.append("Password must be at least 12 characters.")
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    if errors:
        return render(request, "setup.html", {"errors": errors, "display_name": display_name, "email": email},
                      status_code=400)

    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=Role.ADMIN,
        must_change_password=False,
    )
    db.add(user)
    db.flush()

    # Two people (or two requests) can pass the count check above at the
    # same time. Re-counting inside the write transaction that created
    # this row is what actually settles it: the loser sees two and backs
    # out, leaving exactly one administrator.
    if db.query(User).count() > 1:
        db.rollback()
        resp = RedirectResponse(url="/login", status_code=303)
        set_flash(resp, "An administrator account already exists. Please log in.", "error")
        return resp

    db.commit()
    log_audit(db, user=user, action="admin_account_created", request=request)

    resp = RedirectResponse(url="/login", status_code=303)
    set_flash(resp, "Administrator account created. Please log in.", "success")
    return resp


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if db.query(User).count() == 0:
        return RedirectResponse(url="/setup")
    return render(request, "login.html")


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))

    if user is None or not user.active:
        # Hash anyway before answering. Skipping it returned in 7ms where
        # a real account takes 270ms, which is a reliable way to ask the
        # login page which staff addresses have accounts.
        verify_password(password, DUMMY_PASSWORD_HASH)
        log_audit(db, user=None, action="login_failed", resource=email, request=request, success=False,
                   detail="unknown or inactive account")
        return render(request, "login.html", {"error": "Invalid email or password."}, status_code=401)

    if is_locked_out(user):
        log_audit(db, user=user, action="login_blocked_locked_out", request=request, success=False)
        return render(request, "login.html", {"error": "Account temporarily locked due to failed attempts. Try again later."}, status_code=401)

    if not verify_password(password, user.password_hash):
        register_failed_login(db, user)
        log_audit(db, user=user, action="login_failed", request=request, success=False, detail="bad password")
        return render(request, "login.html", {"error": "Invalid email or password."}, status_code=401)

    register_successful_login(db, user)
    log_audit(db, user=user, action="login_success", request=request)

    token = create_session_token(user)
    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE_NAME, token,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600,
    )
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Depends(verify_csrf), db: Session = Depends(get_db)):
    user = None
    try:
        user = get_current_user(request, db)
    except Exception:  # an expired or absent session still logs out cleanly
        logger.debug("Logout without a valid session; clearing the cookie anyway")
    if user:
        log_audit(db, user=user, action="logout", request=request)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp
