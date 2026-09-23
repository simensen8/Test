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
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
# A phone camera is around 12 megapixels and a DSLR 45; beyond that the
# file is not a portrait of anyone. The cap matters because pixels, not
# bytes, are what gets allocated: a 435 KB PNG can decode to 400 MB,
# which on a 1 GB server is the whole machine.
MAX_PIXELS = 60_000_000


class PhotoError(ValueError):
    """The upload isn't an image this app will store."""


def normalize_photo(raw: bytes) -> bytes:
    """Decode, strip metadata, downscale, and re-encode as JPEG."""
    try:
        opened = Image.open(io.BytesIO(raw))
    except Exception as exc:  # Pillow raises a variety of types here
        raise PhotoError(
            "That file doesn't look like a photo. JPEG, PNG or WebP all work."
        ) from exc

    if opened.format and opened.format.upper() not in ACCEPTED_FORMATS:
        raise PhotoError(f"{opened.format} images aren't supported -- please use a JPEG, PNG or WebP.")

    # Checked from the header, before any pixels are allocated.
    width, height = opened.size
    if width * height > MAX_PIXELS:
        raise PhotoError(
            f"That image is {width}x{height}, which is far larger than a portrait needs to be. "
            "Please use a normal photo from a phone or camera."
        )

    try:
        opened.load()
    except Exception as exc:
        raise PhotoError("That photo couldn't be read -- the file may be damaged.") from exc

    # Phones record orientation in EXIF rather than rotating the pixels;
    # applying it here means the photo isn't sideways once EXIF is gone.
    image: Image.Image = ImageOps.exif_transpose(opened) or opened
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()
