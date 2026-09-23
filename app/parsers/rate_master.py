"""Parser for the Rate Master Excel (the consolidated payer-exception
cases). Any participant not present in the resulting rule set defaults
to Private Pay with no special rule, per the source process.

Real workbooks carry multiple sheets (e.g. a legacy "Old Rates" sheet
alongside a current "NEW Rates as of 1.1.2025" sheet); the wrong one
must never be silently used, so sheet selection actively scores
candidates rather than always taking the first sheet in the file.

Column headers vary (a real "Discount" column is where free-text
billing exceptions live, not a column literally named "notes" or
"exception"), so columns are matched by keyword rather than fixed
position. Payer text -- both the clean "Payor" column and the free-text
exception column -- is normalized through `categorize_payer` so it can
be compared against a PCC batch's payer categorization later.

A best-effort attempt is made to detect a "rotating payer" arrangement
(e.g. "3 days Private Pay then 4th and 5th Day Parker Grant", "2nd and
3rd day Title III", "1 day VA - 1 day private pay per week") from the
free text. Real exception wording is diverse and sometimes has no
day-count at all (e.g. "Full PG while appealing Medicaid denial" is a
temporary status note, not a rotation), so detection intentionally
requires an explicit day count before classifying a rule as a rotation
-- anything it can't confidently parse is left as grant_rule_type=none
with the original text preserved in notes, and every parsed rule (auto
or not) should be spot-checked by an admin in the Rate Master screen.
"""
import io
import re
from dataclasses import dataclass, field

import openpyxl

from app.payer_categories import categorize_payer

NAME_KEYS = ("participant", "resident", "name", "last name")
PAYER_KEYS = ("payer", "payor", "source", "primary payer", "primary payor")
RATE_KEYS = ("client rate", "rate")
NOTE_KEYS = ("discount", "exception", "note", "notes", "rule", "arrangement", "special", "grant")
ID_KEYS = ("pat id", "patient id", "id number", "participant id")

# --- rotation ("N days X then M days Y") detection -------------------------

_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

_ORDINAL_RE = re.compile(r"\b(\d+)\s*(?:st|nd|rd|th)\b", re.I)
_CARDINAL_DAY_RE = re.compile(r"\b(\d+)\s*days?\b", re.I)
_WORD_DAY_RE = re.compile(r"\b(" + "|".join(_WORD_NUMS) + r")\s+days?\b", re.I)
_DAY_N_RE = re.compile(r"\bday\s+(\d+)\b", re.I)
_AND_N_RE = re.compile(r"(?:\band\s+|&\s*)(\d+)\b", re.I)
_LEADING_NUM_RE = re.compile(r"^\s*(\d+)\b")
_LEADING_WORD_RE = re.compile(r"^\s*(" + "|".join(_WORD_NUMS) + r")\b", re.I)


def _explicit_count(text: str) -> int | None:
    m = _CARDINAL_DAY_RE.search(text)
    if m:
        return int(m.group(1))
    m = _WORD_DAY_RE.search(text)
    if m:
        return _WORD_NUMS[m.group(1).lower()]
    return None


def _position_count(text: str) -> int | None:
    positions = set()
    for pattern in (_ORDINAL_RE, _DAY_N_RE, _AND_N_RE):
        for m in pattern.finditer(text):
            positions.add(int(m.group(1)))
    return len(positions) if positions else None


def _leading_count(text: str) -> int | None:
    m = _LEADING_NUM_RE.match(text.strip())
    if m:
        return int(m.group(1))
    m = _LEADING_WORD_RE.match(text.strip())
    if m:
        return _WORD_NUMS[m.group(1).lower()]
    return None


def _half_count(text: str) -> int | None:
    return _explicit_count(text) or _position_count(text) or _leading_count(text)


def _detect_rotation(text: str) -> tuple[str, int | None, int, str | None]:
    """Returns (grant_rule_type, primary_days, secondary_days, grant_payer)."""
    if not text or not text.strip():
        return "none", None, 1, None

    lower = text.lower()
    then_idx = lower.find("then")
    if then_idx != -1:
        pre, post = text[:then_idx], text[then_idx + 4:]
        primary = _half_count(pre)
        if primary is not None:
            secondary = _half_count(post) or 1
            return "rotating_grant", primary, secondary, categorize_payer(post)

    for sep in (",", " - "):
        if sep in text:
            pre, post = text.split(sep, 1)
            pre_count, post_count = _half_count(pre), _half_count(post)
            if pre_count is not None and post_count is not None:
                return "rotating_grant", pre_count, post_count, categorize_payer(post)

    ords = [int(m.group(1)) for m in _ORDINAL_RE.finditer(text)]
    if ords:
        primary = min(ords) - 1
        secondary = len(set(ords))
        if primary >= 1:
            return "rotating_grant", primary, secondary, categorize_payer(text)

    return "none", None, 1, None


# --------------------------------------------------------------- parsing ---

@dataclass
class RateRuleRow:
    participant_name: str
    payer_source: str
    rate: float | None
    grant_rule_type: str  # "none" | "rotating_grant"
    grant_cycle_length: int | None
    grant_cycle_secondary_days: int
    grant_payer: str | None
    notes: str | None


