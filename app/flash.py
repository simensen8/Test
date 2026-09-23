"""One-shot messages carried across a redirect.

The message rides in a cookie, and messages here name people -- "Merged
Hall, Tressa into Hall, Tresea", "Adams, Alice is now billed as
Medicaid". That is PHI, so the cookie carries ciphertext rather than the
sentence itself: it is written to disk in the browser profile, which on
a shared workstation outlives the session that produced it.
"""
import json

from cryptography.fernet import InvalidToken
from fastapi.responses import Response

from app.config import COOKIE_SECURE, FERNET

FLASH_COOKIE = "adp_flash"
FLASH_MAX_AGE_SECONDS = 30


def set_flash(response: Response, text: str, category: str = "success"):
    payload = json.dumps([{"text": text, "category": category}])
    sealed = FERNET.encrypt(payload.encode("utf-8")).decode("ascii")
    response.set_cookie(
        FLASH_COOKIE, sealed, max_age=FLASH_MAX_AGE_SECONDS,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
    )


def read_flashes(raw: str | None) -> list[dict]:
    """Decode the flash cookie. Anything unreadable -- tampered with, or
    written under a previous encryption key -- is simply dropped."""
    if not raw:
        return []
    try:
        decrypted = FERNET.decrypt(raw.encode("ascii"), ttl=FLASH_MAX_AGE_SECONDS)
        messages = json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, UnicodeEncodeError, ValueError, TypeError):
        return []
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict) and "text" in m]
