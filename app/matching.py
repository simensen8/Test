"""Participant identity resolution across the three source documents.

The Weekly Attendance sheet identifies people by last name only. PCC
batches and the Rate Master may carry a fuller "Last, First" name. To
keep matching consistent across all three sources -- the guarantee the
weakest source (attendance) can actually support -- canonical participant
identity is keyed on normalized LAST NAME. Two different participants who
happen to share a last name will be merged into one identity by this
heuristic; that is an inherent limitation of the source data, not
something a name-matching algorithm can resolve on its own, so upload
screens surface a warning whenever a name is created/matched ambiguously
so a human can catch and correct it (e.g. by editing the participant's
display name to disambiguate).
"""
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto_types import blind_index
from app.models import Participant

FUZZY_MATCH_THRESHOLD = 87


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def extract_last_name(raw_name: str) -> str:
    raw_name = raw_name.strip()
    if "," in raw_name:
        return normalize(raw_name.split(",")[0])
    return normalize(raw_name)


def get_or_create_participant(db: Session, raw_name: str) -> tuple[Participant, bool]:
    """Deterministic (non-fuzzy) match/create, used for the authoritative
    roster sources (weekly attendance, rate master). Returns
    (participant, created)."""
    last_name = extract_last_name(raw_name)
    idx = blind_index(last_name)
    existing = db.scalar(select(Participant).where(Participant.name_index == idx))
    if existing:
        # Enrich display name if this occurrence carries more detail
        # (e.g. "Smith, John" arriving after an attendance-only "Smith").
        if len(raw_name.strip()) > len(existing.full_name.strip()):
            existing.full_name = raw_name.strip()
        return existing, False

    participant = Participant(full_name=raw_name.strip(), name_index=idx)
    db.add(participant)
    db.flush()
    return participant, True


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
    last_name = extract_last_name(raw_name)
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
