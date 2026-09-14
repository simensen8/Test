import datetime
import io

import openpyxl

from app.parsers.attendance import parse_weekly_attendance
from app.parsers.pcc_batch import parse_pcc_batch
from app.parsers.rate_master import parse_rate_master

MON = datetime.date(2026, 9, 14)


def _xlsx_bytes(rows, headers=("Name", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday")):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(headers))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_weekly_attendance_basic():
    data = _xlsx_bytes([
        ("Adams", "X", "", "X", "", "X"),
        ("Baker", "", "", "", "", ""),
    ])
    result = parse_weekly_attendance(data, MON)
    assert not result.warnings
    assert len(result.rows) == 2
    adams = next(r for r in result.rows if r.participant_name == "Adams")
    assert adams.attendance_by_date[MON] is True
    assert adams.attendance_by_date[MON + datetime.timedelta(days=1)] is False
    assert adams.attendance_by_date[MON + datetime.timedelta(days=2)] is True


def test_parse_weekly_attendance_missing_header_warns():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Just some", "random", "columns"])
    ws.append(["a", "b", "c"])
    buf = io.BytesIO()
    wb.save(buf)
    result = parse_weekly_attendance(buf.getvalue(), MON)
    assert result.rows == []
    assert result.warnings


def test_parse_rate_master_basic():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Participant Name", "Payer Source", "Rate", "Exception Notes"])
    ws.append(["Baker", "Medicare", 50.0, ""])
    ws.append(["Carter", "Private Pay", 45.0, "3 paid days, 4th day billed to Parker Grant"])
    buf = io.BytesIO()
    wb.save(buf)

    result = parse_rate_master(buf.getvalue())
    assert len(result.rows) == 2
    baker = next(r for r in result.rows if r.participant_name == "Baker")
    assert baker.payer_source == "Medicare"
    assert baker.grant_rule_type == "none"

    carter = next(r for r in result.rows if r.participant_name == "Carter")
    assert carter.grant_rule_type == "rotating_grant"
    assert carter.grant_cycle_length == 3
    assert "Grant" in (carter.grant_payer or "")


def test_parse_pcc_batch_text_heuristic():
    import fpdf
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Adams, Alice   Private Pay   $45.00",
        "Baker, Bob     Medicare      $50.00",
    ]:
        pdf.cell(0, 8, text=line, new_x="LMARGIN", new_y="NEXT")
    raw = bytes(pdf.output())

    result = parse_pcc_batch(raw)
    assert result.extraction_method == "text-heuristic"
    assert len(result.rows) == 2
    names = {r.raw_name for r in result.rows}
    assert names == {"Adams, Alice", "Baker, Bob"}
    amounts = {r.raw_name: r.amount for r in result.rows}
    assert amounts["Adams, Alice"] == 45.0
    assert amounts["Baker, Bob"] == 50.0
    payers = {r.raw_name: r.payer_source for r in result.rows}
    assert payers["Baker, Bob"] == "Medicare"
