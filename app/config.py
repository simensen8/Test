"""Application configuration, loaded from environment variables.

No secrets are hard-coded. In production, APP_ENCRYPTION_KEY and
SESSION_SECRET_KEY must be set to strong, persistent random values
(a lost APP_ENCRYPTION_KEY makes existing encrypted PHI unrecoverable).
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'reconcile.db'}")

# Symmetric key used to encrypt PHI fields at rest (names, notes) and
# uploaded source documents. MUST be a stable, secret, 32-byte urlsafe
# base64 key (Fernet.generate_key()). If unset, a key is generated and
# persisted to data/.encryption_key for local/dev use only -- production
# deployments must supply APP_ENCRYPTION_KEY via a secrets manager.
_KEY_FILE = DATA_DIR / ".encryption_key"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("APP_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    _KEY_FILE.chmod(0o600)
    return key


ENCRYPTION_KEY = _load_or_create_key()
FERNET = Fernet(ENCRYPTION_KEY)

SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY")
_SESSION_KEY_FILE = DATA_DIR / ".session_secret"
if not SESSION_SECRET_KEY:
    if _SESSION_KEY_FILE.exists():
        SESSION_SECRET_KEY = _SESSION_KEY_FILE.read_text().strip()
    else:
        SESSION_SECRET_KEY = Fernet.generate_key().decode()
        _SESSION_KEY_FILE.write_text(SESSION_SECRET_KEY)
        _SESSION_KEY_FILE.chmod(0o600)

# Idle session timeout, minutes. Meets a common HIPAA-workforce-security
# expectation of automatic logoff for sessions accessing PHI.
SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", "20"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.environ.get("SESSION_ABSOLUTE_TIMEOUT_HOURS", "10"))

MAX_LOGIN_ATTEMPTS = int(os.environ.get("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES = int(os.environ.get("LOCKOUT_MINUTES", "15"))

# Cookie is marked Secure by default; disable only for local http-only dev.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

ORGANIZATION_NAME = os.environ.get("ORGANIZATION_NAME", "Adult Day Program")
