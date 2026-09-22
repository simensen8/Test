from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

if DATABASE_URL.startswith("sqlite"):
    # SQLite ignores foreign keys unless asked not to, so a delete that
    # orphans a reference (an exception row pointing at a billing row
    # that's been replaced, say) fails silently here while raising on
    # Postgres. Enforcing it keeps dev honest and matches production.
    @event.listens_for(engine, "connect")
    def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
# autoflush=True (the default) matters here: several upload handlers do
# a SELECT-then-INSERT-if-missing loop (e.g. "does an AttendanceRecord
# already exist for this participant/date?") over many rows in one
# request. With autoflush off, an INSERT from an earlier row in the same
# loop wouldn't be visible to a later row's SELECT, so two rows that
# resolve to the same participant (via a last-name collision) could
# both try to insert the same (participant_id, date) primary data and
# crash on the UNIQUE constraint instead of being caught by the check.
SessionLocal = sessionmaker(bind=engine, autoflush=True, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
