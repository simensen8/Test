"""The security and robustness fixes from the code audit.

Each test here failed before the fix it covers.
"""
import datetime
import io
import re

import pytest
from PIL import Image
from sqlalchemy import select, text

from app.config import FERNET
from app.db import engine
from app.matching import get_or_create_participant
from app.models import AuditLog, ParticipantStatus, Role, User
from app.security import create_session_token, decode_session_token, hash_password, refresh_session_token


def _csrf(client, path):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


# ------------------------------------------- PHI in the audit log (C1) ----

def test_audit_details_are_encrypted_at_rest(client, db_session):
    """The audit log names participants and what happened to them. In
    plaintext it undid the point of encrypting every other column."""
    person, _, _ = get_or_create_participant(db_session, "Zzyzxqueen, Bartholomewia")
    db_session.commit()

    client.post(f"/participants/{person.id}/edit", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "full_name": "Zzyzxqueen, Bartholomewia", "external_id": "", "status": "active", "notes": "",
    }, follow_redirects=False)

    with engine.connect() as conn:
        stored = conn.execute(text("SELECT detail FROM audit_log WHERE detail IS NOT NULL")).fetchall()
    assert stored, "the action should have been audited"
    for (raw,) in stored:
        assert "Zzyzxqueen" not in raw
        FERNET.decrypt(raw.encode("ascii"))  # raises if it isn't ciphertext

    # ...and still reads back through the app.
    entries = db_session.scalars(select(AuditLog).where(AuditLog.detail.isnot(None))).all()
    assert any("status=" in (e.detail or "") for e in entries)


