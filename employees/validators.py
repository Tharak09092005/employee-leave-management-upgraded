"""
File-upload validators.

We check three things for every upload:
    1. the file extension is on an allow-list,
    2. the file is not too big,
    3. the first few bytes of the file really match the claimed type
       (so a renamed .exe or .html file cannot pretend to be a .pdf/.jpg).

No external package (such as Pillow) is needed for this.
"""

import os

from django.core.exceptions import ValidationError

MAX_PHOTO_BYTES = 2 * 1024 * 1024       # 2 MB
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024    # 5 MB

# extension -> allowed "magic number" prefixes
FILE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".pdf": (b"%PDF",),
}

PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png")
DOCUMENT_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")


def _validate_upload(uploaded, allowed_extensions, max_bytes, label):
    extension = os.path.splitext(uploaded.name)[1].lower()

    if extension not in allowed_extensions:
        allowed = ", ".join(e.lstrip(".").upper() for e in allowed_extensions)
        raise ValidationError(f"Unsupported {label} type. Allowed types: {allowed}.")

    if uploaded.size > max_bytes:
        raise ValidationError(
            f"The {label} is too large. Maximum size is {max_bytes // (1024 * 1024)} MB."
        )

    uploaded.seek(0)
    header = uploaded.read(16)
    uploaded.seek(0)
    if not any(header.startswith(sig) for sig in FILE_SIGNATURES[extension]):
        raise ValidationError(f"The {label} content does not match its file type.")


def validate_profile_photo(uploaded):
    _validate_upload(uploaded, PHOTO_EXTENSIONS, MAX_PHOTO_BYTES, "photo")


def validate_leave_document(uploaded):
    _validate_upload(uploaded, DOCUMENT_EXTENSIONS, MAX_DOCUMENT_BYTES, "document")
