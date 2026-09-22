"""Photos on a profile: consent first, encrypted at rest, metadata gone."""
import io

from PIL import Image
from sqlalchemy import select

from app.config import UPLOAD_DIR
from app.matching import get_or_create_participant
from app.models import AuditLog, Participant


def _csrf(client, path):
    import re
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match
    return match.group(1)


def _image_bytes(size=(1200, 900), colour=(180, 40, 40), fmt="JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format=fmt)
    return out.getvalue()


def _upload(client, participant, content=None, consent="yes", filename="portrait.jpg",
            content_type="image/jpeg"):
    return client.post(
        f"/participants/{participant.id}/photo",
        data={"csrf_token": _csrf(client, f"/participants/{participant.id}"), "consent": consent},
        files={"photo": (filename, content if content is not None else _image_bytes(), content_type)},
        follow_redirects=False,
    )


def test_a_photo_is_stored_and_served_back(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    assert _upload(client, person).status_code == 303

    db_session.expire_all()
    refreshed = db_session.get(Participant, person.id)
    assert refreshed.photo_path
    assert refreshed.photo_consent_at is not None
    assert refreshed.photo_consent_by_id is not None

    served = client.get(f"/participants/{person.id}/photo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(served.content)).size == (600, 450), "downscaled to a sensible size"
    assert served.headers["cache-control"] == "no-store"


def test_the_file_on_disk_is_encrypted(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()
    _upload(client, person)

    db_session.expire_all()
    stored = (UPLOAD_DIR / db_session.get(Participant, person.id).photo_path).read_bytes()
    assert not stored.startswith(b"\xff\xd8"), "a readable JPEG must not be sitting on disk"
    assert b"JFIF" not in stored


def test_a_photo_without_consent_is_refused(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    _upload(client, person, consent="")

    db_session.expire_all()
    assert db_session.get(Participant, person.id).photo_path is None


def test_something_that_is_not_an_image_is_refused(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    _upload(client, person, content=b"MZ\x90\x00 this is not a photo", filename="payload.jpg")

    db_session.expire_all()
    assert db_session.get(Participant, person.id).photo_path is None


def test_location_metadata_does_not_survive_the_upload(client, db_session):
    """A phone photo can carry the coordinates it was taken at. Storing
    that against a health record is a needless disclosure, and
    re-encoding the pixels drops it."""
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    original = Image.new("RGB", (800, 600), (10, 90, 160))
    out = io.BytesIO()
    exif = original.getexif()
    exif[0x9286] = "taken at the centre"   # UserComment
    exif[0x010F] = "SecretPhoneCo"         # Make
    original.save(out, format="JPEG", exif=exif)

    _upload(client, person, content=out.getvalue())

    served = client.get(f"/participants/{person.id}/photo")
    stored_exif = Image.open(io.BytesIO(served.content)).getexif()
    assert dict(stored_exif) == {}, "no metadata should come back out"
    assert b"SecretPhoneCo" not in served.content


def test_replacing_a_photo_removes_the_old_file(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    _upload(client, person)
    db_session.expire_all()
    first_path = db_session.get(Participant, person.id).photo_path

    _upload(client, person, content=_image_bytes(colour=(20, 120, 60)))
    db_session.expire_all()
    second_path = db_session.get(Participant, person.id).photo_path

    assert second_path != first_path
    assert not (UPLOAD_DIR / first_path).exists(), "the replaced photo is deleted, not orphaned"


def test_removing_a_photo_deletes_it(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()
    _upload(client, person)

    db_session.expire_all()
    path = db_session.get(Participant, person.id).photo_path

    client.post(f"/participants/{person.id}/photo/remove", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
    }, follow_redirects=False)

    db_session.expire_all()
    refreshed = db_session.get(Participant, person.id)
    assert refreshed.photo_path is None
    assert refreshed.photo_consent_at is None
    assert not (UPLOAD_DIR / path).exists()
    assert client.get(f"/participants/{person.id}/photo").status_code == 404


def test_a_photo_is_not_served_to_a_stranger(client, db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()
    _upload(client, person)

    signed_out = TestClient(app)
    resp = signed_out.get(f"/participants/{person.id}/photo", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_adding_and_removing_a_photo_is_audited(client, db_session):
    person, _, _ = get_or_create_participant(db_session, "Adams, Alice")
    db_session.commit()

    _upload(client, person)
    client.post(f"/participants/{person.id}/photo/remove", data={
        "csrf_token": _csrf(client, f"/participants/{person.id}"),
    }, follow_redirects=False)

    actions = [entry.action for entry in db_session.scalars(select(AuditLog)).all()]
    assert "upload_participant_photo" in actions
    assert "remove_participant_photo" in actions
