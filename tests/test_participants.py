"""The roster: seeing who the program serves, correcting a record, and
merging two records that turned out to be one person."""
import datetime
import re

from sqlalchemy import select

from app.matching import fuzzy_find_participant, get_or_create_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    GrantRuleType,
    Participant,
    ParticipantStatus,
    RateRule,
    Role,
    Upload,
    UploadKind,
    User,
)
from app.reconcile import compute_expected_billing, run_reconciliation
from app.security import hash_password

DAY = datetime.date(2026, 9, 10)


def _csrf(client, path="/participants"):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def _upload(db, kind=UploadKind.WEEKLY_ATTENDANCE):
    user = db.scalar(select(User))
    if user is None:
        user = User(email="x@example.org", display_name="X",
                    password_hash=hash_password("x" * 12), role=Role.ADMIN)
        db.add(user)
        db.flush()
    up = Upload(kind=kind, original_filename="f", stored_path="", uploaded_by_id=user.id)
    db.add(up)
    db.flush()
    return up


# ------------------------------------------------------------- roster ----

def test_the_roster_lists_everyone_uploads_created(client, db_session):
    get_or_create_participant(db_session, "Hall, Tresea")
    get_or_create_participant(db_session, "Arocho, Leonidas (PAM-6)")
    db_session.commit()

    body = client.get("/participants").text
    assert "Hall, Tresea" in body
    assert "Arocho, Leonidas" in body
    assert "PAM-6" in body


def test_a_shared_surname_is_called_out_on_the_roster(client, db_session):
    get_or_create_participant(db_session, "Hall, Tresea")
    get_or_create_participant(db_session, "Hall, Tressa")
    db_session.commit()

    assert "shared surname" in client.get("/participants").text


def test_searching_narrows_the_roster(client, db_session):
    get_or_create_participant(db_session, "Hall, Tresea")
    get_or_create_participant(db_session, "Arocho, Leonidas (PAM-6)")
    db_session.commit()

    body = client.get("/participants?q=hall").text
    assert "Hall, Tresea" in body
    assert "Arocho" not in body

    by_id = client.get("/participants?q=PAM-6").text
    assert "Arocho, Leonidas" in by_id
    assert "Hall, Tresea" not in by_id


def test_someone_can_be_added_by_hand(client, db_session):
    resp = client.post("/participants/new", data={
        "csrf_token": _csrf(client), "full_name": "Newcomer, Nora",
        "external_id": "PAM-900", "status": "trial",
    }, follow_redirects=False)
    assert resp.status_code == 303

    person = db_session.scalar(select(Participant))
    assert person.full_name == "Newcomer, Nora"
    assert person.external_id == "PAM-900"
    assert person.status == ParticipantStatus.TRIAL
    # And a batch row for them resolves straight away.
    assert fuzzy_find_participant(db_session, "Newcomer, Nora (PAM-900)").participant.id == person.id


# ------------------------------------------------------------ profile ----

def test_correcting_a_name_keeps_batch_rows_matching(client, db_session):
    """A corrected spelling has to stay findable: the surname index is
    derived from the name, so it has to be rebuilt when the name changes."""
    person, _, _ = get_or_create_participant(db_session, "DelMage, Ann")
    db_session.commit()

    client.post(f"/participants/{person.id}/edit", data={
        "csrf_token": _csrf(client), "full_name": "Del Mage, Ann",
        "external_id": "PAM-77", "status": "active", "notes": "Transport by family.",
    }, follow_redirects=False)

    db_session.expire_all()
    refreshed = db_session.get(Participant, person.id)
    assert refreshed.full_name == "Del Mage, Ann"
    assert refreshed.notes == "Transport by family."
    assert fuzzy_find_participant(db_session, "Del Mage, Ann").participant.id == person.id