@dataclass
class RateMasterParseResult:
    rows: list[RateRuleRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sheet_used: str | None = None


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


def _find_header_row(ws) -> tuple[int | None, list[str]]:
    for row in ws.iter_rows(min_row=1, max_row=min(10, ws.max_row)):
        texts = [str(c.value).strip() if c.value is not None else "" for c in row]
        if any(t.lower() in NAME_KEYS or "name" in t.lower() for t in texts):
            return row[0].row, texts
    return None, []


def _score_sheet(ws, name: str) -> tuple[int, int]:
    """Higher is better: (keyword_score, data_row_count)."""
    header_row, headers = _find_header_row(ws)
    if header_row is None or _match_header(headers, NAME_KEYS) is None:
        return (-1000, 0)
    lname = name.lower()
    keyword_score = 0
    if "new" in lname or "current" in lname:
        keyword_score += 100
    if "old" in lname or "legacy" in lname or "prior" in lname or "archive" in lname:
        keyword_score -= 100
    name_idx = _match_header(headers, NAME_KEYS)
    if name_idx is None:
        # No name column: the sheet may still be scored on its title, but
        # there are no participant rows to count.
        return (keyword_score, 0)

    data_rows = 0
    for row_idx in range(header_row + 1, ws.max_row + 1):
        val = ws.cell(row=row_idx, column=name_idx + 1).value
        if val and str(val).strip():
            data_rows += 1
    return (keyword_score, data_rows)


def _select_sheet(wb):
    scored = [(name, _score_sheet(wb[name], name)) for name in wb.sheetnames]
    scored = [s for s in scored if s[1][0] > -1000]
    if not scored:
        return wb.worksheets[0], [wb.worksheets[0].title]
    scored.sort(key=lambda s: s[1], reverse=True)
    return wb[scored[0][0]], [name for name, _ in scored]


def parse_rate_master(file_bytes: bytes) -> RateMasterParseResult:
    result = RateMasterParseResult()
    wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)

    ws, _candidates = _select_sheet(wb)
    result.sheet_used = ws.title
    if len(wb.sheetnames) > 1:
        result.warnings.append(
            f"This workbook has multiple sheets ({', '.join(wb.sheetnames)}); "
            f"used '{ws.title}' as the current Rate Master. If that's wrong, "
            f"rename the correct sheet to include \"new\" or \"current\" and re-upload."
        )

    header_row_idx, headers = _find_header_row(ws)
    if header_row_idx is None:
        result.warnings.append("Could not locate a header row containing a participant/name column.")
        return result

    name_idx = _match_header(headers, NAME_KEYS)
    payer_idx = _match_header(headers, PAYER_KEYS)
    rate_idx = _match_header(headers, RATE_KEYS)
    note_idx = _match_header(headers, NOTE_KEYS)

    if name_idx is None:
        result.warnings.append("Could not identify the participant name column on this sheet.")
        return result
    if payer_idx is None:
        result.warnings.append("Could not identify the payer source column; payer will need manual entry.")

    for row_idx in range(header_row_idx + 1, ws.max_row + 1):
        name_val = ws.cell(row=row_idx, column=name_idx + 1).value
        name = str(name_val).strip() if name_val else ""
        if not name:
            continue

        payer_raw_val = ws.cell(row=row_idx, column=payer_idx + 1).value if payer_idx is not None else None
        payer_raw = str(payer_raw_val).strip() if payer_raw_val else None
        payer = categorize_payer(payer_raw) or "Private Pay"

        rate_val = ws.cell(row=row_idx, column=rate_idx + 1).value if rate_idx is not None else None
        rate: float | None = None
        rate_note = None
        if rate_val not in (None, ""):
            try:
                rate = float(rate_val)
            except (TypeError, ValueError):
                rate_note = f"Rate column: {rate_val}"

        discount_val = ws.cell(row=row_idx, column=note_idx + 1).value if note_idx is not None else None
        discount_text = str(discount_val).strip() if discount_val else None

        rule_type, cycle_length, secondary_days, grant_payer = _detect_rotation(discount_text or "")
        if rule_type == "rotating_grant":
            result.warnings.append(
                f"'{name}': auto-detected a rotating-payer rule from \"{discount_text}\" "
                f"({cycle_length} day(s) {payer}, then {secondary_days} day(s) {grant_payer or '?'}) "
                f"-- please confirm this in the Rate Master editor."
            )
        elif discount_text:
            result.warnings.append(
                f"'{name}': has an exception note that wasn't auto-parsed into a rule "
                f"(\"{discount_text}\") -- set it up manually in the Rate Master editor if it affects billing."
            )

        notes_parts = [p for p in [discount_text, rate_note, (payer_raw if payer_raw and payer_raw != payer else None)] if p]
        notes = " | ".join(notes_parts) if notes_parts else None

        result.rows.append(
            RateRuleRow(
                participant_name=name,
                payer_source=payer,
                rate=rate,
                grant_rule_type=rule_type,
                grant_cycle_length=cycle_length,
                grant_cycle_secondary_days=secondary_days,
                grant_payer=grant_payer,
                notes=notes,
            )
        )

    if not result.rows:
        result.warnings.append("No rate rule rows were found below the header row.")

    return result
