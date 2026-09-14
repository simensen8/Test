import os
import tempfile

# Must run before any `app.*` module is imported (including by test module
# collection), since app.config reads these at import time to compute
# DATA_DIR/DATABASE_URL/encryption keys.
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="adp_test_")
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402


@pytest.fixture()
def db_session():
    from app.db import Base, SessionLocal, engine
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
