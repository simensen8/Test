"""Changing your own password, and the temporary-password lockout.

`must_change_password` was written when an admin created an account but
never read anywhere, so a temporary password -- one that by definition
has been shared with someone -- stayed valid indefinitely and there was
no screen on which to change it.
"""
import re

import pytest
from sqlalchemy import select

from app.models import AuditLog, Role, User
from app.security import hash_password, verify_password

TEMP = "temporary pass 1"
NEW = "a much better one"


@pytest.fixture()
def reviewer(db_session):
    user = User(
        email="reviewer@example.org", display_name="Reviewer",
        password_hash=hash_password(TEMP), role=Role.REVIEWER, must_change_password=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


def _login(email, password):
    from fastapi.testclient import TestClient

    from app.main import app
    test_client = TestClient(app)
    resp = test_client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return test_client


def _csrf(test_client, path="/account/password"):
    match = re.search(r'name="csrf_token" value="([^"]+)"', test_client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def test_a_temporary_password_cannot_reach_phi(reviewer):
    c = _login("reviewer@example.org", TEMP)
    for path in ("/dashboard", "/upload", "/rate-master"):
        resp = c.get(path, follow_redirects=False)
        assert resp.status_code == 303, path
        assert resp.headers["location"] == "/account/password", path


def test_the_change_password_screen_is_reachable_while_locked_out(reviewer):
    c = _login("reviewer@example.org", TEMP)
    resp = c.get("/account/password")
    assert resp.status_code == 200
    assert "temporary password" in resp.text


def test_changing_the_password_lifts_the_lockout(reviewer, db_session):
    c = _login("reviewer@example.org", TEMP)
    resp = c.post("/account/password", data={
        "csrf_token": _csrf(c), "current_password": TEMP,
        "new_password": NEW, "new_password_confirm": NEW,
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"

    db_session.expire_all()
    user = db_session.get(User, reviewer.id)
    assert user.must_change_password is False
    assert verify_password(NEW, user.password_hash)
    assert not verify_password(TEMP, user.password_hash)

    assert c.get("/dashboard", follow_redirects=False).status_code == 200


def test_the_old_password_stops_working(reviewer):
    c = _login("reviewer@example.org", TEMP)
    c.post("/account/password", data={
        "csrf_token": _csrf(c), "current_password": TEMP,
        "new_password": NEW, "new_password_confirm": NEW,
    }, follow_redirects=False)

    from fastapi.testclient import TestClient

    from app.main import app
    resp = TestClient(app).post(
        "/login", data={"email": "reviewer@example.org", "password": TEMP}, follow_redirects=False,
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("payload, expected", [
    ({"current_password": "wrong password here", "new_password": NEW, "new_password_confirm": NEW},
     "current password is not correct"),
    ({"current_password": TEMP, "new_password": "short", "new_password_confirm": "short"},
     "at least 12 characters"),
    ({"current_password": TEMP, "new_password": NEW, "new_password_confirm": "something else"},
     "do not match"),
    ({"current_password": TEMP, "new_password": TEMP, "new_password_confirm": TEMP},
     "must be different"),
])
def test_bad_submissions_are_refused(reviewer, db_session, payload, expected):
    c = _login("reviewer@example.org", TEMP)
    resp = c.post("/account/password", data={"csrf_token": _csrf(c), **payload})
    assert resp.status_code == 400
    assert expected in resp.text

    db_session.expire_all()
    user = db_session.get(User, reviewer.id)
    assert verify_password(TEMP, user.password_hash), "the password must be unchanged"
    assert user.must_change_password is True


def test_the_change_is_audited_without_the_password(reviewer, db_session):
    c = _login("reviewer@example.org", TEMP)
    c.post("/account/password", data={
        "csrf_token": _csrf(c), "current_password": TEMP,
        "new_password": NEW, "new_password_confirm": NEW,
    }, follow_redirects=False)

    entries = db_session.scalars(select(AuditLog).where(AuditLog.action == "password_changed")).all()
    assert len(entries) == 1
    assert entries[0].user_email_snapshot == "reviewer@example.org"
    for entry in db_session.scalars(select(AuditLog)).all():
        assert NEW not in (entry.detail or ""), "a password must never reach the audit log"
        assert TEMP not in (entry.detail or "")


def test_an_admin_reset_forces_another_change(db_session):
    """A forgotten password has to be recoverable -- there's no email
    reset by design -- and the password the admin hands over is itself
    temporary."""
    admin = User(email="admin@example.org", display_name="Admin",
                 password_hash=hash_password("admin password 1"), role=Role.ADMIN,
                 must_change_password=False)
    target = User(email="user@example.org", display_name="User",
                  password_hash=hash_password("forgotten password"), role=Role.REVIEWER,
                  must_change_password=False)
    db_session.add_all([admin, target])
    db_session.commit()

    c = _login("admin@example.org", "admin password 1")
    resp = c.post(f"/admin/users/{target.id}/reset-password", data={
        "csrf_token": _csrf(c, "/admin/users"), "temp_password": "issued temporarily",
    }, follow_redirects=False)
    assert resp.status_code == 303

    db_session.expire_all()
    refreshed = db_session.get(User, target.id)
    assert refreshed.must_change_password is True
    assert verify_password("issued temporarily", refreshed.password_hash)

    theirs = _login("user@example.org", "issued temporarily")
    assert theirs.get("/dashboard", follow_redirects=False).headers["location"] == "/account/password"


def test_a_short_reset_password_is_refused(db_session):
    admin = User(email="admin2@example.org", display_name="Admin", password_hash=hash_password("admin password 1"),
                 role=Role.ADMIN, must_change_password=False)
    target = User(email="user2@example.org", display_name="User", password_hash=hash_password("their password"),
                  role=Role.REVIEWER, must_change_password=False)
    db_session.add_all([admin, target])
    db_session.commit()

    c = _login("admin2@example.org", "admin password 1")
    c.post(f"/admin/users/{target.id}/reset-password", data={
        "csrf_token": _csrf(c, "/admin/users"), "temp_password": "short",
    }, follow_redirects=False)

    db_session.expire_all()
    refreshed = db_session.get(User, target.id)
    assert verify_password("their password", refreshed.password_hash), "the password must be unchanged"
