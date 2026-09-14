import datetime

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import (
    LOCKOUT_MINUTES,
    MAX_LOGIN_ATTEMPTS,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_IDLE_TIMEOUT_MINUTES,
    SESSION_SECRET_KEY,
)
from app.db import get_db
from app.models import AuditLog, Role, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_serializer = URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="session-cookie")

SESSION_COOKIE_NAME = "adp_session"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_session_token(user: User) -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "uid": user.id,
        "issued_at": now.isoformat(),
        "last_seen": now.isoformat(),
    }
    return _serializer.dumps(payload)


def decode_session_token(token: str) -> dict | None:
    try:
        return _serializer.loads(token, max_age=SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None


def refresh_session_token(payload: dict) -> str:
    payload = dict(payload)
    payload["last_seen"] = datetime.datetime.utcnow().isoformat()
    return _serializer.dumps(payload)


def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def log_audit(
    db: Session,
    *,
    user: User | None,
    action: str,
    resource: str | None = None,
    request: Request | None = None,
    detail: str | None = None,
    success: bool = True,
):
    entry = AuditLog(
        user_id=user.id if user else None,
        user_email_snapshot=user.email if user else None,
        action=action,
        resource=resource,
        ip_address=get_client_ip(request) if request else None,
        detail=detail,
        success=success,
    )
    db.add(entry)
    db.commit()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    unauthorized = HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    if not token:
        raise unauthorized
    payload = decode_session_token(token)
    if not payload:
        raise unauthorized
    last_seen = datetime.datetime.fromisoformat(payload["last_seen"])
    if datetime.datetime.utcnow() - last_seen > datetime.timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        raise unauthorized
    user = db.get(User, payload["uid"])
    if not user or not user.active:
        raise unauthorized
    request.state.session_payload = payload
    request.state.needs_refresh = True
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def is_locked_out(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.datetime.utcnow())


def register_failed_login(db: Session, user: User):
    user.failed_login_count += 1
    if user.failed_login_count >= MAX_LOGIN_ATTEMPTS:
        user.locked_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=LOCKOUT_MINUTES)
        user.failed_login_count = 0
    db.commit()


def register_successful_login(db: Session, user: User):
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.datetime.utcnow()
    db.commit()
