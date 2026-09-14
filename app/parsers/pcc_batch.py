"""Parser for the daily PCC Ancillary Batch export.

The real export (confirmed against an actual PointClickCare "Batch
Detail Report") is an HTML page -- typically produced by the browser's
"Save Page As... > Webpage, HTML Only" from PCC's batch detail report
screen (batchdetail_usnew.jsp) -- not a PDF. Its data table has stable
column headers: "#", "Participant Name (ID)", "Eff. Date", "Charge
Code", "Rev. Code", "HCPCS Code", "Description", "Payer Code", "Trans.
Type", "Units", "Unit Amount", "Total", "GL Acct.", "Days Acct."

The critical wrinkle: PCC's "Payer Code" column is NOT sufficient to
identify the actual payer source. In a real export, a Parker Grant day
and a plain Private Pay day are BOTH coded "PP-ADC" -- only the Charge
Code ("ADC P GRANT" vs "ADC MPNT"/"ADC MPT") or Description ("Adult Day
Care Parker Grant" vs "ADC Medical Prog...") actually distinguishes
them. So payer_source is derived from Charge Code (falling back to
Description), run through `categorize_payer` for the same canonical
vocabulary the Rate Master uses -- never from Payer Code alone.

A PDF fallback path is kept in case a site ever exports as PDF instead
(e.g. "Print to PDF" from the browser); it's the same best-effort
table/text-heuristic parser as before. Either way, every extracted row
is flagged `verified=False` and must be confirmed in the Batch Review
screen before it feeds reconciliation.
"""
import io
import re
from dataclasses import dataclass, field

import pdfplumber
from bs4 import BeautifulSoup

from app.payer_categories import categorize_payer, categorize_payer_from_fields

AMOUNT_RE = re.compile(r"\$?\s*(-?[\d,]{1,7}\.\d{2})\b")
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
    # raw_name may carry a trailing "(PAM-6)"-style facility ID; identity
    # resolution (app.matching.split_external_id) pulls it back out.
    raw_name: str
    payer_source: str | None
    amount: float | None
    units: str | None
    raw_line: str
    row_date: str | None = None  # as text, e.g. "9/10/2026", for date cross-checking


