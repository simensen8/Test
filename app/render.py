import json

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import ORGANIZATION_NAME
from app.csrf import get_csrf_token
from app.flash import FLASH_COOKIE

templates = Jinja2Templates(directory="app/templates")


def render(request: Request, template_name: str, context: dict | None = None, user=None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    ctx["org_name"] = ORGANIZATION_NAME
    ctx["user"] = user
    ctx["csrf_token"] = get_csrf_token(request) if request.cookies.get("adp_session") else ""

    raw_flash = request.cookies.get(FLASH_COOKIE)
    flashes = []
    if raw_flash:
        try:
            flashes = json.loads(raw_flash)
        except (ValueError, TypeError):
            flashes = []
    ctx["flashes"] = flashes

    response = templates.TemplateResponse(template_name, ctx, status_code=status_code)
    if raw_flash:
        response.delete_cookie(FLASH_COOKIE)
    return response
