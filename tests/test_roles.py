"""What a reviewer may and may not do.

Payer rules decide what reconciliation expects, merging deletes a
participant record, and deleting a billing row removes evidence of a
charge -- so those belong to administrators. Everything the daily job
needs stays open to reviewers.

Each test drives a real reviewer session against the URL directly, so a
pass means the server refused, not that a button was hidden.
"""
import datetime
import re

import pytest
from sqlalchemy import select

from app.matching import get_or_create_participant
from app.models import (
    AttendanceSchedule,
    BillingRecord,
    Participant,
    RateRule,
    Role,
    Upload,
    UploadKind,
    User,
)
from app.security import hash_password

DAY = datetime.date(2026, 9, 10)


@pytest.fixture()
def reviewer_client(db_session):
    """A signed-in reviewer."""
    from fastapi.testclient import TestClient

    from app.main import app

    db_session.add(User(
        email="reviewer@example.org", display_name="Reviewer",
        password_hash=hash_password("a reviewer password"), role=Role.REVIEWER,
        must_change_password=False,
    ))
    db_session.commit()

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/login", data={"email": "reviewer@example.org",
                                       "password": "a reviewer password"}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def _csrf(client, path):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    return match.group(1) if match else "no-token-on-page"


def _billing_row(db) -> BillingRecord:
    user = db.scalar(select(User))
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=DAY)
    db.add(upload)
    db.flush()
    row = BillingRecord(raw_name="Adams, Alice", date=DAY, payer_source="Private Pay",
                        source_upload_id=upload.id, verified=False)
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------- refused to a reviewer ----

