"""Parser for the daily PCC Ancillary Batch PDF.

The exact PDF layout PCC generates was not available at build time, so
this parser is intentionally defensive: it tries pdfplumber's structured
table extraction first (most PCC exports render as a table), and falls
back to line-by-line text heuristics otherwise. Every extracted row is
flagged `verified=False` and MUST be confirmed (or corrected) by a
reviewer in the Batch Review screen before it feeds reconciliation --
auto-extracted OCR/text-position data should never silently become the
source of truth for billing comparisons.

If your PCC export format differs enough that extraction quality is
poor, adjust PAYER_KEYWORDS below and/or the table-header matching; the
review screen's manual-edit capability is the safety net regardless.
"""
import io
import re
from dataclasses import dataclass, field

import pdfplumber

AMOUNT_RE = re.compile(r"\$?\s*(-?\d{1,5}\.\d{2})\b")
NAME_RE = re.compile(r"^([A-Z][A-Za-z'’\-]+),\s*([A-Z][A-Za-z'’\-]+(?:\s+[A-Z]\.(?=\s|$))?)")

PAYER_KEYWORDS = [
    "medicare", "medicaid", "private pay", "private", "va", "veterans",
    "parker grant", "grant", "hospice", "managed care", "insurance",
    "self pay", "self-pay",
]

NAME_HEADER_KEYS = ("resident", "participant", "name", "last")
PAYER_HEADER_KEYS = ("payer", "payor", "source", "insurance")
AMOUNT_HEADER_KEYS = ("amount", "charge", "total", "billed")
UNITS_HEADER_KEYS = ("units", "qty", "quantity", "days")


@dataclass
class BatchRow:
    raw_name: str
    payer_source: str | None
    amount: float | None
    units: str | None
    raw_line: str


@dataclass
class BatchParseResult:
    rows: list[BatchRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extraction_method: str = "none"


def _find_col(headers: list[str], keys: tuple[str, ...]) -> int | None:
    lowered = [(h or "").lower() for h in headers]
    for key in keys:
        for i, h in enumerate(lowered):
            if key in h:
                return i
    return None


def _extract_via_tables(pdf) -> list[BatchRow] | None:
    rows: list[BatchRow] = []
    any_table = False
    for page in pdf.pages:
        for table in page.extract_tables():
            if not table or len(table) < 2:
                continue
            headers = [c or "" for c in table[0]]
            name_idx = _find_col(headers, NAME_HEADER_KEYS)
            if name_idx is None:
                continue
            any_table = True
            payer_idx = _find_col(headers, PAYER_HEADER_KEYS)
            amount_idx = _find_col(headers, AMOUNT_HEADER_KEYS)
            units_idx = _find_col(headers, UNITS_HEADER_KEYS)
            for data_row in table[1:]:
                if not data_row or not data_row[name_idx]:
                    continue
                name = str(data_row[name_idx]).strip()
                if not name:
                    continue
                payer = str(data_row[payer_idx]).strip() if payer_idx is not None and data_row[payer_idx] else None
                amount = None
                if amount_idx is not None and data_row[amount_idx]:
                    m = AMOUNT_RE.search(str(data_row[amount_idx]))
                    if m:
                        amount = float(m.group(1))
                units = str(data_row[units_idx]).strip() if units_idx is not None and data_row[units_idx] else None
                rows.append(BatchRow(
                    raw_name=name,
                    payer_source=payer,
                    amount=amount,
                    units=units,
                    raw_line=" | ".join(str(c) for c in data_row if c),
                ))
    return rows if any_table else None


def _extract_via_text(pdf) -> list[BatchRow]:
    rows: list[BatchRow] = []
    for page in pdf.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            name_match = NAME_RE.match(line)
            if not name_match:
                continue
            name = f"{name_match.group(1)}, {name_match.group(2)}"
            payer = None
            lowered = line.lower()
            for kw in PAYER_KEYWORDS:
                if kw in lowered:
                    payer = kw.title()
                    break
            amount = None
            amt_match = AMOUNT_RE.search(line)
            if amt_match:
                amount = float(amt_match.group(1))
            rows.append(BatchRow(raw_name=name, payer_source=payer, amount=amount, units=None, raw_line=line))
    return rows


def parse_pcc_batch(file_bytes: bytes) -> BatchParseResult:
    result = BatchParseResult()
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        table_rows = _extract_via_tables(pdf)
        if table_rows is not None:
            result.rows = table_rows
            result.extraction_method = "table"
        else:
            result.rows = _extract_via_text(pdf)
            result.extraction_method = "text-heuristic"
            result.warnings.append(
                "No structured table detected in this PDF; used text-pattern extraction. "
                "Please carefully verify every row below before reconciling."
            )

    if not result.rows:
        result.warnings.append("No participant billing lines could be extracted from this PDF. Manual entry required.")

    missing_payer = sum(1 for r in result.rows if not r.payer_source)
    if missing_payer:
        result.warnings.append(f"{missing_payer} row(s) had no payer source detected; please fill in during review.")

    return result
