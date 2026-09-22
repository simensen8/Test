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


def _first_name_similarity(a: str, b: str) -> float:
    """How alike two first-name tokens are, used only to rank candidates
    that are already considered compatible."""
    if not a or not b:
        return 50.0  # a record with no first name is a weak but real candidate
    if a == b:
        return 100.0
    return float(fuzz.ratio(a, b))


def find_candidates_by_last_name(db: Session, last_name: str) -> list[Participant]:
    """Every participant sharing this surname.

    Same-surname people are stored under different `name_index` values
    (the second one gets a composite index so it can't collide with the
    first), which makes them invisible to a lookup by name_index alone.
    `last_name_index` is the non-unique index that keeps the whole family
    findable -- without it, a name resolves against whichever record
    happens to hold the plain slot, which is how one sister's billing
    ended up on the other's record.
    """
    idx = blind_index(last_name)
    candidates = list(db.scalars(select(Participant).where(Participant.last_name_index == idx)))
    if candidates:
        return candidates
    # Records created before last_name_index existed (or by a caller that
    # didn't set it) are still reachable by the plain index.
    legacy = db.scalar(select(Participant).where(Participant.name_index == idx))
    return [legacy] if legacy else []


def _best_compatible(candidates: list[Participant], first_name: str) -> Participant | None:
    """The candidate whose first name best fits, or None if none fits."""
    compatible = [
        (p, _first_name_similarity(_first_name_token(p.full_name), first_name))
        for p in candidates
        if _first_names_compatible(_first_name_token(p.full_name), first_name)
    ]
    if not compatible:
        return None
    return max(compatible, key=lambda pair: pair[1])[0]


def get_or_create_participant(db: Session, raw_name: str) -> tuple[Participant, bool, str | None]:
    """Deterministic (non-fuzzy) match/create, used for the authoritative
    roster sources (weekly attendance, rate master). Returns
    (participant, created, warning).

    Matching priority: exact external ID (e.g. "PAM-6"), then the
    same-surname participant whose first name is compatible (same,
    blank, or a nickname-style variant), else a new, separate record.

    Real rosters do have distinct participants sharing a last name (two
    "Goldstein"s, or a "Johnson, Alexander" and a "Johnson, Fran").
    Silently merging those into one identity would corrupt whichever
    one's attendance or billing was written second, so an incompatible
    same-surname row always gets its own record -- with a warning, so a
    human can confirm the split was right.
    """
    clean_name, external_id = split_external_id(raw_name)
    last_name = extract_last_name(clean_name)
    plain_idx = blind_index(last_name)
    new_first = _first_name_token(clean_name)

    def enrich(participant: Participant) -> Participant:
        if external_id and not participant.external_id:
            participant.external_id = external_id
        if len(clean_name) > len(participant.full_name.strip()):
            participant.full_name = clean_name
        if not participant.last_name_index:
            participant.last_name_index = plain_idx
        return participant

    if external_id:
        existing = db.scalar(select(Participant).where(Participant.external_id == external_id))
        if existing:
            return enrich(existing), False, None

    candidates = find_candidates_by_last_name(db, last_name)
    # A candidate carrying a different facility ID is definitively
    # someone else, however alike the names look.
    eligible = [c for c in candidates if not (external_id and c.external_id and c.external_id != external_id)]

    match = _best_compatible(eligible, new_first)
    if match is not None:
        return enrich(match), False, None

    # Nobody fits: a new person. The plain index is the surname slot --
    # the first arrival takes it, later same-surname people get a
    # composite one so the unique constraint doesn't collide them.
    taken = db.scalar(select(Participant).where(Participant.name_index == plain_idx))
    name_index = plain_idx if taken is None else blind_index(
        f"{last_name}|{external_id or normalize(clean_name)}"
    )
    participant = Participant(
        full_name=clean_name, name_index=name_index,
        external_id=external_id, last_name_index=plain_idx,
    )
    db.add(participant)
    db.flush()

    # A split backed by two different facility IDs is a fact, not a
    # judgement call, so it needs no confirmation from anyone.
    ids_settle_it = bool(external_id) and all(c.external_id for c in candidates)
    warning = None
    if candidates and not ids_settle_it:
        others = ", ".join(f"'{c.full_name}'" for c in candidates[:3])
        warning = (
            f"'{clean_name}' shares the last name '{last_name.title()}' with {others} but doesn't "
            f"look like the same person, so they were kept as separate participants. If they are "
            f"one person recorded two ways, merge them on the Roster (their profile lists everyone "
            f"sharing the surname)."
        )
    return participant, True, warning


@dataclass
class FuzzyMatchResult:
    participant: Participant | None
    score: float
    candidate_name: str | None


def fuzzy_find_participant(db: Session, raw_name: str) -> FuzzyMatchResult:
    """Best-effort match for a name on a PCC batch against the known
    roster. Never creates a participant -- an unmatched billing row
    should surface as an exception for a human rather than silently mint
    a new identity from a spelling variant.

    It resolves the same way `get_or_create_participant` does, and that
    matters: when it didn't, a batch row for one of two same-surname
    participants attached to whichever of them held the plain surname
    slot. One sister then read as billed-but-absent and the other as
    attended-but-unbilled -- two exceptions, on a day that was correct.
    """
    clean_name, external_id = split_external_id(raw_name)

    if external_id:
        exact = db.scalar(select(Participant).where(Participant.external_id == external_id))
        if exact:
            return FuzzyMatchResult(participant=exact, score=100.0, candidate_name=exact.full_name)

    last_name = extract_last_name(clean_name)
    first_name = _first_name_token(clean_name)

    candidates = find_candidates_by_last_name(db, last_name)
    if candidates:
        match = _best_compatible(candidates, first_name)
        if match is not None:
            return FuzzyMatchResult(participant=match, score=100.0, candidate_name=match.full_name)
        # Same surname, no first name that fits: near-misses are named
        # in the result so the review screen can show what it nearly
        # matched, but the row stays unmatched for a human to settle.
        nearest = max(
            candidates,
            key=lambda c: _first_name_similarity(_first_name_token(c.full_name), first_name),
        )
        return FuzzyMatchResult(participant=None, score=0.0, candidate_name=nearest.full_name)

    best: Participant | None = None
    best_score = 0.0
    for participant in db.scalars(select(Participant)):
        score = fuzz.token_sort_ratio(last_name, extract_last_name(participant.full_name))
        if score > best_score:
            best_score, best = score, participant

    if best and best_score >= FUZZY_MATCH_THRESHOLD and _first_names_compatible(
        _first_name_token(best.full_name), first_name
    ):
        return FuzzyMatchResult(participant=best, score=best_score, candidate_name=best.full_name)
    return FuzzyMatchResult(participant=None, score=best_score, candidate_name=best.full_name if best else None)
