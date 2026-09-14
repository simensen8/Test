"""Parser for the Rate Master Excel (the ~36 consolidated payer-exception
cases). Any participant not present in the resulting rule set defaults to
Private Pay with no special rule, per the source process.

Column headers vary, so columns are matched by keyword rather than fixed
position. A best-effort attempt is made to detect a "rotating grant"
arrangement (e.g. "3 paid days, 4th day to Parker Grant") from free text
in a notes/exception column; the exact cycle length and grant payer name
should always be confirmed by an admin in the Rate Master screen since
free-text exception wording varies and misreading it silently would
produce wrong billing expectations.
"""
import io
import re
from dataclasses import dataclass, field

import openpyxl

NAME_KEYS = ("participant", "resident", "name", "last name")
PAYER_KEYS = ("payer", "payor", "source", "primary payer", "primary payor")
RATE_KEYS = ("rate",)
NOTE_KEYS = ("exception", "note", "notes", "rule", "grant", "arrangement", "special")

ROTATION_RE = re.compile(r"(\d+)\s*(?:paid|billed|private)?.{0,20}?(?:then|before|,|/)\s*(?:the\s*)?(\d+)(?:st|nd|rd|th)?\s*(?:day)?", re.IGNORECASE)
GRANT_NAME_RE = re.compile(r"([A-Z][A-Za-z]*\s+Grant)", re.IGNORECASE)


@dataclass
class RateRuleRow:
    participant_name: str
    payer_source: str
    rate: float | None
    grant_rule_type: str  # "none" | "rotating_grant"
    grant_cycle_length: int | None
    grant_payer: str | None
    notes: str | None


@dataclass
class RateMasterParseResult:
    rows: list[RateRuleRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _match_header(headers: list[str], keys: tuple[str, ...]) -> int | None:
    lowered = [h.lower().strip() for h in headers]
    for key in keys:
        for i, h in enumerate(lowered):
            if h == key:
                return i
    for key in keys:
        for i, h in enumerate(lowered):
            if key in h:
                return i
    return None


def _detect_rotation(text: str) -> tuple[str, int | None, str | None]:
    if not text:
        return "none", None, None
    if "grant" not in text.lower():
        return "none", None, None
    grant_match = GRANT_NAME_RE.search(text)
    grant_name = grant_match.group(1).strip() if grant_match else "Grant"
    rot_match = ROTATION_RE.search(text)
    cycle_length = int(rot_match.group(1)) if rot_match else None
    return "rotating_grant", cycle_length, grant_name


def parse_rate_master(file_bytes: bytes) -> RateMasterParseResult:
    result = RateMasterParseResult()
    wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
    ws = wb.worksheets[0]

    header_row_idx = None
    headers: list[str] = []
    for row in ws.iter_rows(min_row=1, max_row=min(10, ws.max_row)):
        texts = [str(c.value).strip() if c.value is not None else "" for c in row]
        if any(t.lower() in NAME_KEYS or "name" in t.lower() for t in texts):
            header_row_idx = row[0].row
            headers = texts
            break

    if header_row_idx is None:
        result.warnings.append("Could not locate a header row containing a participant/name column.")
        return result

    name_idx = _match_header(headers, NAME_KEYS)
    payer_idx = _match_header(headers, PAYER_KEYS)
    rate_idx = _match_header(headers, RATE_KEYS)
    note_idx = _match_header(headers, NOTE_KEYS)

    if name_idx is None:
        result.warnings.append("Could not identify the participant name column.")
        return result
    if payer_idx is None:
        result.warnings.append("Could not identify the payer source column; payer will need manual entry.")

    for row_idx in range(header_row_idx + 1, ws.max_row + 1):
        name_val = ws.cell(row=row_idx, column=name_idx + 1).value
        name = str(name_val).strip() if name_val else ""
        if not name:
            continue

        payer_val = ws.cell(row=row_idx, column=payer_idx + 1).value if payer_idx is not None else None
        payer = str(payer_val).strip() if payer_val else "Private Pay"

        rate_val = ws.cell(row=row_idx, column=rate_idx + 1).value if rate_idx is not None else None
        try:
            rate = float(rate_val) if rate_val not in (None, "") else None
        except (TypeError, ValueError):
            rate = None

        notes_val = ws.cell(row=row_idx, column=note_idx + 1).value if note_idx is not None else None
        notes = str(notes_val).strip() if notes_val else None

        rule_type, cycle_length, grant_payer = _detect_rotation(notes or "")
        if rule_type == "rotating_grant" and cycle_length is None:
            result.warnings.append(
                f"'{name}': a grant/rotation exception was mentioned but the cycle length "
                f"could not be parsed automatically -- please confirm in Rate Master editor."
            )

        result.rows.append(
            RateRuleRow(
                participant_name=name,
                payer_source=payer,
                rate=rate,
                grant_rule_type=rule_type,
                grant_cycle_length=cycle_length,
                grant_payer=grant_payer,
                notes=notes,
            )
        )

    if not result.rows:
        result.warnings.append("No rate rule rows were found below the header row.")

    return result