def test_a_reviewer_cannot_change_how_someone_is_billed(reviewer_client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    resp = reviewer_client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(reviewer_client, f"/participants/{person.id}"),
        "payer_source": "Medicaid", "rate": "", "grant_rule_type": "none",
        "grant_cycle_length": "", "grant_cycle_secondary_days": "1", "grant_payer": "",
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 403
    assert db_session.scalars(select(RateRule)).all() == []


def test_a_reviewer_cannot_edit_or_remove_an_existing_rule(reviewer_client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    rule = RateRule(participant_id=person.id, payer_source="Medicaid")
    db_session.add(rule)
    db_session.commit()

    token = _csrf(reviewer_client, f"/participants/{person.id}")
    edit = reviewer_client.post(f"/participants/{person.id}/rules/{rule.id}/edit", data={
        "csrf_token": token, "payer_source": "VA", "rate": "", "grant_rule_type": "none",
        "grant_cycle_length": "", "grant_cycle_secondary_days": "1", "grant_payer": "",
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)
    remove = reviewer_client.post(f"/participants/{person.id}/rules/{rule.id}/remove",
                                  data={"csrf_token": token}, follow_redirects=False)

    assert edit.status_code == 403
    assert remove.status_code == 403
    db_session.expire_all()
    surviving = db_session.scalar(select(RateRule))
    assert surviving.payer_source == "Medicaid"
    assert surviving.active is True


def test_a_reviewer_cannot_merge_participants(reviewer_client, db_session):
    keep, _, _ = get_or_create_participant(db_session, "Hall, Tresea")
    fold, _, _ = get_or_create_participant(db_session, "Hall, Tressa")
    db_session.commit()
    fold_id = fold.id

    resp = reviewer_client.post(f"/participants/{fold.id}/merge", data={
        "csrf_token": _csrf(reviewer_client, f"/participants/{fold.id}"), "target_id": keep.id,
    }, follow_redirects=False)

    assert resp.status_code == 403
    db_session.expire_all()
    assert db_session.get(Participant, fold_id) is not None, "no participant was deleted"


def test_a_reviewer_cannot_delete_a_billing_row(reviewer_client, db_session):
    row = _billing_row(db_session)
    db_session.commit()
    row_id = row.id

    resp = reviewer_client.post(f"/batches/{DAY.isoformat()}/review/delete/{row_id}", data={
        "csrf_token": _csrf(reviewer_client, f"/batches/{DAY.isoformat()}/review"),
    }, follow_redirects=False)

    assert resp.status_code == 403
    db_session.expire_all()
    assert db_session.get(BillingRecord, row_id) is not None


def test_a_reviewer_cannot_reach_the_importers(reviewer_client, db_session):
    assert reviewer_client.get("/admin/import", follow_redirects=False).status_code == 403
    for path in ["/admin/import/roster", "/admin/import/rates"]:
        resp = reviewer_client.post(path, data={"csrf_token": "x", "week_start": "2026-09-07"},
                                    follow_redirects=False)
        assert resp.status_code == 403, path


def test_a_reviewer_cannot_edit_rules_through_the_rate_master_page(reviewer_client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    rule = RateRule(participant_id=person.id, payer_source="Medicaid")
    db_session.add(rule)
    db_session.commit()

    token = _csrf(reviewer_client, "/rate-master")
    for path, data in [
        (f"/rate-master/{rule.id}/edit", {"payer_source": "VA"}),
        (f"/rate-master/{rule.id}/deactivate", {}),
        ("/rate-master/add", {"participant_name": "New, Person", "payer_source": "VA"}),
    ]:
        resp = reviewer_client.post(path, data={"csrf_token": token, **data}, follow_redirects=False)
        assert resp.status_code == 403, path

    db_session.expire_all()
    assert db_session.scalar(select(RateRule)).payer_source == "Medicaid"


# -------------------------------------------- still open to a reviewer ----

def test_a_reviewer_can_still_do_the_daily_job(reviewer_client, db_session):
    """Check-in, uploading a batch, verifying rows, reconciling and
    resolving exceptions are the job -- none of them are admin work."""
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.add(AttendanceSchedule(participant_id=person.id, days_of_week="0,1,2,3,4"))
    db_session.commit()

    marked = reviewer_client.post("/check-in/mark", data={
        "csrf_token": _csrf(reviewer_client, "/check-in"), "participant_id": person.id,
        "day": "2026-09-07", "attended": "present",
    }, follow_redirects=False)
    assert marked.status_code == 303

    batch = reviewer_client.post("/upload/pcc-batch", data={
        "csrf_token": _csrf(reviewer_client, "/upload"),
    }, files=[("files", ("b.html", b"<html><body>not a batch table</body></html>", "text/html"))],
        follow_redirects=False)
    assert batch.status_code in (200, 303)

    reconcile = reviewer_client.post(f"/reports/{DAY.isoformat()}/reconcile", data={
        "csrf_token": _csrf(reviewer_client, f"/reports/{DAY.isoformat()}"),
    }, follow_redirects=False)
    assert reconcile.status_code == 303


def test_a_reviewer_can_still_read_everything(reviewer_client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    for path in ["/dashboard", "/check-in", "/calendar", "/upload", "/participants",
                 f"/participants/{person.id}", "/rate-master",
                 f"/reports/{DAY.isoformat()}", f"/batches/{DAY.isoformat()}/review"]:
        assert reviewer_client.get(path, follow_redirects=False).status_code == 200, path


def test_the_admin_only_controls_are_not_rendered_for_a_reviewer(reviewer_client, db_session):
    """The server refuses regardless, but a button that always fails is
    its own kind of bug."""
    keep, _, _ = get_or_create_participant(db_session, "Hall, Tresea")
    get_or_create_participant(db_session, "Hall, Tressa")
    db_session.add(RateRule(participant_id=keep.id, payer_source="Medicaid"))
    db_session.commit()

    profile = reviewer_client.get(f"/participants/{keep.id}").text
    assert "/rules/add" not in profile
    assert "/merge" not in profile
    assert "An administrator can change this." in profile

    rates = reviewer_client.get("/rate-master").text
    assert "/rate-master/add" not in rates
    assert "Medicaid" in rates, "the rule is still visible, just not editable"

    assert "/admin/import" not in reviewer_client.get("/upload").text
