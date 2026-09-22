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


def run_migrations() -> None:
    add_participant_last_name_index()
    backfill_participant_last_name_index()
