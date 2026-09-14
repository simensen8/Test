"""Participant identity resolution across the three source documents.

Real exports carry the facility's own internal participant ID inline in
the name text, e.g. "Arocho, Leonidas (PAM-6)" -- on the Weekly
Attendance sheet, and on the PCC batch. That ID is a far more reliable
join key than any name matching, so it takes priority whenever present.
Not every source/row carries it though (the Rate Master's current sheet
has no ID column at all, and a handful of Weekly Attendance rows lack
the "(PAM-xxx)" suffix), so name-based matching remains the fallback --
keyed on normalized LAST NAME, since that is the weakest source
(attendance) can reliably support. Two different participants who share
a last name will be merged into one identity by this heuristic; that is
an inherent limitation of the source data, not something a name-matching
algorithm can resolve on its own, so upload screens surface a warning
whenever a name is created/matched ambiguously so a human can catch and
correct it (e.g. by editing the participant's display name to
disambiguate).
"""
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto_types import blind_index
from app.models import Participant

FUZZY_MATCH_THRESHOLD = 87

EXTERNAL_ID_RE = re.compile(r"\(\s*([A-Za-z]{2,6}-\d+)\s*\)")


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def split_external_id(raw_name: str) -> tuple[str, str | None]:
    """Split "Lastname, Firstname (PAM-6)" into ("Lastname, Firstname",
    "PAM-6"). Returns the name unchanged and None if no ID is present."""
    match = EXTERNAL_ID_RE.search(raw_name)
    if not match:
        return raw_name.strip(), None
    name = (raw_name[: match.start()] + raw_name[match.end():]).strip()
    return name, match.group(1).upper()


def extract_last_name(raw_name: str) -> str:
    raw_name = raw_name.strip()
    if "," in raw_name:
        return normalize(raw_name.split(",")[0])
    return normalize(raw_name)


def _first_name_token(clean_name: str) -> str:
    rest = clean_name.split(",", 1)[1] if "," in clean_name else ""
    tokens = rest.strip().split()
    return tokens[0].lower() if tokens else ""


def _first_names_compatible(a: str, b: str) -> bool:
    """True if two first-name tokens plausibly refer to the same person:
    either is blank (a bare last-name-only record being enriched), they
    match exactly, one is a meaningful prefix or suffix of the other
    (nickname tolerance -- "Fran"/"Frances", "Fred"/"Alfred" all appear
    for the same real person across Rate Master vs. Attendance data), or
    they're a near-identical spelling (transliteration variance, e.g.
    "Mohamed"/"Mohammed"). Deliberately conservative: arbitrary
    traditional nicknames unrelated by spelling (Joe/Joseph, Bob/Robert)
    aren't caught -- those still split into two records with a warning
    rather than risk merging two actually-different people."""
    if not a or not b:
        return True
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)):
        return True
    if len(a) >= 4 and len(b) >= 4 and fuzz.ratio(a, b) >= 85:
        return True
    return False


def get_or_create_participant(db: Session, raw_name: str) -> tuple[Participant, bool, str | None]:
    """Deterministic (non-fuzzy) match/create, used for the authoritative
    roster sources (weekly attendance, rate master). Returns
    (participant, created, warning).

    Matching priority: exact external ID (e.g. "PAM-6") match, then
    exact last-name match IF the first names look compatible (same,
    blank, or a nickname-style prefix of each other), else a new,
    separate record.

    Real rosters do have distinct participants sharing a last name
    (e.g. two "Goldstein"s, or a "Johnson, Alexander" and a "Johnson,
    Fran"). Silently merging those into one identity by last name alone
    would corrupt whichever one's attendance/billing data got
    overwritten second -- worse than the alternative. So an incompatible
    same-surname row always gets its OWN record (disambiguated by
    external ID when available, otherwise by the full name text), never
    merged into an existing, differently-named one. A warning is
    returned either way so a human can confirm the split was right (or
    merge them back via the participant's display name if it turns out
    to be the same person recorded two different ways)."""
    clean_name, external_id = split_external_id(raw_name)
    last_name = extract_last_name(clean_name)
    plain_idx = blind_index(last_name)
    new_first = _first_name_token(clean_name)

    if external_id:
        existing = db.scalar(select(Participant).where(Participant.external_id == external_id))
        if existing:
            if len(clean_name) > len(existing.full_name.strip()):
                existing.full_name = clean_name
            return existing, False, None

    # A prior ambiguous, ID-less occurrence of this exact name would have
    # been filed under a composite index -- check there before assuming
    # this is a brand new person.
    composite_idx = blind_index(f"{last_name}|{normalize(clean_name)}")
    existing = db.scalar(select(Participant).where(Participant.name_index == composite_idx))
    if existing:
        if external_id and not existing.external_id:
            existing.external_id = external_id
        return existing, False, None

    existing = db.scalar(select(Participant).where(Participant.name_index == plain_idx))
    if existing is None:
        participant = Participant(full_name=clean_name, name_index=plain_idx, external_id=external_id)
        db.add(participant)
        db.flush()
        return participant, True, None

    existing_first = _first_name_token(existing.full_name)
    same_id_or_unresolvable_conflict = existing.external_id and external_id and existing.external_id != external_id
    if same_id_or_unresolvable_conflict or not _first_names_compatible(existing_first, new_first):
        disambiguator = external_id or normalize(clean_name)
        participant = Participant(
            full_name=clean_name, name_index=blind_index(f"{last_name}|{disambiguator}"), external_id=external_id,
        )
        db.add(participant)
        db.flush()
        warning = None
        if not external_id or not existing.external_id:
            warning = (
                f"'{clean_name}' and '{existing.full_name}' both share the last name "
                f"'{last_name.title()}' but don't look like the same person -- kept as separate "
                f"participants; please confirm in the roster (merge them by editing a display "
                f"name if they're actually one person)."
            )
        return participant, True, warning

    if external_id and not existing.external_id:
        existing.external_id = external_id
    if len(clean_name) > len(existing.full_name.strip()):
        existing.full_name = clean_name
    return existing, False, None


@dataclass
class FuzzyMatchResult:
    participant: Participant | None
    score: float
    candidate_name: str | None


def fuzzy_find_participant(db: Session, raw_name: str) -> FuzzyMatchResult:
    """Best-effort match for a name appearing on a PCC batch against the
    known participant roster. Never creates a new participant -- an
    unmatched billing row should surface as an exception for human
    review rather than silently mint a new identity from a possible OCR
    or spelling variant."""
    clean_name, external_id = split_external_id(raw_name)

    if external_id:
        exact = db.scalar(select(Participant).where(Participant.external_id == external_id))
        if exact:
            return FuzzyMatchResult(participant=exact, score=100.0, candidate_name=exact.full_name)

    last_name = extract_last_name(clean_name)
    idx = blind_index(last_name)
    exact = db.scalar(select(Participant).where(Participant.name_index == idx))
    if exact:
        return FuzzyMatchResult(participant=exact, score=100.0, candidate_name=exact.full_name)

    best: Participant | None = None
    best_score = 0.0
    for participant in db.scalars(select(Participant)):
        score = fuzz.token_sort_ratio(last_name, extract_last_name(participant.full_name))
        if score > best_score:
            best_score, best = score, participant

    if best and best_score >= FUZZY_MATCH_THRESHOLD:
        return FuzzyMatchResult(participant=best, score=best_score, candidate_name=best.full_name)
    return FuzzyMatchResult(participant=None, score=best_score, candidate_name=best.full_name if best else None)
