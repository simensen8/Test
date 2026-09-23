"""Reading values off a submitted form.

Starlette types a form value as `str | UploadFile`, since one field can
be either. Every caller that wants text wants it as text, so the
conversion happens once here rather than being assumed at each use.
"""
from starlette.datastructures import FormData, UploadFile


def form_text(form: FormData, key: str, default: str = "") -> str:
    value = form.get(key)
    if value is None or isinstance(value, UploadFile):
        return default
    return value


def uploaded_files(form: FormData, *keys: str) -> list[UploadFile]:
    """Every file part under the given field names that actually has a
    file attached. An empty file input arrives as an empty string."""
    parts: list[UploadFile] = []
    for key in keys:
        for value in form.getlist(key):
            if isinstance(value, UploadFile) and value.filename:
                parts.append(value)
    return parts
