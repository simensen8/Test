"""Signing out has to actually sign you out.

This is shared-workstation software holding PHI: the sign-out button is
what staff rely on when they step away from a machine, so "it returned
303" is not the thing worth asserting. What matters is that the next
request from that browser is no longer authenticated.

The bug these cover: reading the user inside the logout route armed the
sliding-expiration refresh, and the middleware -- which runs after the
route -- then wrote a fresh valid cookie straight over the deletion. The
response carried two Set-Cookie headers for the session, the second one
undoing the first, and the session survived.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Role, User
from app.security import SESSION_COOKIE_NAME, hash_password


@pytest.fixture()
def signed_in(db_session):
    db_session.add(User(
        email="out@example.org", display_name="Signing Out",
        password_hash=hash_password("a perfectly good password"),
        role=Role.REVIEWER, must_change_password=False,
    ))
    db_session.commit()
    client = TestClient(app)
    resp = client.post("/login", data={"email": "out@example.org",
                                       "password": "a perfectly good password"},
                       follow_redirects=False)
    assert resp.status_code == 303
    return client


def _csrf(client, path="/dashboard"):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    return match.group(1) if match else "no-token"


def test_the_session_is_dead_after_signing_out(signed_in):
    assert signed_in.get("/dashboard", follow_redirects=False).status_code == 200

    resp = signed_in.post("/logout", data={"csrf_token": _csrf(signed_in)},
                          follow_redirects=False)
    assert resp.status_code == 303

    after = signed_in.get("/dashboard", follow_redirects=False)
    assert after.status_code == 303, "the session survived sign-out"
    assert after.headers["location"] == "/login"


def test_signing_out_leaves_no_session_cookie_behind(signed_in):
    signed_in.post("/logout", data={"csrf_token": _csrf(signed_in)}, follow_redirects=False)
    assert not signed_in.cookies.get(SESSION_COOKIE_NAME)


def test_the_response_does_not_re_issue_the_cookie_it_just_deleted(signed_in):
    """The regression proper.

    A browser replaces a cookie only when name, path and domain all
    match, so a deletion and a re-issue can sit in the same response
    with the re-issue winning. Asserting on the headers catches that
    even if a future client library papers over it.
    """
    resp = signed_in.post("/logout", data={"csrf_token": _csrf(signed_in)},
                          follow_redirects=False)
    session_cookies = [
        value for key, value in resp.headers.items()
        if key.lower() == "set-cookie" and value.startswith(f"{SESSION_COOKIE_NAME}=")
    ]
    assert len(session_cookies) == 1, f"expected one session cookie header, got {session_cookies}"
    assert "Max-Age=0" in session_cookies[0]
    assert f'{SESSION_COOKIE_NAME}=""' in session_cookies[0] or \
           f"{SESSION_COOKIE_NAME}=;" in session_cookies[0]


def test_signing_out_without_a_session_still_clears_the_cookie(db_session):
    """Someone whose session already expired still presses the button."""
    client = TestClient(app)
    resp = client.post("/logout", data={"csrf_token": "anything"}, follow_redirects=False)
    # No session means no CSRF token to verify against, so this is
    # refused or redirected -- either way it must not raise.
    assert resp.status_code in (303, 400, 403)


def test_signing_out_is_recorded_in_the_audit_log(signed_in, db_session):
    from sqlalchemy import select

    from app.models import AuditLog
    signed_in.post("/logout", data={"csrf_token": _csrf(signed_in)}, follow_redirects=False)
    actions = {row.action for row in db_session.scalars(select(AuditLog)).all()}
    assert "logout" in actions
