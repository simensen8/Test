from app.parsers.pcc_batch import parse_pcc_batch

# A trimmed-down snippet of the real PCC "Batch Detail Report" HTML
# structure (table with header row containing "Participant Name (ID)"
# plus data rows that start with a row number, and non-data rows -- a
# subtotal row and a "Revised By" line -- that must NOT be parsed as
# participants).
SAMPLE_HTML = """
<!DOCTYPE html>
<html><body>
<table border="0" cellspacing="1" cellpadding="2" width="940">
  <tbody><tr valign="bottom">
    <td class="repHeadSmaller">#</td>
    <td class="repHeadSmaller">Participant Name (ID)</td>
    <td class="repHeadSmaller" align="right">Eff.&nbsp;Date</td>
    <td class="repHeadSmaller" align="center">Charge<br>Code</td>
    <td class="repHeadSmaller" align="center">Rev.<br>Code</td>
    <td class="repHeadSmaller" align="center">HCPCS<br>Code</td>
    <td class="repHeadSmaller">Description</td>
    <td class="repHeadSmaller">Payer<br>Code</td>
    <td class="repHeadSmaller" align="center">Trans.&nbsp;Type</td>
    <td class="repHeadSmaller">Units</td>
    <td class="repHeadSmaller" align="center">Unit<br>Amount</td>
    <td class="repHeadSmaller" align="center">Total</td>
    <td class="repHeadSmaller" align="center">GL Acct.</td>
    <td class="repHeadSmaller" align="center">Days Acct.</td>
  </tr>
     <tr style="vertical-align:top">
        <td class="smallData">1</td>
        <td class="smallData">Arocho, Leonidas (PAM-6)</td>
        <td class="smallData" align="right">9/10/2026</td>
        <td class="smallData" align="right">ADC MCD MW</td>
        <td class="smallData" align="right"></td>
        <td class="smallData" align="right">S5102</td>
        <td class="smallData">Adult Day Care Medicaid / Medicaid Waiver</td>
        <td class="smallData">MA-ADC</td>
        <td class="smallData" align="center">A</td>
        <td class="smallData" align="right">1</td>
        <td class="smallData" align="right">$95.96</td>
        <td class="smallData" align="right">$95.96</td>
        <td class="smallData" align="right">5000-00-310321-230</td>
        <td class="smallData" align="right"></td>
      </tr>
     <tr style="vertical-align:top">
    <td colspan="5">&nbsp;</td><td class="smallData" colspan="3"></td><td class="repHeadSmall" align="right">1</td><td class="repHeadSmall" align="right"></td><td class="repHeadSmall" align="right">$95.96</td></tr>
    <tr><td class="smallData" colspan="99">Revised By _system_ on 9/11/2026 18:11:32</td></tr>
        <tr><td class="smallData">39</td>
        <td class="smallData">Biederman, Georgiana (PAM-209)</td>
        <td class="smallData" align="right">9/10/2026</td>
        <td class="smallData" align="right">ADC P GRANT</td>
        <td class="smallData" align="right"></td>
        <td class="smallData" align="right"></td>
        <td class="smallData">Adult Day Care Parker Grant</td>
        <td class="smallData">PP-ADC</td>
        <td class="smallData" align="center">A</td>
        <td class="smallData" align="right">1</td>
        <td class="smallData" align="right">$95.00</td>
        <td class="smallData" align="right">$95.00</td>
        <td class="smallData" align="right">5000-00-310321-230</td>
        <td class="smallData" align="right"></td>
      </tr>
        <tr><td class="smallData">13</td>
        <td class="smallData">Berkin, Harvey (PAM-90)</td>
        <td class="smallData" align="right">9/10/2026</td>
        <td class="smallData" align="right">ADC MPNT</td>
        <td class="smallData" align="right"></td>
        <td class="smallData" align="right"></td>
        <td class="smallData">ADC Medical Prog. W/o Transp</td>
        <td class="smallData">PP-ADC</td>
        <td class="smallData" align="center">A</td>
        <td class="smallData" align="right">1</td>
        <td class="smallData" align="right">$95.00</td>
        <td class="smallData" align="right">$95.00</td>
        <td class="smallData" align="right">5000-00-310321-230</td>
        <td class="smallData" align="right"></td>
      </tr>
</tbody></table>
</body></html>
"""


def test_parse_real_pcc_html_structure():
    result = parse_pcc_batch(SAMPLE_HTML.encode("windows-1252"))
    assert result.extraction_method == "html-table"
    assert len(result.rows) == 3  # subtotal / "Revised By" rows must be excluded

    by_name = {r.raw_name: r for r in result.rows}
    assert "Arocho, Leonidas (PAM-6)" in by_name
    assert by_name["Arocho, Leonidas (PAM-6)"].payer_source == "Medicaid"
    assert by_name["Arocho, Leonidas (PAM-6)"].amount == 95.96

    # The critical case: Parker Grant and plain Private Pay share the
    # same Payer Code ("PP-ADC") in real PCC exports -- only Charge
    # Code/Description tell them apart, and the parser must get this right.
    assert by_name["Biederman, Georgiana (PAM-209)"].payer_source == "Parker Grant"
    assert by_name["Berkin, Harvey (PAM-90)"].payer_source == "Private Pay"


def test_parse_real_pcc_html_row_dates():
    result = parse_pcc_batch(SAMPLE_HTML.encode("windows-1252"))
    assert all(r.row_date == "9/10/2026" for r in result.rows)
