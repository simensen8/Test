"""Core reconciliation engine.

For a single billing date, compares:
  - AttendanceRecord (from Weekly Attendance Excel, itself transcribed
    from the paper sign-in sheet)
  - BillingRecord (from the daily PCC ancillary batch PDF, human-verified
    in the review screen)
  - RateRule (from the Rate Master Excel; absence of a rule means
    Private Pay with no special arrangement)

and produces Exception_ rows for anything requiring human review. A date
with zero exceptions is "Reconciled / No Exceptions".
"""
import datetime
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching import fuzzy_find_participant
from app.models import (
    AttendanceRecord,
    BillingRecord,
    Exception_,
    ExceptionReason,
    GrantRuleType,
    Participant,
    RateRule,
    ReconciliationRun,
)

DEFAULT_PAYER = "Private Pay"


@dataclass
class ExpectedBilling:
    payer: str
    is_grant_day: bool
    rule: RateRule | None


def _active_rate_rule(db: Session, participant_id: str, on_date: datetime.date) -> RateRule | None:
    rules = db.scalars(
        select(RateRule).where(RateRule.participant_id == participant_id, RateRule.active == True)  # noqa: E712
    ).all()
    for rule in rules:
        if rule.effective_start and on_date < rule.effective_start:
            continue
        if rule.effective_end and on_date > rule.effective_end:
            continue
        return rule
    return None


def _attended_ordinal(db: Session, participant_id: str, on_date: datetime.date) -> int:
    """1-based count of attended days for this participant up to and
    including on_date, across all attendance records loaded so far."""
    dates = db.scalars(
        select(AttendanceRecord.date).where(
            AttendanceRecord.participant_id == participant_id,
            AttendanceRecord.attended == True,  # noqa: E712
            AttendanceRecord.date <= on_date,
        )
    ).all()
    return len(dates)


def compute_expected_billing(db: Session, participant_id: str, on_date: datetime.date) -> ExpectedBilling:
    rule = _active_rate_rule(db, participant_id, on_date)
    if rule is None:
        return ExpectedBilling(payer=DEFAULT_PAYER, is_grant_day=False, rule=None)

    if rule.grant_rule_type == GrantRuleType.ROTATING_GRANT and rule.grant_cycle_length:
        cycle = rule.grant_cycle_length + 1
        ordinal = _attended_ordinal(db, participant_id, on_date)
        position = ((ordinal - 1) % cycle) + 1
        if position == cycle:
            return ExpectedBilling(payer=rule.grant_payer or "Grant", is_grant_day=True, rule=rule)

    return ExpectedBilling(payer=rule.payer_source, is_grant_day=False, rule=rule)


