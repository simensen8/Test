"""Parsing the dates that arrive in URLs and form fields.

A date in a path is whatever the browser sent, and every screen here is
addressed by one. Parsing it with `date.fromisoformat` directly turns a
mistyped or stale URL into a 500 and a blank error page, so the callers
take a None back and redirect with something readable instead.
"""
import datetime


def parse_iso_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