def test_flash_messages_do_not_ride_in_a_readable_cookie(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Zzyzxqueen, Bartholomewia")
    db_session.commit()

    resp = client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "payer_source": "Medicaid", "rate": "", "grant_rule_type": "none",
        "grant_cycle_length": "", "grant_cycle_secondary_days": "1", "grant_payer": "",
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    assert "Zzyzxqueen" not in resp.headers.get("set-cookie", "")
    # The message still reaches the next page.
    assert "Zzyzxqueen" in client.get(f"/participants/{person.id}").text


# ------------------------------------------------ session lifetime (H3) ----

def test_an_active_session_still_expires_absolutely():
    """Refreshing re-signs the cookie, so the signature age can't be the
    absolute cap -- it has to be measured from when the session began."""
    user = User(id="u1", email="a@b.c", display_name="A", password_hash="x")
    payload = decode_session_token(create_session_token(user))

    payload["issued_at"] = (datetime.datetime.utcnow() - datetime.timedelta(days=3)).isoformat()
    payload["last_seen"] = datetime.datetime.utcnow().isoformat()

    assert decode_session_token(refresh_session_token(payload)) is None


def test_a_malformed_but_signed_payload_is_refused():
    from app.security import _serializer
    assert decode_session_token(_serializer.dumps({"uid": "u1"})) is None
    assert decode_session_token(_serializer.dumps("not-a-dict")) is None


# -------------------------------------------- password invalidates (M6) ----

def test_an_admin_reset_signs_the_user_out_everywhere(client, db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    target = User(email="reviewer@example.org", display_name="R",
                  password_hash=hash_password("their own password"), role=Role.REVIEWER,
                  must_change_password=False)
    db_session.add(target)
    db_session.commit()

    theirs = TestClient(app)
    assert theirs.post("/login", data={"email": "reviewer@example.org",
                                       "password": "their own password"},
                       follow_redirects=False).status_code == 303
    assert theirs.get("/dashboard", follow_redirects=False).status_code == 200

    client.post(f"/admin/users/{target.id}/reset-password", data={
        "csrf_token": _csrf(client, "/admin/users"), "temp_password": "a new temporary one",
    }, follow_redirects=False)

    resp = theirs.get("/dashboard", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_changing_your_own_password_keeps_you_signed_in(client, db_session):
    """The session doing the changing is re-issued, so the person who
    just typed their new password isn't thrown out along with everyone
    else."""
    resp = client.post("/account/password", data={
        "csrf_token": _csrf(client, "/account/password"),
        "current_password": "correct horse battery",
        "new_password": "a better one entirely",
        "new_password_confirm": "a better one entirely",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert client.get("/dashboard", follow_redirects=False).status_code == 200


# ----------------------------------------------- first-run setup (C2) ----

def test_setup_needs_the_token(db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    anonymous = TestClient(app)
    resp = anonymous.post("/setup", data={
        "display_name": "Impostor", "email": "impostor@example.org",
        "password": "a long enough password", "password_confirm": "a long enough password",
        "setup_token": "guessed",
    }, follow_redirects=False)

    assert resp.status_code == 400
    assert "setup token" in resp.text
    assert db_session.scalars(select(User)).all() == [], "no account should have been created"


def test_setup_works_with_the_right_token(db_session):
    from fastapi.testclient import TestClient

    from app.config import SETUP_TOKEN
    from app.main import app

    resp = TestClient(app).post("/setup", data={
        "display_name": "Owner", "email": "owner@example.org",
        "password": "a long enough password", "password_confirm": "a long enough password",
        "setup_token": SETUP_TOKEN,
    }, follow_redirects=False)

    assert resp.status_code == 303
    created = db_session.scalars(select(User)).all()
    assert [u.email for u in created] == ["owner@example.org"]
    assert created[0].role == Role.ADMIN


# ------------------------------------------------------ input guards ----

@pytest.mark.parametrize("path", [
    "/reports/not-a-date",
    "/reports/2026-13-45",
    "/reports/not-a-date/export.csv",
    "/batches/not-a-date/review",
])
def test_a_malformed_date_in_a_url_redirects_rather_than_crashing(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 303


def test_uploading_nothing_is_refused_politely(client):
    for path, data in [("/upload/attendance", {"week_start": "2026-09-07"}),
                       ("/upload/rate-master", {})]:
        resp = client.post(path, data={"csrf_token": _csrf(client, "/upload"), **data},
                           follow_redirects=False)
        assert resp.status_code == 303, path


def test_a_file_that_is_not_a_workbook_is_refused_politely(client, db_session):
    from app.models import Upload

    resp = client.post("/upload/attendance", data={
        "csrf_token": _csrf(client, "/upload"), "week_start": "2026-09-07",
    }, files={"file": ("notes.xlsx", b"this is not a spreadsheet", "application/vnd.ms-excel")},
        follow_redirects=False)

    assert resp.status_code == 303
    assert db_session.scalars(select(Upload)).all() == [], \
        "nothing is stored for a file that couldn't be read"


def test_a_typed_rate_is_refused_rather_than_crashing(client, db_session):
    from app.models import RateRule

    resp = client.post("/rate-master/add", data={
        "csrf_token": _csrf(client, "/rate-master"), "participant_name": "Adams, Alice",
        "payer_source": "Medicaid", "rate": "ninety five",
    }, follow_redirects=False)

    assert resp.status_code == 303
    assert db_session.scalars(select(RateRule)).all() == []


def test_an_unknown_status_is_refused(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    client.post(f"/participants/{person.id}/edit", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "full_name": "Adams, Alice", "external_id": "", "status": "deleted", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    assert db_session.get(type(person), person.id).status == ParticipantStatus.ACTIVE


# ------------------------------------------------- deleting a row (H6) ----

def test_a_reconciled_billing_row_can_still_be_deleted(client, db_session):
    """The row is referenced by the exceptions raised against it, so
    deleting it used to fail on the foreign key."""
    import datetime as dt

    from app.models import AttendanceRecord, BillingRecord, Exception_, Upload, UploadKind
    from app.reconcile import run_reconciliation

    day = dt.date(2026, 9, 10)
    user = db_session.scalar(select(User))
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=day)
    db_session.add(upload)
    db_session.flush()
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.add(AttendanceRecord(participant_id=person.id, date=day, attended=False,
                                    source_upload_id=upload.id))
    row = BillingRecord(raw_name="Adams, Alice", date=day, payer_source="Private Pay",
                        source_upload_id=upload.id, verified=True)
    db_session.add(row)
    db_session.commit()
    row_id = row.id

    run_reconciliation(db_session, day, user.id)
    db_session.commit()
    assert db_session.scalars(select(Exception_)).all()

    resp = client.post(f"/batches/{day.isoformat()}/review/delete/{row_id}", data={
        "csrf_token": _csrf(client, f"/batches/{day.isoformat()}/review"),
    }, follow_redirects=False)

    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(BillingRecord, row_id) is None


# ------------------------------------------------------ photo size (H7) ----

def test_an_image_that_would_eat_the_server_is_refused(client, db_session):
    """A small file can decode to hundreds of megabytes; the check is on
    the pixel count in the header, before anything is allocated."""
    from app.models import Participant
    from app.photos import PhotoError, normalize_photo

    buf = io.BytesIO()
    Image.new("RGB", (9000, 9000), (10, 10, 10)).save(buf, format="PNG", optimize=False)
    assert len(buf.getvalue()) < 1_000_000, "the point is that it's a small file"

    with pytest.raises(PhotoError):
        normalize_photo(buf.getvalue())

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()
    client.post(f"/participants/{person.id}/photo", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"), "consent": "yes",
    }, files={"photo": ("huge.png", buf.getvalue(), "image/png")}, follow_redirects=False)

    db_session.expire_all()
    assert db_session.get(Participant, person.id).photo_path is None


# ------------------------------------------------------- headers (M4/M5) ----

def test_the_api_schema_is_not_published(client):
    assert client.get("/openapi.json", follow_redirects=False).status_code == 404


def test_responses_carry_the_security_headers(client):
    headers = client.get("/dashboard").headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_a_forged_forwarded_header_does_not_land_in_the_audit_log(client, db_session):
    client.get("/dashboard", headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.9"})
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "view_dashboard").order_by(AuditLog.at.desc())
    ).first()
    assert entry.ip_address == "10.0.0.9", "the proxy's own observation, not the caller's claim"


# ------------------------------------------------- last administrator ----

def test_the_last_administrator_cannot_be_deactivated(client, db_session):
    other = User(email="second@example.org", display_name="S",
                 password_hash=hash_password("x" * 12), role=Role.ADMIN, must_change_password=False)
    db_session.add(other)
    db_session.commit()

    # Two admins: deactivating one is fine.
    client.post(f"/admin/users/{other.id}/deactivate", data={
        "csrf_token": _csrf(client, "/admin/users")}, follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(User, other.id).active is False

    # The remaining one can't be removed by anyone (including itself).
    signed_in = db_session.scalar(select(User).where(User.email == "director@example.org"))
    client.post(f"/admin/users/{signed_in.id}/deactivate", data={
        "csrf_token": _csrf(client, "/admin/users")}, follow_redirects=False)
    db_session.expire_all()
    assert db_session.get(User, signed_in.id).active is True


# ------------------------------------------------- concurrent marks (H8) ----

def test_two_marks_landing_at_once_do_not_error(client, db_session):
    """Staff double-tap, and two people can mark the same participant at
    the same moment. The database keeps one row per person per day; the
    request that loses that race used to return a 500."""
    import threading

    from app.models import AttendanceRecord, AttendanceSchedule

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.add(AttendanceSchedule(participant_id=person.id, days_of_week="0,1,2,3,4"))
    db_session.commit()

    token = _csrf(client, "/check-in")
    statuses: list[int] = []

    def mark():
        statuses.append(client.post("/check-in/mark", data={
            "csrf_token": token, "participant_id": person.id,
            "day": "2026-09-07", "attended": "present",
        }, follow_redirects=False).status_code)

    threads = [threading.Thread(target=mark) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(statuses) == {303}, f"every mark should be accepted, got {sorted(statuses)}"
    db_session.expire_all()
    assert len(db_session.scalars(select(AttendanceRecord)).all()) == 1
