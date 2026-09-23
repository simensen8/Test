import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import (
    BASE_DIR,
    COOKIE_SECURE,
    DATA_DIR,
    ENCRYPTION_KEY_IS_GENERATED,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SETUP_TOKEN_FILE,
)
from app.db import Base, engine
from app.migrations import run_migrations
from app.models import User
from app.render import render
from app.routers import (
    account, admin, auth, batches, calendar, checkin, dashboard, participants, rate_master,
    reports, uploads,
)
from app.security import SESSION_COOKIE_NAME, refresh_session_token

logger = logging.getLogger(__name__)

STATIC_DIR = BASE_DIR / "app" / "static"

# create_all builds any wholly new table (and a fresh database in full);
# the migrations then add columns to tables that already existed, which
# create_all leaves alone. On a fresh database they find nothing to do.
Base.metadata.create_all(bind=engine)
run_migrations()


def _startup_notices() -> None:
    """Say the two things an operator can't find out any other way."""
    with Session(engine) as session:
        has_users = session.scalar(select(func.count()).select_from(User)) or 0

    if not has_users:
        logger.warning(
            "No accounts exist yet. Open /setup and use the setup token in %s to create the "
            "first administrator.", SETUP_TOKEN_FILE,
        )
    if ENCRYPTION_KEY_IS_GENERATED:
        logger.warning(
            "APP_ENCRYPTION_KEY is not set, so the key in %s is being used. Every encrypted "
            "record depends on that file: back it up, and if it is ever lost the data cannot "
            "be recovered.", DATA_DIR / ".encryption_key",
        )


_startup_notices()

# openapi_url is off alongside the docs pages: the schema is served
# without authentication and maps every route and form field of a system
# holding PHI, which is a free reconnaissance pass for anyone who finds
# the domain. Nothing here is a public API.
app = FastAPI(
    title="Adult Day Program Billing Reconciliation",
    docs_url=None, redoc_url=None, openapi_url=None,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(account.router)
app.include_router(dashboard.router)
app.include_router(uploads.router)
app.include_router(batches.router)
app.include_router(reports.router)
app.include_router(checkin.router)
app.include_router(calendar.router)
app.include_router(participants.router)
app.include_router(rate_master.router)
app.include_router(admin.router)


@app.middleware("http")
async def security_headers_and_session_refresh(request: Request, call_next):
    response = await call_next(request)
    # Sliding-expiration refresh of the session's idle timer.
    if getattr(request.state, "needs_refresh", False) and hasattr(request.state, "session_payload"):
        new_token = refresh_session_token(request.state.session_payload)
        response.set_cookie(
            SESSION_COOKIE_NAME, new_token,
            httponly=True, secure=COOKIE_SECURE, samesite="lax",
            max_age=SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # No third-party scripts, styles, frames or form targets: the whole
    # app is server-rendered from its own origin, so the policy that
    # matches what it does is also the strictest one available.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/static/"):
        # Stylesheets carry no PHI and change only on deploy.
        response.headers["Cache-Control"] = "public, max-age=3600"
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    """Answer in HTML, since every caller here is a browser."""
    if exc.status_code == 303 and exc.headers and exc.headers.get("Location"):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return render(request, "error.html", {
        "status_code": exc.status_code,
        "message": exc.detail if isinstance(exc.detail, str) else "Something went wrong.",
    }, status_code=exc.status_code)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    """A page rather than a blank 500, with the detail kept to the log.

    What went wrong is never shown to the user: the exception text can
    carry a row of a PHI-bearing file straight onto the screen.
    """
    logger.exception("Unhandled error serving %s", request.url.path)
    return render(request, "error.html", {
        "status_code": 500,
        "message": "Something went wrong at our end. Nothing was saved -- please try again, and "
                   "tell your administrator if it keeps happening.",
    }, status_code=500)


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/healthz")
def healthz():
    """Liveness for the container runtime. Deliberately says nothing
    about the data -- it is the one route reachable without signing in."""
    return {"status": "ok"}
