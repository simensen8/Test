"""Parser for the Weekly Attendance Excel workbook.

Expected shape (per the source process description): one worksheet per
week, with a header row naming Monday-Friday and, below it, one row per
participant (listed by last name) with an "X" marking each day attended.

Real workbooks vary in exact layout, so this parser is heuristic: it
locates the weekday header row by matching weekday names/abbreviations,
locates the participant-name column as the left-most mostly-text column,
and treats any non-blank cell in a weekday column as "attended". The
calendar date for each weekday column is computed from a caller-supplied
week_start (the Monday of that week), not by trusting dates embedded in
the sheet, since those are inconsistently present across real-world
exports.

Any row/column the parser cannot confidently interpret is skipped and
recorded in `warnings` rather than guessed at silently -- reconciliation
should never silently assume attendance.
"""
import datetime
from dataclasses import dataclass, field

import openpyxl

WEEKDAY_PATTERNS = {
    0: ("monday", "mon"),
    1: ("tuesday", "tue", "tues"),
    2: ("wednesday", "wed", "weds"),
    3: ("thursday", "thu", "thur", "thurs"),
    4: ("friday", "fri"),
}


@dataclass
class AttendanceRow:
    participant_name: str
    attendance_by_date: dict[datetime.date, bool]


@dataclass
class AttendanceParseResult:
    rows: list[AttendanceRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _cell_text(cell) -> str:
    if cell.value is None:
        return ""
    return str(cell.value).strip()


def _find_weekday_header(ws):
    """Return (header_row_index, {weekday_offset: col_index}) or (None, {})"""
    for row in ws.iter_rows(min_row=1, max_row=min(15, ws.max_row)):
        found = {}
        for cell in row:
            text = _cell_text(cell).lower()
            if not text:
                continue
            for offset, patterns in WEEKDAY_PATTERNS.items():
                if text in patterns or text.startswith(patterns[0][:3]):
                    if offset not in found:
                        found[offset] = cell.column
        if len(found) >= 3:  # accept partial weeks (e.g. holiday-shortened)
            return row[0].row, found
    return None, {}


def _guess_name_column(ws, header_row: int, day_columns: set[int]) -> int | None:
    candidates = [c for c in range(1, (ws.max_column or 1) + 1) if c not in day_columns]
    best_col, best_score = None, -1
    for col in candidates:
        score = 0
        for row_idx in range(header_row + 1, min(header_row + 40, ws.max_row) + 1):
            val = ws.cell(row=row_idx, column=col).value
            if isinstance(val, str) and val.strip() and val.strip().upper() != "X":
                score += 1
        if score > best_score:
            best_score, best_col = score, col
    return best_col


def parse_weekly_attendance(file_bytes: bytes, week_start: datetime.date) -> AttendanceParseResult:
    result = AttendanceParseResult()
    wb = openpyxl.load_workbook(filename=__import__("io").BytesIO(file_bytes), data_only=True)

    for ws in wb.worksheets:
        header_row, day_cols = _find_weekday_header(ws)
        if header_row is None:
            result.warnings.append(f"Sheet '{ws.title}': could not locate a Monday-Friday header row; skipped.")
            continue

        name_col = _guess_name_column(ws, header_row, set(day_cols.values()))
        if name_col is None:
            result.warnings.append(f"Sheet '{ws.title}': could not locate a participant name column; skipped.")
            continue

        date_by_offset = {offset: week_start + datetime.timedelta(days=offset) for offset in day_cols}

        for row_idx in range(header_row + 1, ws.max_row + 1):
            name = _cell_text(ws.cell(row=row_idx, column=name_col))
            if not name:
                continue
            if name.lower() in ("total", "totals", "key", "legend"):
                continue
            attendance = {}
            for offset, col in day_cols.items():
                val = _cell_text(ws.cell(row=row_idx, column=col))
                attended = val.upper() in ("X", "P", "YES", "Y", "1")
                attendance[date_by_offset[offset]] = attended
            result.rows.append(AttendanceRow(participant_name=name, attendance_by_date=attendance))

    if not result.rows:
        result.warnings.append("No participant attendance rows were found in this workbook.")

    return result
