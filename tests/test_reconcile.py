import datetime

from app.matching import get_or_create_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    ExceptionReason,
    GrantRuleType,
    RateRule,
    Upload,
    UploadKind,
    User,
    Role,
)
from app.reconcile import compute_expected_billing, run_reconciliation
from app.security import hash_password

MON = datetime.date(2026, 9, 14)
TUE = MON + datetime.timedelta(days=1)
WED = MON + datetime.timedelta(days=2)
THU = MON + datetime.timedelta(days=3)
FRI = MON + datetime.timedelta(days=4)


def _user(db):
    u = User(email="tester@example.org", display_name="Tester", password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(u)
    db.flush()
    return u


def _upload(db, kind, **kw):
    u = _user(db) if not kw.get("_user") else kw.pop("_user")
    up = Upload(kind=kind, original_filename="f", stored_path="", uploaded_by_id=u.id, **{k: v for k, v in kw.items() if k != "_user"})
    db.add(up)
    db.flush()
    return up


def _attend(db, participant, day, attended, upload):
    db.add(AttendanceRecord(participant_id=participant.id, date=day, attended=attended, source_upload_id=upload.id))


def test_missing_from_batch(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Davis")
    _attend(db, participant, MON, True, att_upload)
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 1
    assert run.exceptions[0].reason == ExceptionReason.MISSING_FROM_BATCH


def test_billed_but_absent(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Evans")
    _attend(db, participant, MON, False, att_upload)

    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=MON, _user=user)
    db.add(BillingRecord(raw_name="Evans, Eve", date=MON, payer_source="Private Pay",
                          source_upload_id=pcc_upload.id, verified=True, participant_id=participant.id))
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 1
    assert run.exceptions[0].reason == ExceptionReason.BILLED_BUT_ABSENT


def test_wrong_payer(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Garcia")
    _attend(db, participant, MON, True, att_upload)
    db.add(RateRule(participant_id=participant.id, payer_source="Medicaid"))

    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=MON, _user=user)
    db.add(BillingRecord(raw_name="Garcia, Gina", date=MON, payer_source="Private Pay",
                          source_upload_id=pcc_upload.id, verified=True, participant_id=participant.id))
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 1
    exc = run.exceptions[0]
    assert exc.reason == ExceptionReason.WRONG_PAYER
    assert exc.expected_billing == "Medicaid"
    assert exc.actual_billing == "Private Pay"


def test_duplicate_entry(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Frost")
    _attend(db, participant, MON, True, att_upload)

    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=MON, _user=user)
    for _ in range(2):
        db.add(BillingRecord(raw_name="Frost, Fiona", date=MON, payer_source="Private Pay",
                              source_upload_id=pcc_upload.id, verified=True, participant_id=participant.id))
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 1
    assert run.exceptions[0].reason == ExceptionReason.DUPLICATE_ENTRY


def test_unmatched_name_no_participant_anywhere(db_session):
    db = db_session
    user = _user(db)
    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=MON, _user=user)
    db.add(BillingRecord(raw_name="Harris, Henry", date=MON, payer_source="Private Pay",
                          source_upload_id=pcc_upload.id, verified=True))
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 1
    assert run.exceptions[0].reason == ExceptionReason.UNMATCHED_NAME
    assert run.exceptions[0].participant_id is None


def test_clean_day_no_exceptions(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Adams")
    _attend(db, participant, MON, True, att_upload)

    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=MON, _user=user)
    db.add(BillingRecord(raw_name="Adams, Alice", date=MON, payer_source="Private Pay",
                          source_upload_id=pcc_upload.id, verified=True, participant_id=participant.id))
    db.commit()

    run = run_reconciliation(db, MON, user.id)
    assert run.exception_count == 0


def test_default_private_pay_when_no_rate_rule(db_session):
    db = db_session
    _user(db)
    participant, _, _ = get_or_create_participant(db, "Nobody")
    db.commit()
    expected = compute_expected_billing(db, participant.id, MON)
    assert expected.payer == "Private Pay"
    assert expected.is_grant_day is False


def test_rotating_grant_cycle(db_session):
    """3 paid attendance days -> 4th attended day bills to the grant,
    then the cycle repeats. Verifies the ordinal count is based on
    ATTENDED days (not calendar days) and repeats every 4th day."""
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Carter")
    db.add(RateRule(
        participant_id=participant.id, payer_source="Private Pay",
        grant_rule_type=GrantRuleType.ROTATING_GRANT, grant_cycle_length=3, grant_payer="Parker Grant",
    ))

    days = [MON, TUE, WED, THU, FRI]
    for day in days:
        _attend(db, participant, day, True, att_upload)
    db.commit()

    expected_payers = []
    for day in days:
        expected_payers.append(compute_expected_billing(db, participant.id, day).payer)

    assert expected_payers == [
        "Private Pay", "Private Pay", "Private Pay", "Parker Grant", "Private Pay",
    ]


def test_rotating_grant_skips_absences(db_session):
    """The rotation counts ATTENDED days only -- an absence should not
    advance or reset the cycle."""
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Carter")
    db.add(RateRule(
        participant_id=participant.id, payer_source="Private Pay",
        grant_rule_type=GrantRuleType.ROTATING_GRANT, grant_cycle_length=3, grant_payer="Parker Grant",
    ))

    _attend(db, participant, MON, True, att_upload)   # 1st attended
    _attend(db, participant, TUE, False, att_upload)  # absent, doesn't count
    _attend(db, participant, WED, True, att_upload)   # 2nd attended
    _attend(db, participant, THU, True, att_upload)   # 3rd attended
    _attend(db, participant, FRI, True, att_upload)   # 4th attended -> grant
    db.commit()

    assert compute_expected_billing(db, participant.id, FRI).payer == "Parker Grant"
    assert compute_expected_billing(db, participant.id, THU).payer == "Private Pay"


def test_rotating_payer_multi_secondary_days(db_session):
    """'Three days private, then two days Title III' -- a generalized
    rotation with more than one secondary day, and a secondary payer
    that isn't literally a grant."""
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Cavalieri")
    db.add(RateRule(
        participant_id=participant.id, payer_source="Private Pay",
        grant_rule_type=GrantRuleType.ROTATING_GRANT, grant_cycle_length=3,
        grant_cycle_secondary_days=2, grant_payer="Title III",
    ))
    days = [MON, TUE, WED, THU, FRI]
    for day in days:
        _attend(db, participant, day, True, att_upload)
    db.commit()

    expected_payers = [compute_expected_billing(db, participant.id, d).payer for d in days]
    assert expected_payers == [
        "Private Pay", "Private Pay", "Private Pay", "Title III", "Title III",
    ]


def test_grant_rule_not_applied_exception(db_session):
    db = db_session
    user = _user(db)
    att_upload = _upload(db, UploadKind.WEEKLY_ATTENDANCE, week_start=MON, _user=user)
    participant, _, _ = get_or_create_participant(db, "Carter")
    db.add(RateRule(
        participant_id=participant.id, payer_source="Private Pay",
        grant_rule_type=GrantRuleType.ROTATING_GRANT, grant_cycle_length=3, grant_payer="Parker Grant",
    ))
    days = [MON, TUE, WED, THU]
    for day in days:
        _attend(db, participant, day, True, att_upload)

    pcc_upload = _upload(db, UploadKind.PCC_BATCH, batch_date=THU, _user=user)
    # PCC batch incorrectly billed the 4th day to Private Pay instead of the grant.
    db.add(BillingRecord(raw_name="Carter, Cathy", date=THU, payer_source="Private Pay",
                          source_upload_id=pcc_upload.id, verified=True, participant_id=participant.id))
    db.commit()

    run = run_reconciliation(db, THU, user.id)
    assert run.exception_count == 1
    assert run.exceptions[0].reason == ExceptionReason.GRANT_RULE_NOT_APPLIED
    assert run.exceptions[0].expected_billing == "Parker Grant"


def test_re_running_keeps_resolutions_and_replaces_the_run(db_session):
    """Re-running is routine -- after a rename, a merge, or a corrected
    batch. It used to reopen every finding on the day and discard the
    reviewer's reasoning."""
    import datetime as dt

    from sqlalchemy import select as sa_select

    from app.matching import get_or_create_participant
    from app.models import (
        AttendanceRecord,
        BillingRecord,
        Exception_,
        ExceptionStatus,
        ReconciliationRun,
        Role,
        Upload,
        UploadKind,
        User,
    )
    from app.reconcile import run_reconciliation
    from app.security import hash_password

    db = db_session
    day = dt.date(2026, 9, 10)
    user = User(email="r@example.org", display_name="R",
                password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(user)
    db.flush()
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=day)
    db.add(upload)
    db.flush()

    person, _, _ = get_or_create_participant(db, "Adams, Alice")
    db.add(AttendanceRecord(participant_id=person.id, date=day, attended=False,
                            source_upload_id=upload.id))
    db.add(BillingRecord(raw_name="Adams, Alice", date=day, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    run_reconciliation(db, day, user.id)
    settled = db.scalar(sa_select(Exception_))
    settled.status = ExceptionStatus.NOT_AN_ERROR
    settled.resolution_notes = "Checked with finance -- the sheet was wrong, billing was right."
    settled.resolved_by_id = user.id
    db.commit()

    run_reconciliation(db, day, user.id)
    db.expire_all()

    runs = db.scalars(sa_select(ReconciliationRun)).all()
    assert len(runs) == 1, "the superseded run doesn't accumulate"

    exceptions = db.scalars(sa_select(Exception_)).all()
    assert len(exceptions) == 1
    assert exceptions[0].status == ExceptionStatus.NOT_AN_ERROR
    assert "Checked with finance" in exceptions[0].resolution_notes


def test_a_finding_that_is_genuinely_new_comes_back_open(db_session):
    import datetime as dt

    from sqlalchemy import select as sa_select

    from app.matching import get_or_create_participant
    from app.models import (
        AttendanceRecord,
        BillingRecord,
        Exception_,
        ExceptionStatus,
        Role,
        Upload,
        UploadKind,
        User,
    )
    from app.reconcile import run_reconciliation
    from app.security import hash_password

    db = db_session
    day = dt.date(2026, 9, 10)
    user = User(email="r@example.org", display_name="R",
                password_hash=hash_password("x" * 12), role=Role.ADMIN)
    db.add(user)
    db.flush()
    upload = Upload(kind=UploadKind.PCC_BATCH, original_filename="b", stored_path="",
                    uploaded_by_id=user.id, batch_date=day)
    db.add(upload)
    db.flush()

    alice, _, _ = get_or_create_participant(db, "Adams, Alice")
    db.add(AttendanceRecord(participant_id=alice.id, date=day, attended=False,
                            source_upload_id=upload.id))
    db.add(BillingRecord(raw_name="Adams, Alice", date=day, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()
    run_reconciliation(db, day, user.id)
    for exc in db.scalars(sa_select(Exception_)).all():
        exc.status = ExceptionStatus.NOT_AN_ERROR
    db.commit()

    # A second participant develops the same kind of problem.
    bob, _, _ = get_or_create_participant(db, "Brown, Bob")
    db.add(AttendanceRecord(participant_id=bob.id, date=day, attended=False,
                            source_upload_id=upload.id))
    db.add(BillingRecord(raw_name="Brown, Bob", date=day, payer_source="Private Pay",
                         source_upload_id=upload.id, verified=True))
    db.commit()

    run_reconciliation(db, day, user.id)
    db.expire_all()

    by_name = {e.participant_name_snapshot: e.status for e in db.scalars(sa_select(Exception_)).all()}
    assert by_name["Adams, Alice"] == ExceptionStatus.NOT_AN_ERROR
    assert by_name["Brown, Bob"] == ExceptionStatus.OPEN, "a new finding is not pre-resolved"
