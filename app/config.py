"""Application configuration, loaded from environment variables.

No secrets are hard-coded. In production, APP_ENCRYPTION_KEY and
SESSION_SECRET_KEY must be set to strong, persistent random values
(a lost APP_ENCRYPTION_KEY makes existing encrypted PHI unrecoverable).
"""
import os
import secrets
import stat
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    """An environment variable, treating empty as unset.

    Docker Compose passes every key in an env_file through, so a line
    left blank as an invitation to use the default ("DATABASE_URL=")
    arrives as an empty string -- which os.environ.get happily returns
    in place of the default, and which then fails to parse as a URL.
    """
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number, got {raw!r}.") from exc


def _write_secret_file(path: Path, contents: bytes) -> None:
    """Write a secret readable only by this user, private from creation.

    Creating the file and then chmod-ing it leaves a window in which it
    is world-readable, which for the key that decrypts every PHI field
    is a window worth closing.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as handle:
        handle.write(contents)


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_env("APP_DATA_DIR", str(BASE_DIR / "data")) or str(BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL: str = _env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'reconcile.db'}") or ""

# Symmetric key used to encrypt PHI fields at rest (names, notes) and
# uploaded source documents. MUST be a stable, secret, 32-byte urlsafe
# base64 key (Fernet.generate_key()). If unset, a key is generated and
# persisted to data/.encryption_key for local/dev use only -- production
# deployments must supply APP_ENCRYPTION_KEY via a secrets manager.
_KEY_FILE = DATA_DIR / ".encryption_key"


ENCRYPTION_KEY_IS_GENERATED = False


def _load_or_create_key() -> bytes:
    global ENCRYPTION_KEY_IS_GENERATED
    env_key = _env("APP_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    if _KEY_FILE.exists():
        ENCRYPTION_KEY_IS_GENERATED = True
        return _KEY_FILE.read_bytes().strip()
    key = Fernet.generate_key()
    _write_secret_file(_KEY_FILE, key)
    ENCRYPTION_KEY_IS_GENERATED = True
    return key


ENCRYPTION_KEY = _load_or_create_key()
FERNET = Fernet(ENCRYPTION_KEY)

SESSION_SECRET_KEY: str = _env("SESSION_SECRET_KEY") or ""
_SESSION_KEY_FILE = DATA_DIR / ".session_secret"
if not SESSION_SECRET_KEY:
    if _SESSION_KEY_FILE.exists():
        SESSION_SECRET_KEY = _SESSION_KEY_FILE.read_text().strip()
    else:
        SESSION_SECRET_KEY = Fernet.generate_key().decode()
        _write_secret_file(_SESSION_KEY_FILE, SESSION_SECRET_KEY.encode())

# One-time token guarding the first-run /setup screen, so the window
# between the site answering on its domain and its owner creating the
# admin account isn't a window in which anyone else can. Set
# APP_SETUP_TOKEN to choose it; otherwise one is generated and written
# here, and startup prints where to read it from.
SETUP_TOKEN_FILE = DATA_DIR / ".setup_token"
SETUP_TOKEN: str = _env("APP_SETUP_TOKEN") or ""
if not SETUP_TOKEN:
    if SETUP_TOKEN_FILE.exists():
        SETUP_TOKEN = SETUP_TOKEN_FILE.read_text().strip()
    else:
        SETUP_TOKEN = secrets.token_urlsafe(24)
        _write_secret_file(SETUP_TOKEN_FILE, SETUP_TOKEN.encode())

# Idle session timeout, minutes. Meets a common HIPAA-workforce-security
# expectation of automatic logoff for sessions accessing PHI.
SESSION_IDLE_TIMEOUT_MINUTES = _env_int("SESSION_IDLE_TIMEOUT_MINUTES", 20)
SESSION_ABSOLUTE_TIMEOUT_HOURS = _env_int("SESSION_ABSOLUTE_TIMEOUT_HOURS", 10)

MAX_LOGIN_ATTEMPTS = _env_int("MAX_LOGIN_ATTEMPTS", 5)
LOCKOUT_MINUTES = _env_int("LOCKOUT_MINUTES", 15)

# Cookie is marked Secure by default; disable only for local http-only dev.
COOKIE_SECURE = (_env("COOKIE_SECURE", "true") or "true").lower() != "false"

ORGANIZATION_NAME: str = _env("ORGANIZATION_NAME", "Adult Day Program") or "Adult Day Program"