def test_a_pcc_id_cannot_be_given_to_two_people(client, db_session):
    first, _, _ = get_or_create_participant(db_session, "Adams, Alice (PAM-1)")
    second, _, _ = get_or_create_participant(db_session, "Brown, Bob")
    db_session.commit()

    client.post(f"/participants/{second.id}/edit", data={
        "csrf_token": _csrf(client), "full_name": "Brown, Bob",
        "external_id": "PAM-1", "status": "active", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    assert db_session.get(Participant, second.id).external_id is None
    assert db_session.get(Participant, first.id).external_id == "PAM-1"


# -------------------------------------------------------------- merge ----

def test_merging_two_halves_of_one_person_clears_the_false_exceptions(client, db_session):
    """The split that the roster exists to fix: attendance landed on one
    record, billing on the other, so the day read as two exceptions."""
    db = db_session
    att_upload = _upload(db)
    pcc_upload = _upload(db, UploadKind.PCC_BATCH)
    user = db.scalar(select(User))

    attended_half, _, _ = get_or_create_participant(db, "Hall, Tresea")
    billed_half, _, _ = get_or_create_participant(db, "Hall, Tressa")
    billed_half_id = billed_half.id
    db.add(AttendanceRecord(participant_id=attended_half.id, date=DAY, attended=True,
                            source_upload_id=att_upload.id))
    db.add(BillingRecord(raw_name="Hall, Tressa", date=DAY, payer_source="Private Pay",
                         source_upload_id=pcc_upload.id, verified=True,
                         participant_id=billed_half.id))
    db.commit()

    assert run_reconciliation(db, DAY, user.id).exception_count == 2
    db.commit()

    resp = client.post(f"/participants/{billed_half.id}/merge", data={
        "csrf_token": _csrf(client, f"/participants/{billed_half.id}"),
        "target_id": attended_half.id,
    }, follow_redirects=False)
    assert resp.status_code == 303

    db.expire_all()
    assert db.get(Participant, billed_half_id) is None, "the merged-away record is gone"
    assert run_reconciliation(db, DAY, user.id).exception_count == 0


def test_merging_keeps_attendance_when_both_halves_have_the_same_day(client, db_session):
    db = db_session
    upload = _upload(db)
    keep, _, _ = get_or_create_participant(db, "Hall, Tresea")
    fold, _, _ = get_or_create_participant(db, "Hall, Tressa")
    db.add(AttendanceRecord(participant_id=keep.id, date=DAY, attended=False, source_upload_id=upload.id))
    db.add(AttendanceRecord(participant_id=fold.id, date=DAY, attended=True, source_upload_id=upload.id))
    db.commit()

    client.post(f"/participants/{fold.id}/merge", data={
        "csrf_token": _csrf(client, f"/participants/{fold.id}"), "target_id": keep.id,
    }, follow_redirects=False)

    db.expire_all()
    records = db.scalars(select(AttendanceRecord)).all()
    assert len(records) == 1
    assert records[0].attended is True, "a mark of attendance outranks a blank"


def test_merging_carries_the_pcc_id_and_billing_rule_across(client, db_session):
    db = db_session
    keep, _, _ = get_or_create_participant(db, "Hall, Tresea")
    fold, _, _ = get_or_create_participant(db, "Hall, Tressa (PAM-300)")
    db.add(RateRule(participant_id=fold.id, payer_source="Medicaid"))
    db.commit()

    client.post(f"/participants/{fold.id}/merge", data={
        "csrf_token": _csrf(client, f"/participants/{fold.id}"), "target_id": keep.id,
    }, follow_redirects=False)

    db.expire_all()
    kept = db.get(Participant, keep.id)
    assert kept.external_id == "PAM-300"
    assert [r.payer_source for r in db.scalars(select(RateRule)).all()] == ["Medicaid"]
    assert db.scalar(select(RateRule)).participant_id == keep.id


def test_a_participant_cannot_be_merged_into_themselves(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Hall, Tresea")
    db_session.commit()

    client.post(f"/participants/{person.id}/merge", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"), "target_id": person.id,
    }, follow_redirects=False)

    db_session.expire_all()
    assert db_session.get(Participant, person.id) is not None


# -------------------------------------------------------- billing rules ----

def test_billing_set_on_the_profile_drives_reconciliation(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Arocho, Leonidas (PAM-6)")
    db_session.commit()

    client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "payer_source": "Medicaid", "rate": "95.96", "grant_rule_type": "none",
        "grant_cycle_length": "", "grant_cycle_secondary_days": "1", "grant_payer": "",
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    expected = compute_expected_billing(db_session, person.id, DAY)
    assert expected.payer == "Medicaid"
    assert db_session.scalar(select(RateRule)).rate == 95.96


def test_a_rotation_set_on_the_profile_is_applied(client, db_session):
    db = db_session
    upload = _upload(db)
    person, _, _ = get_or_create_participant(db, "Biederman, Georgiana (PAM-209)")
    db.commit()

    client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "payer_source": "Private Pay", "rate": "", "grant_rule_type": "rotating_grant",
        "grant_cycle_length": "3", "grant_cycle_secondary_days": "1",
        "grant_payer": "Parker Grant", "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    db.expire_all()
    rule = db.scalar(select(RateRule))
    assert rule.grant_rule_type == GrantRuleType.ROTATING_GRANT

    # Three private pay days, then a grant day.
    for offset in range(4):
        db.add(AttendanceRecord(participant_id=person.id, date=DAY + datetime.timedelta(days=offset),
                                attended=True, source_upload_id=upload.id))
    db.commit()

    payers = [compute_expected_billing(db, person.id, DAY + datetime.timedelta(days=o)).payer
              for o in range(4)]
    assert payers == ["Private Pay", "Private Pay", "Private Pay", "Parker Grant"]


def test_an_incomplete_rotation_is_refused(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "payer_source": "Private Pay", "rate": "", "grant_rule_type": "rotating_grant",
        "grant_cycle_length": "3", "grant_cycle_secondary_days": "1",
        "grant_payer": "",  # no payer to rotate to
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    assert db_session.scalars(select(RateRule)).all() == [], "a half-specified rotation saves nothing"


def test_a_new_rule_supersedes_the_open_ended_one(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.add(RateRule(participant_id=person.id, payer_source="Private Pay"))
    db_session.commit()

    client.post(f"/participants/{person.id}/rules/add", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "payer_source": "Medicaid", "rate": "", "grant_rule_type": "none",
        "grant_cycle_length": "", "grant_cycle_secondary_days": "1", "grant_payer": "",
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    active = db_session.scalars(select(RateRule).where(RateRule.active == True)).all()  # noqa: E712
    assert [r.payer_source for r in active] == ["Medicaid"]
    assert compute_expected_billing(db_session, person.id, DAY).payer == "Medicaid"


def test_removing_the_rule_falls_back_to_private_pay(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    rule = RateRule(participant_id=person.id, payer_source="Medicaid")
    db_session.add(rule)
    db_session.commit()

    client.post(f"/participants/{person.id}/rules/{rule.id}/remove", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
    }, follow_redirects=False)

    db_session.expire_all()
    assert compute_expected_billing(db_session, person.id, DAY).payer == "Private Pay"


# ----------------------------------------------------------- schedule ----

def test_a_schedule_set_on_the_profile_drives_the_check_in_list(client, db_session):
    from app.models import AttendanceSchedule
    from app.routers.checkin import scheduled_participants

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    client.post(f"/participants/{person.id}/schedule", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "weekday": ["0", "2", "4"], "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    schedule = db_session.scalar(select(AttendanceSchedule))
    assert schedule.days_of_week == "0,2,4"
    assert schedule.describe() == "Mon, Wed, Fri"

    monday = datetime.date(2026, 9, 7)
    assert [p.id for p in scheduled_participants(db_session, monday)] == [person.id]
    assert scheduled_participants(db_session, monday + datetime.timedelta(days=1)) == []


def test_unticking_every_day_takes_someone_off_the_schedule(client, db_session):
    from app.models import AttendanceSchedule
    from app.routers.checkin import scheduled_participants

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.add(AttendanceSchedule(participant_id=person.id, days_of_week="0,1,2,3,4"))
    db_session.commit()

    client.post(f"/participants/{person.id}/schedule", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "effective_start": "", "effective_end": "", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    assert scheduled_participants(db_session, datetime.date(2026, 9, 7)) == []


def test_a_schedule_only_applies_inside_its_effective_dates(client, db_session):
    from app.routers.checkin import scheduled_participants

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    client.post(f"/participants/{person.id}/schedule", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "weekday": ["0"], "effective_start": "2026-09-07", "effective_end": "2026-09-07", "notes": "",
    }, follow_redirects=False)

    db_session.expire_all()
    assert len(scheduled_participants(db_session, datetime.date(2026, 9, 7))) == 1
    assert scheduled_participants(db_session, datetime.date(2026, 9, 14)) == []


def test_a_backwards_schedule_range_is_refused(client, db_session):
    from app.models import AttendanceSchedule

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    client.post(f"/participants/{person.id}/schedule", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
        "weekday": ["0"], "effective_start": "2026-09-30", "effective_end": "2026-09-01", "notes": "",
    }, follow_redirects=False)

    assert db_session.scalars(select(AttendanceSchedule)).all() == []
