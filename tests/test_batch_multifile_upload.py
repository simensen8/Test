"""Multi-file PCC batch upload: a week's exports in one go, each filed
under the service date printed on the file itself."""
import datetime
import re

from sqlalchemy import select

from app.models import BillingRecord, Upload, UploadKind
from app.parsers.pcc_batch import parse_pcc_batch

HEADER_ROW = """
  <tr valign="bottom">
    <td>#</td><td>Participant Name (ID)</td><td align="right">Eff.&nbsp;Date</td>
    <td align="center">Charge<br>Code</td><td align="center">Rev.<br>Code</td>
    <td align="center">HCPCS<br>Code</td><td>Description</td><td>Payer<br>Code</td>
    <td align="center">Trans.&nbsp;Type</td><td>Units</td><td align="center">Unit<br>Amount</td>
    <td align="center">Total</td><td align="center">GL Acct.</td><td align="center">Days Acct.</td>
  </tr>
"""


def _row(number: int, name: str, eff_date: str) -> str:
    return f"""
  <tr>
    <td>{number}</td><td>{name}</td><td align="right">{eff_date}</td>
    <td align="right">ADC MPNT</td><td></td><td></td>
    <td>ADC Medical Prog. W/o Transp</td><td>PP-ADC</td>
    <td align="center">A</td><td align="right">1</td><td align="right">$95.00</td>
    <td align="right">$95.00</td><td align="right">5000-00-310321-230</td><td></td>
  </tr>
"""


def batch_html(service_date: str, names: list[str], row_date: str | None = None, header: bool = True) -> bytes:
    """A trimmed PCC batch export. `service_date` is the batch header's
    "Services for ..." day; `row_date` (defaulting to it, and settable to
    "" for an export with no Eff. Date at all) is each row's Eff. Date,
    so the two can be made to disagree."""
    row_date = service_date if row_date is None else row_date
    header_block = (
        f"<table><tr><td>911</td><td>Services for {service_date}</td><td>_system_</td></tr></table>"
        if header else ""
    )
    rows = "".join(_row(i + 1, name, row_date) for i, name in enumerate(names))
    return f"""<!DOCTYPE html><html><body>
{header_block}
<table>{HEADER_ROW}{rows}</table>
</body></html>""".encode("windows-1252")


def _csrf(client) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/upload").text)
    assert match
    return match.group(1)


def _post(client, files: list[tuple[str, bytes]], follow_redirects=False):
    return client.post(
        "/upload/pcc-batch",
        data={"csrf_token": _csrf(client)},
        files=[("files", (name, content, "text/html")) for name, content in files],
        follow_redirects=follow_redirects,
    )


# ------------------------------------------------------------- parser ----

def test_service_date_comes_from_the_batch_header():
    result = parse_pcc_batch(batch_html("9/10/2026", ["Adams, Alice (PAM-1)"]))
    assert result.batch_date == datetime.date(2026, 9, 10)
    assert result.batch_date_source == "header"


def test_service_date_falls_back_to_the_rows_when_there_is_no_header():
    result = parse_pcc_batch(batch_html("9/10/2026", ["Adams, Alice (PAM-1)"], header=False))
    assert result.batch_date == datetime.date(2026, 9, 10)
    assert result.batch_date_source == "rows"


def test_header_and_row_dates_disagreeing_is_a_warning_not_a_silent_choice():
    result = parse_pcc_batch(batch_html("9/10/2026", ["Adams, Alice (PAM-1)"], row_date="9/11/2026"))
    assert result.batch_date == datetime.date(2026, 9, 10), "the header wins"
    assert any("9/11/2026" in w for w in result.warnings)


def test_a_file_with_no_date_anywhere_reports_no_date_rather_than_guessing():
    result = parse_pcc_batch(batch_html("9/10/2026", ["Adams, Alice (PAM-1)"], row_date="", header=False))
    assert result.batch_date is None
    assert result.batch_date_source == "none"


# -------------------------------------------------------------- route ----

def test_a_week_of_files_imports_each_under_its_own_date(client, db_session):
    days = ["9/8/2026", "9/9/2026", "9/10/2026", "9/11/2026", "9/12/2026"]
    resp = _post(client, [(f"batch_{d.replace('/', '')}.html", batch_html(d, ["Adams, Alice (PAM-1)"]))
                          for d in days])
    assert resp.status_code == 200, resp.text

    rows = db_session.scalars(select(BillingRecord)).all()
    assert {r.date for r in rows} == {datetime.date(2026, 9, d) for d in (8, 9, 10, 11, 12)}
    assert len(rows) == 5

    uploads = db_session.scalars(select(Upload).where(Upload.kind == UploadKind.PCC_BATCH)).all()
    assert {u.batch_date for u in uploads} == {datetime.date(2026, 9, d) for d in (8, 9, 10, 11, 12)}


def test_a_single_clean_file_goes_straight_to_its_review_screen(client, db_session):
    resp = _post(client, [("batch.html", batch_html("9/10/2026", ["Adams, Alice (PAM-1)"]))])
    assert resp.status_code == 303
    assert resp.headers["location"] == "/batches/2026-09-10/review"


def test_two_files_for_the_same_day_import_nothing(client, db_session):
    """Uploading the same day twice in one submission means the wrong
    file was picked; importing one and silently dropping the other would
    leave the reviewer unable to tell which day's data they're looking at."""
    resp = _post(client, [
        ("monday.html", batch_html("9/10/2026", ["Adams, Alice (PAM-1)"])),
        ("monday_copy.html", batch_html("9/10/2026", ["Brown, Bob (PAM-2)"])),
    ])
    assert resp.status_code == 303
    assert resp.headers["location"] == "/upload"
    assert db_session.scalars(select(BillingRecord)).all() == []
    assert db_session.scalars(select(Upload)).all() == []


def test_a_dateless_file_is_refused_while_its_siblings_still_import(client, db_session):
    resp = _post(client, [
        ("good.html", batch_html("9/10/2026", ["Adams, Alice (PAM-1)"])),
        ("undated.html", batch_html("9/11/2026", ["Brown, Bob (PAM-2)"], row_date="", header=False)),
    ])
    assert resp.status_code == 200
    assert "Not imported" in resp.text

    rows = db_session.scalars(select(BillingRecord)).all()
    assert {r.date for r in rows} == {datetime.date(2026, 9, 10)}

    # The refused file is still recorded -- it was received and stored.
    undated = db_session.scalars(
        select(Upload).where(Upload.original_filename == "undated.html")
    ).all()
    assert len(undated) == 1
    assert undated[0].batch_date is None
    assert "No service date" in (undated[0].parse_warnings or "")


def test_reuploading_a_day_replaces_it(client, db_session):
    _post(client, [("mon.html", batch_html("9/10/2026", ["Adams, Alice (PAM-1)", "Brown, Bob (PAM-2)"]))])
    _post(client, [("mon_fixed.html", batch_html("9/10/2026", ["Adams, Alice (PAM-1)"]))])

    rows = db_session.scalars(select(BillingRecord).where(
        BillingRecord.date == datetime.date(2026, 9, 10)
    )).all()
    assert len(rows) == 1, "the second upload replaces the day rather than adding to it"


def test_uploading_nothing_is_rejected(client, db_session):
    resp = client.post(
        "/upload/pcc-batch",
        data={"csrf_token": _csrf(client)},
        files=[("files", ("", b"", "text/html"))],
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/upload"
    assert db_session.scalars(select(Upload)).all() == []
