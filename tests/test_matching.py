from app.matching import extract_last_name, fuzzy_find_participant, get_or_create_participant, split_external_id


def test_split_external_id():
    name, ext_id = split_external_id("Arocho, Leonidas (PAM-6)")
    assert name == "Arocho, Leonidas"
    assert ext_id == "PAM-6"


def test_split_external_id_absent():
    name, ext_id = split_external_id("Biederman, Georgie")
    assert name == "Biederman, Georgie"
    assert ext_id is None


def test_get_or_create_matches_by_external_id_despite_name_spelling_diff(db_session):
    """Real data: the attendance sheet may spell a first name differently
    ("Georgie" vs "Georgiana") or omit the ID entirely for one source but
    not another. When an ID IS present and matches, it must win over any
    name-based logic."""
    db = db_session
    p1, created1, warn1 = get_or_create_participant(db, "Biederman, Georgiana (PAM-209)")
    assert created1
    assert warn1 is None
    p2, created2, warn2 = get_or_create_participant(db, "Biederman, Georgie (PAM-209)")
    assert not created2
    assert warn2 is None
    assert p2.id == p1.id


def test_get_or_create_enriches_bare_last_name_with_later_id(db_session):
    """A participant first seen without an ID (e.g. Weekly Attendance
    row lacking the "(PAM-xxx)" suffix) should get the ID attached once
    a later source supplies it for the same last name, rather than
    creating a second identity."""
    db = db_session
    bare, _, _ = get_or_create_participant(db, "Del Mage, Rosemary")
    assert bare.external_id is None
    enriched, created, warn = get_or_create_participant(db, "Del Mage, Rosemary (PAM-15)")
    assert not created
    assert warn is None
    assert enriched.id == bare.id
    assert enriched.external_id == "PAM-15"


def test_get_or_create_keeps_distinct_identities_when_ids_differ(db_session):
    """Real roster data: two different participants can share a last
    name (e.g. two "Goldstein"s). When each carries its OWN distinct
    external ID, they must be kept as separate identities, not merged --
    merging them previously crashed the whole upload with a UNIQUE
    constraint violation when both were later given attendance records
    for the same dates."""
    db = db_session
    judith, created1, warn1 = get_or_create_participant(db, "Goldstein, Judith (PAM-20)")
    assert created1
    assert warn1 is None
    marvin, created2, warn2 = get_or_create_participant(db, "Goldstein, Marvin (PAM-215)")
    assert created2  # a distinct participant, not a merge
    assert warn2 is None
    assert marvin.id != judith.id
    assert marvin.external_id == "PAM-215"
    assert judith.external_id == "PAM-20"

    # Re-uploading the same two rows again must re-match each to their
    # own existing record, not create a third.
    judith2, created3, _ = get_or_create_participant(db, "Goldstein, Judith (PAM-20)")
    assert not created3
    assert judith2.id == judith.id


def test_get_or_create_keeps_separate_records_for_ambiguous_no_id_collision(db_session):
    """Two different people sharing a last name with NO ID on either
    side (e.g. two "Murphy"s in a real roster) can't be told apart from
    the source data alone -- but merging them would silently corrupt
    whichever one's attendance got overwritten second. They must be
    kept as separate records (each keeping its own attendance data
    correctly attributed), with a warning for a human to confirm."""
    db = db_session
    ellen, created1, warn1 = get_or_create_participant(db, "Murphy, Ellen")
    assert created1
    assert warn1 is None
    henry, created2, warn2 = get_or_create_participant(db, "Murphy, Henry")
    assert created2
    assert henry.id != ellen.id
    assert warn2 is not None
    assert "Murphy" in warn2


def test_get_or_create_tolerates_nickname_style_first_names(db_session):
    """"Fran" (Rate Master) and "Frances" (Attendance, with an ID) are
    almost certainly the same person recorded two different ways -- a
    prefix match should merge/enrich them rather than splitting."""
    db = db_session
    fran, created1, warn1 = get_or_create_participant(db, "Johnson, Fran")
    assert created1
    assert warn1 is None
    frances, created2, warn2 = get_or_create_participant(db, "Johnson, Frances (PAM-103)")
    assert not created2
    assert frances.id == fran.id
    assert frances.external_id == "PAM-103"
    assert warn2 is None


def test_get_or_create_splits_incompatible_name_from_bare_existing(db_session):
    """Real scenario: Rate Master has a bare 'Johnson, Fran' (no ID).
    Attendance later has a genuinely different person, 'Johnson,
    Alexander (PAM-67)'. Alexander must NOT be merged into Fran's bare
    record just because they share a surname and Fran has no ID yet."""
    db = db_session
    fran, _, _ = get_or_create_participant(db, "Johnson, Fran")
    alexander, created, warn = get_or_create_participant(db, "Johnson, Alexander (PAM-67)")
    assert created
    assert alexander.id != fran.id
    assert alexander.external_id == "PAM-67"
    assert warn is not None

    # A later, unrelated row for the SAME ambiguous bare name (e.g. a
    # second upload of the same Rate Master) should re-match Fran's
    # existing bare record, not spawn a third one.
    fran_again, created2, _ = get_or_create_participant(db, "Johnson, Fran")
    assert not created2
    assert fran_again.id == fran.id


def test_get_or_create_tolerates_suffix_nickname(db_session):
    """"Fred" (a real Rate Master entry) is a suffix-style nickname of
    "Alfred" (real Attendance entry, same participant)."""
    db = db_session
    alfred, _, _ = get_or_create_participant(db, "Galella, Alfred (PAM-105)")
    fred, created, warn = get_or_create_participant(db, "Galella, Fred")
    assert not created
    assert fred.id == alfred.id
    assert warn is None


def test_get_or_create_tolerates_spelling_variant(db_session):
    """"Mohamed" vs "Mohammed" -- a transliteration spelling variance
    seen in real Attendance vs. Rate Master data for the same person."""
    db = db_session
    a, _, _ = get_or_create_participant(db, "Ibrahim, Mohammed (PAM-137)")
    b, created, warn = get_or_create_participant(db, "Ibrahim, Mohamed")
    assert not created
    assert b.id == a.id
    assert warn is None


def test_fuzzy_find_by_external_id_exact(db_session):
    db = db_session
    participant, _, _ = get_or_create_participant(db, "Arocho, Leonidas (PAM-6)")
    db.commit()
    match = fuzzy_find_participant(db, "Arocho, Leonidas (PAM-6)")
    assert match.participant.id == participant.id
    assert match.score == 100.0


def test_fuzzy_find_falls_back_to_last_name_when_no_id(db_session):
    db = db_session
    participant, _, _ = get_or_create_participant(db, "Frost")
    db.commit()
    match = fuzzy_find_participant(db, "Frost, Fiona")
    assert match.participant.id == participant.id


def test_extract_last_name_handles_comma_and_bare():
    assert extract_last_name("Smith, John") == "smith"
    assert extract_last_name("Smith") == "smith"
