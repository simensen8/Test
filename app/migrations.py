"""Schema touch-ups applied at startup.

`Base.metadata.create_all` creates tables that don't exist yet but never
alters one that does, so a column added to a model after a site has been
running is invisible to it -- and this app's data (encrypted PHI it is
the only copy of, in practice) can't just be dropped and recreated.

These are deliberately small and idempotent: add a missing column, fill
it in from data already present. Anything more involved than that wants
a real migration tool.
"""
import logging

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.crypto_types import blind_index
from app.db import engine

logger = logging.getLogger(__name__)


def _column_names(table: str) -> set[str]:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def add_participant_last_name_index() -> None:
    """Add participants.last_name_index and backfill it.

    The value is derived from the (encrypted) full name, so the backfill
    has to run through the ORM rather than in SQL.
    """
    columns = _column_names("participants")
    if not columns or "last_name_index" in columns:
        return  # no such table yet (create_all builds it complete), or already added

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE participants ADD COLUMN last_name_index VARCHAR(64)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_participants_last_name_index "
                          "ON participants (last_name_index)"))
    logger.info("Added participants.last_name_index")


def add_participant_profile_columns() -> None:
    """Add the profile fields (status, notes) to an existing roster."""
    columns = _column_names("participants")
    if not columns:
        return

    with engine.begin() as conn:
        if "status" not in columns:
            # Everyone already on the roster is by definition someone the
            # program is serving, so they start active.
            conn.execute(text("ALTER TABLE participants ADD COLUMN status VARCHAR(20) "
                              "NOT NULL DEFAULT 'ACTIVE'"))
            logger.info("Added participants.status")
        if "notes" not in columns:
            conn.execute(text("ALTER TABLE participants ADD COLUMN notes VARCHAR(2000)"))
            logger.info("Added participants.notes")


def backfill_participant_last_name_index() -> None:
    from app.matching import extract_last_name
    from app.models import Participant

    if "last_name_index" not in _column_names("participants"):
        return

    with Session(engine) as session:
        pending = session.scalars(
            select(Participant).where(Participant.last_name_index.is_(None))
        ).all()
        if not pending:
            return
        for participant in pending:
            participant.last_name_index = blind_index(extract_last_name(participant.full_name))
        session.commit()
        logger.info("Backfilled last_name_index for %d participant(s)", len(pending))


ATTENDANCE_REBUILT = """
CREATE TABLE attendance_records_new (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    participant_id VARCHAR(36) NOT NULL REFERENCES participants (id),
    date DATE NOT NULL,
    attended BOOLEAN NOT NULL,
    source VARCHAR(8) NOT NULL DEFAULT 'UPLOAD',
    source_upload_id VARCHAR(36) REFERENCES uploads (id),
    recorded_by_id VARCHAR(36) REFERENCES users (id),
    recorded_at DATETIME,
    CONSTRAINT uq_attendance_participant_date UNIQUE (participant_id, date)
)"""


def allow_attendance_without_an_upload() -> None:
    """Let an attendance record stand on its own.

    Attendance used to arrive only inside an uploaded workbook, so the
    row required an upload to point at. A day marked on the check-in
    screen has no upload behind it, which means the column has to become
    optional -- and SQLite can't relax NOT NULL in place, so the table is
    rebuilt and copied across.
    """
    inspector = inspect(engine)
    if "attendance_records" not in inspector.get_table_names():
        return

    columns = {col["name"]: col for col in inspector.get_columns("attendance_records")}
    needs_new_columns = "source" not in columns
    needs_rebuild = not columns["source_upload_id"]["nullable"]

    if not needs_new_columns and not needs_rebuild:
        return

    if needs_rebuild:
        # Copy through a new table: every existing row came from an
        # upload, which is what the default records.
        carried = "id, participant_id, date, attended, source_upload_id"
        statements = [
            "DROP TABLE IF EXISTS attendance_records_new",
            ATTENDANCE_REBUILT,
            f"INSERT INTO attendance_records_new ({carried}) SELECT {carried} FROM attendance_records",
            "DROP TABLE attendance_records",
            "ALTER TABLE attendance_records_new RENAME TO attendance_records",
            "CREATE INDEX IF NOT EXISTS ix_attendance_records_date ON attendance_records (date)",
        ]
        # Driven through the raw connection: foreign key enforcement is a
        # connection-level pragma that a transaction ignores, and it has
        # to be off while the table the rows point at is swapped.
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            for statement in statements:
                cursor.execute(statement)
            raw.commit()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        finally:
            raw.close()
        logger.info("Rebuilt attendance_records so a check-in needs no upload")
        return

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE attendance_records ADD COLUMN source VARCHAR(8) "
                          "NOT NULL DEFAULT 'UPLOAD'"))
        conn.execute(text("ALTER TABLE attendance_records ADD COLUMN recorded_by_id VARCHAR(36)"))
        conn.execute(text("ALTER TABLE attendance_records ADD COLUMN recorded_at DATETIME"))
    logger.info("Added attendance provenance columns")


def run_migrations() -> None:
    add_participant_last_name_index()
    add_participant_profile_columns()
    allow_attendance_without_an_upload()
    backfill_participant_last_name_index()
