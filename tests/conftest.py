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


@pytest.fixture()
def client(db_session):
    """A TestClient logged in as an admin, sharing the db_session's
    schema. Route-level tests need this: the upload handlers do their
    real work (CSRF, encryption at rest, audit logging) only when driven
    through the app, not by calling the function directly."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Role, User
    from app.security import hash_password

    user = User(
        email="director@example.org", display_name="Director",
        password_hash=hash_password("correct horse battery"), role=Role.ADMIN,
        # An established account: a user still on their temporary password
        # is held at the change-password screen (see test_password_change).
        must_change_password=False,
    )
    db_session.add(user)
    db_session.commit()

    test_client = TestClient(app)
    # The login form itself carries no CSRF token (there's no session to
    # tie one to yet); every form behind the login does.
    resp = test_client.post(
        "/login",
        data={"email": "director@example.org", "password": "correct horse battery"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return test_client
