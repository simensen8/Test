"""Encrypted-at-rest storage for uploaded source documents (Excel/PDF),
which contain PHI (participant names, attendance, billing)."""
import uuid
from pathlib import Path

from app.config import FERNET, UPLOAD_DIR


def save_encrypted(kind: str, original_filename: str, raw_bytes: bytes) -> str:
    suffix = Path(original_filename).suffix
    name = f"{kind}/{uuid.uuid4()}{suffix}.enc"
    dest = UPLOAD_DIR / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(FERNET.encrypt(raw_bytes))
    return str(dest.relative_to(UPLOAD_DIR))


def load_decrypted(stored_path: str) -> bytes:
    dest = UPLOAD_DIR / stored_path
    return FERNET.decrypt(dest.read_bytes())


def delete_stored(stored_path: str) -> None:
    """Remove an encrypted file. Missing is fine -- the point is that it
    is gone."""
    (UPLOAD_DIR / stored_path).unlink(missing_ok=True)
