import hashlib
import hmac

from fastapi import Form, HTTPException, Request, status

from app.config import SESSION_SECRET_KEY
from app.security import SESSION_COOKIE_NAME, decode_session_token


def _stable_csrf_secret(request: Request) -> str | None:
    """CSRF tokens are derived from the parts of the session payload that
    stay constant for the life of a login (user id + issued_at), not the
    full cookie value -- the cookie's last_seen field is rewritten on
    every request for sliding-expiration, which would otherwise
    invalidate a token the moment the page that rendered it triggered
    that rewrite."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    payload = decode_session_token(token)
    if not payload:
        return None
    return f"{payload['uid']}:{payload['issued_at']}"


def csrf_token_for(secret: str) -> str:
    return hmac.new(SESSION_SECRET_KEY.encode(), secret.encode(), hashlib.sha256).hexdigest()


def get_csrf_token(request: Request) -> str:
    secret = _stable_csrf_secret(request)
    if secret is None:
        return ""
    return csrf_token_for(secret)


def verify_csrf(request: Request, csrf_token: str = Form(...)):
    secret = _stable_csrf_secret(request)
    expected = csrf_token_for(secret) if secret is not None else None
    if not expected or not hmac.compare_digest(expected, csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired form token. Please retry.")
