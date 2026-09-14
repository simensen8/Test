"""SQLAlchemy column types that transparently encrypt PHI at rest.

Values are encrypted with the application Fernet key before being written
to the database and decrypted on read. This protects participant names and
free-text notes even if the raw database file (e.g. a SQLite file, or a
backup/snapshot of a managed Postgres instance) is exposed outside of the
application's own access controls. It is defense-in-depth, not a
substitute for encrypting the underlying disk/volume and transport (TLS).
"""
import hashlib
import hmac

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import ENCRYPTION_KEY, FERNET


def blind_index(value: str) -> str:
    """Deterministic, non-reversible index for exact-match lookups on an
    encrypted field (e.g. matching a participant name across uploads
    without storing/searching plaintext). Uses HMAC-SHA256 keyed with the
    app encryption key so it cannot be recomputed without that secret."""
    normalized = " ".join(value.strip().lower().split())
    return hmac.new(ENCRYPTION_KEY, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


class EncryptedString(TypeDecorator):
    """A string column encrypted at rest with Fernet (AES-128-CBC + HMAC)."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return FERNET.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return FERNET.decrypt(value.encode("ascii")).decode("utf-8")
