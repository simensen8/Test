from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, ORGANIZATION_NAME
from app.csrf import get_csrf_token
from app.models import Role
from app.flash import FLASH_COOKIE, read_flashes
from app.security import SESSION_COOKIE_NAME

# Absolute, so rendering doesn't depend on the working directory the
# process happened to start in.
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def render(request: Request, template_name: str, context: dict | None = None, user=None, status_code: int = 200):
    ctx = dict(context or {})
    ctx["request"] = request
    ctx["org_name"] = ORGANIZATION_NAME
    ctx["user"] = user
    # Templates hide what the signed-in user may not do. The server
    # enforces it regardless -- this only keeps buttons off the screen
    # that would answer 403 if pressed.
    ctx["is_admin"] = bool(user is not None and user.role == Role.ADMIN)
    ctx["csrf_token"] = get_csrf_token(request) if request.cookies.get(SESSION_COOKIE_NAME) else ""

    raw_flash = request.cookies.get(FLASH_COOKIE)
    ctx["flashes"] = read_flashes(raw_flash)

    response = templates.TemplateResponse(request, template_name, ctx, status_code=status_code)
    if raw_flash:
        response.delete_cookie(FLASH_COOKIE)
    return response
