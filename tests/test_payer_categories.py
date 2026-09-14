from app.payer_categories import categorize_payer, categorize_payer_from_fields


def test_categorize_common_variants():
    cases = {
        "Medicaid": "Medicaid",
        "Medacaid": "Medicaid",  # real typo seen in source data
        "Medaciad": "Medicaid",  # real typo seen in source data
        "ADC MCD MW": "Medicaid",
        "Adult Day Care Medicaid / Medicaid Waiver": "Medicaid",
        "MA-ADC": "Medicaid",
        "Private Pay": "Private Pay",
        "Private Pay: Sliding Scale $26.25": "Private Pay",
        "ADC Medical Prog w/trans.": "Private Pay",
        "VA": "VA",
        "Adult Day Care Veterans": "VA",
        "VA-ADC": "VA",
        "Parker Grant": "Parker Grant",
        "Adult Day Care Parker Grant": "Parker Grant",
        "ADC P GRANT": "Parker Grant",
        "Alzheimer Grant #7-63.26": "Alzheimer Grant",
        "ADC ALZ's Grant Rate $63.26": "Alzheimer Grant",
        "Title III 2 days/ Parker Grant 2 days": "Title III",  # earliest-mentioned keyword wins
        "Adult Day Care Title III": "Title III",
        "T3-ADC": "Title III",
        "Hospice": "Hospice",
    }
    for raw, expected in cases.items():
        assert categorize_payer(raw) == expected, f"{raw!r} -> expected {expected}, got {categorize_payer(raw)}"


def test_categorize_payer_conflates_private_pay_and_grant_on_payer_code_alone():
    """Documents the real-world gotcha this app must NOT fall into: PCC's
    bare Payer Code ("PP-ADC") is the same for plain Private Pay and a
    Parker Grant day. Categorizing off that code alone would be wrong;
    the parser must use Charge Code/Description instead (tested in
    test_parsers.py)."""
    assert categorize_payer("PP-ADC") == "Private Pay"


def test_categorize_unrecognized_returns_titlecased_original():
    assert categorize_payer("Some Future Payer") == "Some Future Payer"


def test_categorize_empty():
    assert categorize_payer("") is None
    assert categorize_payer(None) is None


def test_categorize_from_fields_falls_through_unrecognized_charge_code():
    """A bare PCC charge code like "ADC MPNT" isn't recognizable on its
    own -- only its Description ("ADC Medical Prog. W/o Transp")
    identifies it as Private Pay. categorize_payer_from_fields must fall
    through to later fields rather than stopping at the first
    unrecognized (but non-None) result."""
    result = categorize_payer_from_fields("ADC MPNT", "ADC Medical Prog. W/o Transp", "PP-ADC")
    assert result == "Private Pay"


def test_categorize_from_fields_prefers_charge_code_for_grant_over_payer_code():
    """The real gotcha: Payer Code is "PP-ADC" for BOTH plain private pay
    and Parker Grant days. Charge Code ("ADC P GRANT") must win."""
    result = categorize_payer_from_fields("ADC P GRANT", "Adult Day Care Parker Grant", "PP-ADC")
    assert result == "Parker Grant"


def test_categorize_from_fields_all_unrecognized_falls_back_to_first():
    result = categorize_payer_from_fields("Unknown Code", "Unknown Description")
    assert result == "Unknown Code"
