"""The startup migrations, run against a database that predates them.

`create_all` never alters a table that already exists, so a column added
to a model after a site has been running would simply be missing there --
and this database holds encrypted PHI that can't be dropped and rebuilt.
"""
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.crypto_types import EncryptedString, blind_index
from app.db import Base, engine
from app.migrations import run_migrations
from app.models import Participant

OLD_SCHEMA = """
CREATE TABLE participants (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    full_name VARCHAR(512) NOT NULL,
    name_index VARCHAR(64) NOT NULL UNIQUE,
    external_id VARCHAR(64),
    created_at DATETIME
)"""


@pytest.fixture()
def legacy_db():
    """A database whose participants table has no last_name_index."""
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text(OLD_SCHEMA))
    Base.metadata.create_all(bind=engine)  # every other table, as at startup
    yield
    Base.metadata.drop_all(bind=engine)


def _insert_legacy_participant(name: str, last_name: str) -> None:
    encrypted = EncryptedString(512).process_bind_param(name, engine.dialect)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO participants (id, full_name, name_index, external_id) "
                 "VALUES (:id, :name, :idx, NULL)"),
            {"id": name, "name": encrypted, "idx": blind_index(last_name)},
        )


def test_the_column_is_added_and_backfilled(legacy_db):
    _insert_legacy_participant("Hall, Tresea", "hall")
    assert "last_name_index" not in {c["name"] for c in inspect(engine).get_columns("participants")}

    run_migrations()

    assert "last_name_index" in {c["name"] for c in inspect(engine).get_columns("participants")}
    with Session(engine) as session:
        participant = session.scalar(select(Participant))
        assert participant.full_name == "Hall, Tresea", "the encrypted name survives untouched"
        assert participant.last_name_index == blind_index("hall")


def test_existing_participants_become_findable_by_surname(legacy_db):
    """The point of the backfill: a batch row for someone enrolled before
    the upgrade still resolves to them."""
    from app.matching import fuzzy_find_participant

    _insert_legacy_participant("Hall, Tresea", "hall")
    run_migrations()

    with Session(engine) as session:
        assert fuzzy_find_participant(session, "Hall, Tresea").participant.full_name == "Hall, Tresea"


def test_running_them_again_changes_nothing(legacy_db):
    _insert_legacy_participant("Hall, Tresea", "hall")
    run_migrations()
    run_migrations()  # every startup runs these

    with Session(engine) as session:
        assert len(session.scalars(select(Participant)).all()) == 1
