"""Parser for the Weekly Attendance workbook.

Real files are `.ods` (OpenDocument Spreadsheet) as often as `.xlsx`, so
both are supported. Expected shape (per the source process and a real
sample file): one worksheet per week, with a header row naming
Monday-Friday (a short week, e.g. only Monday-Thursday around a holiday,
is normal) and, below it, one row per participant -- "Lastname,
Firstname (PAM-123)" or just a bare last name -- with a mark ("x") in
each day attended.

A "Trials:" row, if present, marks the end of the real roster: PCC never
bills trial participants (they aren't "active" yet), so anything listed
under it must be excluded from the MISSING_FROM_BATCH check or every
trial participant would permanently, incorrectly show as an exception.

Real workbooks vary in exact layout, so this parser is heuristic: it
locates the weekday header row by matching weekday names/abbreviations,
locates the participant-name column as the left-most mostly-text column,
and treats any non-blank cell in a weekday column as "attended". The
calendar date for each weekday column is computed from a caller-supplied
week_start (the Monday of that week), not by trusting dates embedded in
the sheet -- real files have been seen with a stale/incorrect "Week
starting" label left over from copy-pasting a prior week's template.

Any row/column the parser cannot confidently interpret is skipped and
recorded in `warnings` rather than guessed at silently -- reconciliation
should never silently assume attendance.
"""
import datetime
import io
import zipfile
from dataclasses import dataclass, field

import openpyxl

WEEKDAY_PATTERNS = {
    0: ("monday", "mon"),
    1: ("tuesday", "tue", "tues"),
    2: ("wednesday", "wed", "weds"),
    3: ("thursday", "thu", "thur", "thurs"),
    4: ("friday", "fri"),
}

END_OF_ROSTER_MARKERS = ("trials", "trials:", "discharged", "discharges")
SKIP_NAME_VALUES = ("total", "totals", "key", "legend", "")

# Cap how far a single repeated-cell run expands to (ODS compresses long
# runs of identical trailing blank cells into one element with a
# number-columns-repeated attribute, which can be in the thousands).
_MAX_REPEAT_EXPAND = 80


@dataclass
class AttendanceRow:
    participant_name: str
    attendance_by_date: dict[datetime.date, bool]


@dataclass
class AttendanceParseResult:
    rows: list[AttendanceRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------- sheet loading ----

def _is_ods(file_bytes: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            if "mimetype" in z.namelist():
                return b"opendocument.spreadsheet" in z.read("mimetype")
            return "content.xml" in z.namelist()
    except zipfile.BadZipFile:
        return False


def _load_ods_sheets(file_bytes: bytes) -> list[tuple[str, list[list[str]]]]:
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    def cell_text(cell) -> str:
        return "".join(str(p) for p in cell.getElementsByType(P))

    doc = load(io.BytesIO(file_bytes))
    sheets = []
    for table in doc.spreadsheet.getElementsByType(Table):
        name = table.getAttribute("name") or "Sheet"
        rows: list[list[str]] = []
        for row in table.getElementsByType(TableRow):
            vals: list[str] = []
            for cell in row.getElementsByType(TableCell):
                txt = cell_text(cell)
                repeat_attr = cell.getAttribute("numbercolumnsrepeated")
                repeat = min(int(repeat_attr), _MAX_REPEAT_EXPAND) if repeat_attr else 1
                vals.extend([txt] * repeat)
            rows.append(vals)
        sheets.append((name, rows))
    return sheets


def _load_xlsx_sheets(file_bytes: bytes) -> list[tuple[str, list[list[str]]]]:
    wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows():
            rows.append(["" if c.value is None else str(c.value).strip() for c in row])
        sheets.append((ws.title, rows))
    return sheets


def _load_sheets(file_bytes: bytes) -> list[tuple[str, list[list[str]]]]:
    if _is_ods(file_bytes):
        return _load_ods_sheets(file_bytes)
    return _load_xlsx_sheets(file_bytes)


# --------------------------------------------------------------- parsing ----

def _find_weekday_header(rows: list[list[str]]) -> tuple[int | None, dict[int, int]]:
    """Return (header_row_index, {weekday_offset: col_index}), 0-based."""
    for row_idx, row in enumerate(rows[:15]):
        found = {}
        for col_idx, raw in enumerate(row):
            text = (raw or "").strip().lower()
            if not text:
                continue
            for offset, patterns in WEEKDAY_PATTERNS.items():
                if text in patterns or text.startswith(patterns[0][:3]):
                    if offset not in found:
                        found[offset] = col_idx
        if len(found) >= 3:  # accept partial weeks (e.g. holiday-shortened)
            return row_idx, found
    return None, {}


def _guess_name_column(rows: list[list[str]], header_row: int, day_columns: set[int]) -> int | None:
    max_cols = max((len(r) for r in rows), default=0)
    candidates = [c for c in range(max_cols) if c not in day_columns]
    best_col, best_score = None, -1
    for col in candidates:
        score = 0
        for row_idx in range(header_row + 1, min(header_row + 40, len(rows))):
            row = rows[row_idx]
            val = row[col] if col < len(row) else ""
            if val and val.strip() and val.strip().upper() != "X":
                score += 1
        if score > best_score:
            best_score, best_col = score, col
    return best_col


def parse_weekly_attendance(file_bytes: bytes, week_start: datetime.date) -> AttendanceParseResult:
    result = AttendanceParseResult()
    sheets = _load_sheets(file_bytes)

    for sheet_name, rows in sheets:
        header_row, day_cols = _find_weekday_header(rows)
        if header_row is None:
            result.warnings.append(f"Sheet '{sheet_name}': could not locate a Monday-Friday header row; skipped.")
            continue

        name_col = _guess_name_column(rows, header_row, set(day_cols.values()))
        if name_col is None:
            result.warnings.append(f"Sheet '{sheet_name}': could not locate a participant name column; skipped.")
            continue

        date_by_offset = {offset: week_start + datetime.timedelta(days=offset) for offset in day_cols}

        end_of_roster = False
        for row_idx in range(header_row + 1, len(rows)):
            if end_of_roster:
                break
            row = rows[row_idx]
            name = (row[name_col].strip() if name_col < len(row) else "")
            if not name:
                continue
            lowered = name.lower().rstrip(":")
            if lowered in END_OF_ROSTER_MARKERS:
                end_of_roster = True
                continue
            if lowered in SKIP_NAME_VALUES:
                continue

            attendance = {}
            for offset, col in day_cols.items():
                val = (row[col].strip() if col < len(row) else "")
                attended = val.upper() in ("X", "P", "YES", "Y", "1")
                attendance[date_by_offset[offset]] = attended
            result.rows.append(AttendanceRow(participant_name=name, attendance_by_date=attendance))

    if not result.rows:
        result.warnings.append("No participant attendance rows were found in this workbook.")

    return result