def _payers_match(expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    return " ".join(expected.strip().lower().split()) == " ".join(actual.strip().lower().split())


def run_reconciliation(db: Session, on_date: datetime.date, run_by_id: str) -> ReconciliationRun:
    run = ReconciliationRun(date=on_date, run_by_id=run_by_id)
    db.add(run)
    db.flush()

    billing_rows = db.scalars(select(BillingRecord).where(BillingRecord.date == on_date)).all()

    # Auto-match any billing rows not yet linked to a participant.
    for row in billing_rows:
        if row.participant_id is None:
            match = fuzzy_find_participant(db, row.raw_name)
            if match.participant is not None:
                row.participant_id = match.participant.id

    billing_by_participant: dict[str, list[BillingRecord]] = {}
    unmatched_billing: list[BillingRecord] = []
    for row in billing_rows:
        if row.participant_id:
            billing_by_participant.setdefault(row.participant_id, []).append(row)
        else:
            unmatched_billing.append(row)

    attendance_rows = db.scalars(
        select(AttendanceRecord).where(AttendanceRecord.date == on_date)
    ).all()
    attendance_by_participant = {r.participant_id: r for r in attendance_rows}

    exceptions: list[Exception_] = []

    def add_exception(participant: Participant | None, name_snapshot: str, reason: ExceptionReason,
                       expected: str | None, actual: str | None, detail: str | None,
                       billing_record_id: str | None = None):
        exceptions.append(Exception_(
            run_id=run.id,
            date=on_date,
            participant_id=participant.id if participant else None,
            participant_name_snapshot=name_snapshot,
            reason=reason,
            expected_billing=expected,
            actual_billing=actual,
            detail=detail,
            billing_record_id=billing_record_id,
        ))

    # 1) Everyone marked attended: check they were billed, correctly.
    for participant_id, attendance in attendance_by_participant.items():
        if not attendance.attended:
            continue
        participant = db.get(Participant, participant_id)
        expected = compute_expected_billing(db, participant_id, on_date)
        rows = billing_by_participant.get(participant_id, [])

        if len(rows) == 0:
            add_exception(
                participant, participant.full_name, ExceptionReason.MISSING_FROM_BATCH,
                expected=expected.payer, actual="Not billed",
                detail="Participant attended per Weekly Attendance but does not appear on the PCC batch.",
            )
        elif len(rows) > 1:
            actual_desc = "; ".join(f"{r.payer_source or 'unknown payer'} (${r.amount or 0:.2f})" for r in rows)
            add_exception(
                participant, participant.full_name, ExceptionReason.DUPLICATE_ENTRY,
                expected=expected.payer, actual=actual_desc,
                detail=f"{len(rows)} billing entries found for this participant on this date.",
                billing_record_id=rows[0].id,
            )
        else:
            row = rows[0]
            if not _payers_match(expected.payer, row.payer_source):
                reason = ExceptionReason.GRANT_RULE_NOT_APPLIED if expected.is_grant_day or (
                    expected.rule and expected.rule.grant_rule_type == GrantRuleType.ROTATING_GRANT
                ) else ExceptionReason.WRONG_PAYER
                detail = (
                    f"Expected payer '{expected.payer}'"
                    + (" (grant rotation day)" if expected.is_grant_day else "")
                    + f", PCC batch shows '{row.payer_source or 'blank'}'."
                )
                add_exception(
                    participant, participant.full_name, reason,
                    expected=expected.payer, actual=row.payer_source or "(blank)",
                    detail=detail, billing_record_id=row.id,
                )

    # 2) Billed rows for participants attendance says did NOT attend, or
    #    for whom we have no attendance record at all for this date.
    for participant_id, rows in billing_by_participant.items():
        attendance = attendance_by_participant.get(participant_id)
        participant = db.get(Participant, participant_id)
        if attendance is not None and attendance.attended:
            continue  # already handled above
        for row in rows:
            if attendance is None:
                add_exception(
                    participant, participant.full_name, ExceptionReason.UNMATCHED_NAME,
                    expected="No attendance record for this date", actual=row.payer_source or "(blank)",
                    detail=(
                        "Participant appears on the PCC batch but has no Weekly Attendance record "
                        "for this date (attendance data missing or participant not listed that week)."
                    ),
                    billing_record_id=row.id,
                )
            else:
                add_exception(
                    participant, participant.full_name, ExceptionReason.BILLED_BUT_ABSENT,
                    expected="Not billed (marked absent)", actual=row.payer_source or "(blank)",
                    detail="Weekly Attendance shows this participant as absent, but they appear on the PCC batch.",
                    billing_record_id=row.id,
                )

    # 3) Billing rows that could not be matched to any participant at all.
    for row in unmatched_billing:
        add_exception(
            None, row.raw_name, ExceptionReason.UNMATCHED_NAME,
            expected=None, actual=row.payer_source or "(blank)",
            detail="This name on the PCC batch could not be matched to any participant in Weekly Attendance or the Rate Master.",
            billing_record_id=row.id,
        )

    for exc in exceptions:
        db.add(exc)
    run.exception_count = len(exceptions)
    db.commit()
    return run
