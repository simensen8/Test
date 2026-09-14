import json

from fastapi import Request
from fastapi.responses import Response

from app.config import COOKIE_SECURE

FLASH_COOKIE = "adp_flash"


def set_flash(response: Response, text: str, category: str = "success"):
    payload = json.dumps([{"text": text, "category": category}])
    response.set_cookie(FLASH_COOKIE, payload, max_age=30, httponly=True, secure=COOKIE_SECURE, samesite="lax")


def pop_flashes(request: Request, response: Response) -> list[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return []
    response.delete_cookie(FLASH_COOKIE)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []
