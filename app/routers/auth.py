from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import COOKIE_SECURE, SESSION_ABSOLUTE_TIMEOUT_HOURS
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

router = APIRouter()


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
    db: Session = Depends(get_db),
):
    if db.query(User).count() > 0:
        return RedirectResponse(url="/login", status_code=303)

    errors = []
    if password != password_confirm:
        errors.append("Passwords do not match.")
    if len(password) < 12:
        errors.append("Password must be at least 12 characters.")
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    if errors:
        return render(request, "setup.html", {"errors": errors, "display_name": display_name, "email": email})

    user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=Role.ADMIN,
        must_change_password=False,
    )
    db.add(user)
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
    except Exception:
        pass
    if user:
        log_audit(db, user=user, action="logout", request=request)
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp
