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

OLD_ATTENDANCE_SCHEMA = """
CREATE TABLE attendance_records (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    participant_id VARCHAR(36) NOT NULL REFERENCES participants (id),
    date DATE NOT NULL,
    attended BOOLEAN NOT NULL,
    source_upload_id VARCHAR(36) NOT NULL REFERENCES uploads (id),
    CONSTRAINT uq_attendance_participant_date UNIQUE (participant_id, date)
)"""

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


# ------------------------------------------------- attendance records ----

@pytest.fixture()
def legacy_attendance_db():
    """A database whose attendance rows must each point at an upload."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE attendance_records"))
        conn.execute(text(OLD_ATTENDANCE_SCHEMA))
    yield
    Base.metadata.drop_all(bind=engine)


def _legacy_attendance_row(session, participant, upload_id):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO attendance_records (id, participant_id, date, attended, source_upload_id) "
                 "VALUES ('a1', :pid, '2026-09-10', 1, :uid)"),
            {"pid": participant.id, "uid": upload_id},
        )


def test_attendance_can_stand_without_an_upload_afterwards(legacy_attendance_db):
    """A check-in has no upload behind it, so the column has to become
    optional -- without losing the rows that do have one."""
    import datetime

    from app.models import AttendanceRecord, AttendanceSource, Role, Upload, UploadKind, User
    from app.security import hash_password

    with Session(engine) as session:
        user = User(email="m@example.org", display_name="M",
                    password_hash=hash_password("x" * 12), role=Role.ADMIN)
        participant = Participant(full_name="Adams, Alice", name_index=blind_index("adams"),
                                  last_name_index=blind_index("adams"))
        session.add_all([user, participant])
        session.flush()
        upload = Upload(kind=UploadKind.WEEKLY_ATTENDANCE, original_filename="f", stored_path="",
                        uploaded_by_id=user.id)
        session.add(upload)
        session.commit()
        participant_id, upload_id, user_id = participant.id, upload.id, user.id
        _legacy_attendance_row(session, participant, upload_id)

    run_migrations()

    with Session(engine) as session:
        carried = session.scalar(select(AttendanceRecord))
        assert carried.source_upload_id == upload_id, "the uploaded day survives the rebuild"
        assert carried.source == AttendanceSource.UPLOAD

        session.add(AttendanceRecord(
            participant_id=participant_id, date=datetime.date(2026, 9, 11), attended=True,
            source=AttendanceSource.CHECK_IN, recorded_by_id=user_id,
            recorded_at=datetime.datetime.utcnow(),
        ))
        session.commit()
        assert len(session.scalars(select(AttendanceRecord)).all()) == 2


def test_the_attendance_rebuild_runs_once(legacy_attendance_db):
    run_migrations()
    run_migrations()

    from app.models import AttendanceRecord
    with Session(engine) as session:
        assert session.scalars(select(AttendanceRecord)).all() == []
