"""Canonical payer-source categories, and normalization from the very
different raw spellings the Rate Master and PCC batch export use.

This exists because comparing raw strings between sources doesn't work:
the Rate Master's "Payor" column has free text like "Private Pay
Discount $53.00 Daily" or "Medacaid" (typo), while a PCC batch identifies
the payer via a short internal charge code like "ADC MPNT" or "ADC MCD
MW" and a description like "Adult Day Care Medicaid / Medicaid Waiver".
Critically, PCC's "Payer Code" column alone is NOT enough to distinguish
Private Pay from a Parker Grant day -- both are billed under the same
"PP-ADC" payer code in real exports; only the charge code / description
("ADC P GRANT" / "Adult Day Care Parker Grant") tells them apart. So
every payer-ish string from either source is normalized through
`categorize_payer` before being compared or stored as a RateRule/
BillingRecord payer_source, so "Medicaid" and "ADC MCD MW" land on the
same canonical value.
"""
import re

PRIVATE_PAY = "Private Pay"
MEDICAID = "Medicaid"
MEDICARE = "Medicare"
VA = "VA"
PARKER_GRANT = "Parker Grant"
ALZHEIMER_GRANT = "Alzheimer Grant"
TITLE_III = "Title III"
HOSPICE = "Hospice"

CANONICAL_CATEGORIES = [
    PRIVATE_PAY, MEDICAID, MEDICARE, VA, PARKER_GRANT, ALZHEIMER_GRANT, TITLE_III, HOSPICE,
]

# Priority-ordered (most specific first) keyword -> category, used only
# as a tie-breaker when two keywords start at the exact same text
# position (effectively never). Matched as case-insensitive
# substrings/word-boundaries against the raw text. Typo variants seen in
# real Rate Master data are included deliberately (e.g. "medacaid",
# "medaciad", "titlelll" -- the last from an OCR/typo of "Title III"
# with lowercase L's instead of I's).
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"parker\s*grant|\bp\.?\s*grant\b|adc p grant", re.I), PARKER_GRANT),
    (re.compile(r"alzheimer|\balz\b", re.I), ALZHEIMER_GRANT),
    (re.compile(r"hospice", re.I), HOSPICE),
    (re.compile(r"title\s*(iii|lll|3)\b|\bt3\b", re.I), TITLE_III),
    (re.compile(r"medicaid|medacaid|medaciad|\bmcd\b|\bma-adc\b", re.I), MEDICAID),
    (re.compile(r"medicare", re.I), MEDICARE),
    (re.compile(r"veteran|\bva\b|\bva-adc\b", re.I), VA),
    (re.compile(r"private\s*pay|medical prog|\bpp\b|\bpp-adc\b|\bprivate\b", re.I), PRIVATE_PAY),
]


def categorize_payer(raw_text: str | None) -> str | None:
    """Map a raw payer string/code/description from either source into a
    canonical category. Returns the title-cased original text if nothing
    matches (so it still displays sensibly) rather than guessing, and
    None for empty input.

    When text mentions more than one recognized keyword (real Rate
    Master text sometimes does, e.g. "Private Pay/Titlelll-Discount$25"
    or "Title III 2 days/ Parker Grant 2 days"), the EARLIEST-occurring
    keyword wins on the assumption that whoever wrote it named the
    primary/base payer first -- not whichever pattern happens to be
    checked first in `_RULES`."""
    if not raw_text or not raw_text.strip():
        return None
    best_pos, best_category = None, None
    for pattern, category in _RULES:
        match = pattern.search(raw_text)
        if match and (best_pos is None or match.start() < best_pos):
            best_pos, best_category = match.start(), category
    if best_category is not None:
        return best_category
    return " ".join(w.capitalize() for w in raw_text.strip().split())


def is_recognized_category(value: str | None) -> bool:
    return value in CANONICAL_CATEGORIES


def categorize_payer_from_fields(*texts: str | None) -> str | None:
    """Try each text in priority order, keeping the first that maps to a
    *recognized* canonical category. `categorize_payer` alone can't be
    `or`-chained for this: it never returns None for non-empty input (it
    falls back to title-casing unrecognized text as a still-readable
    default), so a plain `categorize_payer(a) or categorize_payer(b)`
    would always stop at `a` even when `a` is an unrecognized code like
    a raw charge code ("ADC MPNT") that only `b` (its description) would
    actually identify. Falls back to the first non-empty text's
    best-effort categorization if nothing recognized is found."""
    fallback = None
    for text in texts:
        category = categorize_payer(text)
        if category is None:
            continue
        if is_recognized_category(category):
            return category
        if fallback is None:
            fallback = category
    return fallback
