from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import COOKIE_SECURE, SESSION_ABSOLUTE_TIMEOUT_HOURS
from app.db import Base, engine
from app.migrations import run_migrations
from app.routers import (
    account, admin, auth, batches, calendar, checkin, dashboard, participants, rate_master,
    reports, uploads,
)
from app.security import SESSION_COOKIE_NAME, refresh_session_token

# create_all builds any wholly new table (and a fresh database in full);
# the migrations then add columns to tables that already existed, which
# create_all leaves alone. On a fresh database they find nothing to do.
Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Adult Day Program Billing Reconciliation", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

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
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(StarletteHTTPException)
async def redirect_on_auth_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 303 and exc.headers and exc.headers.get("Location"):
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")