@dataclass
class BatchParseResult:
    rows: list[BatchRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extraction_method: str = "none"


# ---------------------------------------------------------------- HTML ----

def _looks_like_html(raw_bytes: bytes) -> bool:
    head = raw_bytes[:2048].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<table" in head


def _decode_html(raw_bytes: bytes) -> str:
    for encoding in ("windows-1252", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_via_html(raw_bytes: bytes) -> BatchParseResult:
    result = BatchParseResult(extraction_method="html-table")
    soup = BeautifulSoup(_decode_html(raw_bytes), "lxml")

    header_tr = None
    for tr in soup.find_all("tr"):
        text = tr.get_text(" ", strip=True)
        if "participant name" in text.lower() and "#" in text:
            header_tr = tr
            break

    if header_tr is None:
        result.warnings.append(
            "Could not find the PCC batch detail table (expected a 'Participant Name (ID)' column header)."
        )
        return result

    headers = [td.get_text(" ", strip=True).lower() for td in header_tr.find_all("td")]

    def col(*keys):
        for key in keys:
            for i, h in enumerate(headers):
                if key in h:
                    return i
        return None

    name_idx = col("participant name")
    date_idx = col("eff")
    charge_idx = col("charge")
    desc_idx = col("description")
    payer_code_idx = col("payer")
    amount_idx = col("total")
    units_idx = col("units")

    if name_idx is None:
        result.warnings.append("Could not identify the participant name column in the PCC batch table.")
        return result

    table = header_tr.find_parent("table")
    rows_in_table = table.find_all("tr") if table else soup.find_all("tr")
    start = rows_in_table.index(header_tr) + 1 if header_tr in rows_in_table else 0

    for tr in rows_in_table[start:]:
        tds = tr.find_all("td")
        if len(tds) <= max(i for i in [name_idx, date_idx, charge_idx, desc_idx, payer_code_idx, amount_idx, units_idx] if i is not None):
            continue
        first_cell = tds[0].get_text(strip=True)
        if not first_cell.isdigit():
            continue  # subtotal/"Revised By"/separator rows don't start with a row number

        vals = [td.get_text(" ", strip=True) for td in tds]
        name_raw = vals[name_idx]
        if not name_raw:
            continue

        charge_text = vals[charge_idx] if charge_idx is not None else ""
        desc_text = vals[desc_idx] if desc_idx is not None else ""
        payer_code_text = vals[payer_code_idx] if payer_code_idx is not None else ""
        # Charge Code / Description identify the actual payer arrangement
        # (e.g. "ADC P GRANT" = Parker Grant); Payer Code alone conflates
        # Private Pay and Parker Grant under the same "PP-ADC" code.
        category = categorize_payer_from_fields(charge_text, desc_text, payer_code_text)

        amount = None
        if amount_idx is not None:
            m = AMOUNT_RE.search(vals[amount_idx])
            if m:
                amount = float(m.group(1).replace(",", ""))

        units = vals[units_idx] if units_idx is not None else None
        row_date = vals[date_idx] if date_idx is not None else None

        raw_line = " | ".join(f"{h}: {v}" for h, v in zip(
            ["Charge Code", "Description", "Payer Code"], [charge_text, desc_text, payer_code_text]
        ) if v)

        result.rows.append(BatchRow(
            raw_name=name_raw, payer_source=category,
            amount=amount, units=units, raw_line=raw_line, row_date=row_date,
        ))

    if not result.rows:
        result.warnings.append("The batch table was found but no participant rows could be extracted from it.")

    return result


# ----------------------------------------------------------------- PDF ----

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
                payer_raw = str(data_row[payer_idx]).strip() if payer_idx is not None and data_row[payer_idx] else None
                amount = None
                if amount_idx is not None and data_row[amount_idx]:
                    m = AMOUNT_RE.search(str(data_row[amount_idx]))
                    if m:
                        amount = float(m.group(1).replace(",", ""))
                units = str(data_row[units_idx]).strip() if units_idx is not None and data_row[units_idx] else None
                rows.append(BatchRow(
                    raw_name=name, payer_source=categorize_payer(payer_raw),
                    amount=amount, units=units,
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
            payer_raw = None
            lowered = line.lower()
            for kw in PAYER_KEYWORDS:
                if kw in lowered:
                    payer_raw = kw
                    break
            amount = None
            amt_match = AMOUNT_RE.search(line)
            if amt_match:
                amount = float(amt_match.group(1).replace(",", ""))
            rows.append(BatchRow(
                raw_name=name, payer_source=categorize_payer(payer_raw),
                amount=amount, units=None, raw_line=line,
            ))
    return rows


def _extract_via_pdf(raw_bytes: bytes) -> BatchParseResult:
    result = BatchParseResult()
    with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
        table_rows = _extract_via_tables(pdf)
        if table_rows is not None:
            result.rows = table_rows
            result.extraction_method = "pdf-table"
        else:
            result.rows = _extract_via_text(pdf)
            result.extraction_method = "pdf-text-heuristic"
            result.warnings.append(
                "No structured table detected in this PDF; used text-pattern extraction. "
                "Please carefully verify every row below before reconciling."
            )
    return result


# --------------------------------------------------------------- shared ----

def parse_pcc_batch(file_bytes: bytes) -> BatchParseResult:
    if _looks_like_html(file_bytes):
        result = _extract_via_html(file_bytes)
    else:
        result = _extract_via_pdf(file_bytes)

    if not result.rows and not result.warnings:
        result.warnings.append("No participant billing lines could be extracted from this file. Manual entry required.")

    missing_payer = sum(1 for r in result.rows if not r.payer_source)
    if missing_payer:
        result.warnings.append(f"{missing_payer} row(s) had no payer source detected; please fill in during review.")

    return result
