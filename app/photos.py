"""Turning an uploaded image into something safe to store.

Two things happen here that matter beyond making thumbnails load fast.
A photo from a phone carries EXIF metadata -- often including where it
was taken -- and re-encoding the pixels drops all of it. And decoding
before storing means a file that isn't really an image is refused at the
door rather than served back to a browser later.
"""
import io

from PIL import Image, ImageOps

MAX_EDGE = 600  # plenty for a profile portrait and a row thumbnail
JPEG_QUALITY = 85
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC", "GIF", "BMP"}


class PhotoError(ValueError):
    """The upload isn't an image this app will store."""


def normalize_photo(raw: bytes) -> bytes:
    """Decode, strip metadata, downscale, and re-encode as JPEG."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # Pillow raises a variety of types here
        raise PhotoError(
            "That file doesn't look like a photo. JPEG, PNG or WebP all work."
        ) from exc

    if image.format and image.format.upper() not in ACCEPTED_FORMATS:
        raise PhotoError(f"{image.format} images aren't supported -- please use a JPEG, PNG or WebP.")

    # Phones record orientation in EXIF rather than rotating the pixels;
    # applying it here means the photo isn't sideways once EXIF is gone.
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
