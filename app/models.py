import datetime
import enum
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.crypto_types import EncryptedString
from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


class Role(str, enum.Enum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.REVIEWER)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class Participant(Base):
    """Canonical participant identity, matched across uploads via a blind
    index on the (encrypted) normalized name. In this program, roster
    matching is done by name as PCC batches, attendance sheets, and the
    rate master all key off participant last/first name."""

    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(EncryptedString(512))
    name_index: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    rate_rules: Mapped[list["RateRule"]] = relationship(back_populates="participant")
    attendance_records: Mapped[list["AttendanceRecord"]] = relationship(back_populates="participant")
    billing_records: Mapped[list["BillingRecord"]] = relationship(back_populates="participant")


class GrantRuleType(str, enum.Enum):
    NONE = "none"
    # After N days billed to the primary payer within a cycle, the next
    # attended day bills to the grant payer instead (e.g. 3-for-1 Parker
    # Grant arrangement), then the cycle repeats.
    ROTATING_GRANT = "rotating_grant"


class RateRule(Base):
    """One row of the consolidated Rate Master: a participant's payer
    source, rate, and any special billing exception. Participants not
    present here default to Private Pay with no special rule."""

    __tablename__ = "rate_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_id: Mapped[str] = mapped_column(String(36), ForeignKey("participants.id"))
    payer_source: Mapped[str] = mapped_column(EncryptedString(255))
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    grant_rule_type: Mapped[GrantRuleType] = mapped_column(Enum(GrantRuleType), default=GrantRuleType.NONE)
    # For ROTATING_GRANT: after this many attended days billed to
    # payer_source, the next attended day bills to grant_payer.
    grant_cycle_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grant_payer: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    # Day-of-week specific overrides are captured as free text notes for
    # human review rather than auto-applied, since payer source may vary
    # by participant AND by day in ways the source data doesn't fully
    # formalize.
    notes: Mapped[str | None] = mapped_column(EncryptedString(2000), nullable=True)
    effective_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    effective_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    source_upload_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("uploads.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    participant: Mapped[Participant] = relationship(back_populates="rate_rules")


class UploadKind(str, enum.Enum):
    WEEKLY_ATTENDANCE = "weekly_attendance"
    PCC_BATCH = "pcc_batch"
    RATE_MASTER = "rate_master"


class Upload(Base):
    """Record of a source document uploaded for reconciliation. The raw
    file is stored encrypted on disk (see storage.py); this row tracks
    provenance for audit purposes."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[UploadKind] = mapped_column(Enum(UploadKind))
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    uploaded_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    uploaded_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    # For weekly attendance: the Monday of the week covered.
    week_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    # For PCC batch: the single billing day covered.
    batch_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)


class AttendanceRecord(Base):
    """One participant's attendance status for one day, sourced from the
    Weekly Attendance Excel (which is itself a transcription of the paper
    sign-in sheet)."""

    __tablename__ = "attendance_records"
    __table_args__ = (UniqueConstraint("participant_id", "date", name="uq_attendance_participant_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_id: Mapped[str] = mapped_column(String(36), ForeignKey("participants.id"))
    date: Mapped[datetime.date] = mapped_column(Date, index=True)
    attended: Mapped[bool] = mapped_column(Boolean)
    source_upload_id: Mapped[str] = mapped_column(String(36), ForeignKey("uploads.id"))

    participant: Mapped[Participant] = relationship(back_populates="attendance_records")


class BillingRecord(Base):
    """One line item parsed (and possibly human-corrected) from a daily
    PCC ancillary batch PDF."""

    __tablename__ = "billing_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("participants.id"), nullable=True)
    # Raw name as it appeared on the batch, kept even when matched, so a
    # reviewer can see exactly what PCC printed (encrypted, PHI).
    raw_name: Mapped[str] = mapped_column(EncryptedString(512))
    date: Mapped[datetime.date] = mapped_column(Date, index=True)
    payer_source: Mapped[str | None] = mapped_column(EncryptedString(255), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_line: Mapped[str | None] = mapped_column(EncryptedString(2000), nullable=True)
    source_upload_id: Mapped[str] = mapped_column(String(36), ForeignKey("uploads.id"))
    # True once a human has confirmed/corrected this row in the review UI.
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)

    participant: Mapped[Participant | None] = relationship(back_populates="billing_records")


class ExceptionReason(str, enum.Enum):
    MISSING_FROM_BATCH = "missing_from_batch"           # attended, not billed
    BILLED_BUT_ABSENT = "billed_but_absent"              # billed, did not attend
    WRONG_PAYER = "wrong_payer"                          # payer source mismatch
    GRANT_RULE_NOT_APPLIED = "grant_rule_not_applied"    # rotation rule mismatch
    UNMATCHED_NAME = "unmatched_name"                    # can't match across sources
    DUPLICATE_ENTRY = "duplicate_entry"                  # duplicate billing line


class ExceptionStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    NOT_AN_ERROR = "not_an_error"  # reviewer confirms billing was actually correct


class ReconciliationRun(Base):
    """One reconciliation pass over a single billing date."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    date: Mapped[datetime.date] = mapped_column(Date, index=True)
    run_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now)
    run_by_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    exception_count: Mapped[int] = mapped_column(Integer, default=0)

    exceptions: Mapped[list["Exception_"]] = relationship(back_populates="run")


class Exception_(Base):
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("reconciliation_runs.id"))
    date: Mapped[datetime.date] = mapped_column(Date, index=True)
    participant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("participants.id"), nullable=True)
    participant_name_snapshot: Mapped[str] = mapped_column(EncryptedString(512))
    reason: Mapped[ExceptionReason] = mapped_column(Enum(ExceptionReason))
    expected_billing: Mapped[str | None] = mapped_column(EncryptedString(500), nullable=True)
    actual_billing: Mapped[str | None] = mapped_column(EncryptedString(500), nullable=True)
    detail: Mapped[str | None] = mapped_column(EncryptedString(2000), nullable=True)
    status: Mapped[ExceptionStatus] = mapped_column(Enum(ExceptionStatus), default=ExceptionStatus.OPEN)
    resolved_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(EncryptedString(2000), nullable=True)
    billing_record_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("billing_records.id"), nullable=True)

    run: Mapped[ReconciliationRun] = relationship(back_populates="exceptions")


class AuditLog(Base):
    """Append-only record of actions touching PHI or system security state,
    satisfying the HIPAA Security Rule's audit-controls expectation
    (45 CFR 164.312(b))."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_now, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    user_email_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
