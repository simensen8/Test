from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.csrf import verify_csrf
from app.db import get_db
from app.flash import set_flash
from app.models import AuditLog, Role, User
from app.render import render
from app.security import hash_password, log_audit, require_admin

router = APIRouter()


@router.get("/admin/users")
def list_users(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    users = db.scalars(select(User).order_by(User.display_name)).all()
    log_audit(db, user=user, action="view_users", request=request)
    return render(request, "admin_users.html", {"users": users, "roles": list(Role)}, user=user)


@router.post("/admin/users/create")
def create_user(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    temp_password: str = Form(...),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    if len(temp_password) < 12:
        resp = RedirectResponse(url="/admin/users", status_code=303)
        set_flash(resp, "Temporary password must be at least 12 characters.", "error")
        return resp

    existing = db.scalar(select(User).where(User.email == email.strip().lower()))
    if existing:
        resp = RedirectResponse(url="/admin/users", status_code=303)
        set_flash(resp, "A user with that email already exists.", "error")
        return resp

    new_user = User(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(temp_password),
        role=Role(role),
        must_change_password=True,
    )
    db.add(new_user)
    db.commit()

    log_audit(db, user=user, action="create_user", resource=new_user.id, request=request, detail=new_user.email)

    resp = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(resp, f"User {new_user.email} created. Share the temporary password with them through a secure channel (not email).", "success")
    return resp


@router.post("/admin/users/{user_id}/deactivate")
def deactivate_user(
    user_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    target = db.get(User, user_id)
    if target and target.id != user.id:
        target.active = False
        db.commit()
        log_audit(db, user=user, action="deactivate_user", resource=user_id, request=request)
    resp = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(resp, "User deactivated.", "success")
    return resp


@router.post("/admin/users/{user_id}/reactivate")
def reactivate_user(
    user_id: str,
    request: Request,
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    target = db.get(User, user_id)
    if target:
        target.active = True
        db.commit()
        log_audit(db, user=user, action="reactivate_user", resource=user_id, request=request)
    resp = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(resp, "User reactivated.", "success")
    return resp


@router.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    request: Request,
    temp_password: str = Form(...),
    csrf_token: str = Depends(verify_csrf),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    """Give a user a new temporary password.

    Without this, a forgotten password is an unrecoverable lockout: there
    is no email-based reset (and there shouldn't be -- a reset link in a
    mailbox is a way into PHI). The new password must be changed on the
    user's next sign-in, same as a newly created account.
    """
    target = db.get(User, user_id)
    if target is None:
        resp = RedirectResponse(url="/admin/users", status_code=303)
        set_flash(resp, "That user no longer exists.", "error")
        return resp
    if len(temp_password) < 12:
        resp = RedirectResponse(url="/admin/users", status_code=303)
        set_flash(resp, "Temporary password must be at least 12 characters.", "error")
        return resp

    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    target.failed_login_count = 0
    target.locked_until = None
    db.commit()

    log_audit(db, user=user, action="reset_user_password", resource=target.id, request=request,
              detail=target.email)

    resp = RedirectResponse(url="/admin/users", status_code=303)
    set_flash(resp, f"Temporary password set for {target.email}. Share it through a secure channel "
                    "(not email) -- they'll be asked to choose their own password when they sign in.", "success")
    return resp


@router.get("/admin/audit-log")
def audit_log(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    entries = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(500)).all()
    return render(request, "audit_log.html", {"entries": entries}, user=user)
