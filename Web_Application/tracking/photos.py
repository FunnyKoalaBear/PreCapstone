import uuid
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO_SIZE = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 50_000_000  # guards against decompression bombs
MAX_SIDE, QUALITY = 1600, 85
ALLOWED_FORMATS = ('JPEG', 'PNG', 'WEBP')  # checked on the decoded image, not on the file name


def photo_upload_to(instance, filename):
    """Random name under defects/YYYY/MM/; the uploaded file name is never used."""
    return f'defects/{timezone.localdate():%Y/%m}/{uuid.uuid4().hex}.jpg'


def process_photo(upload):
    """Check an uploaded image with Pillow and return a clean JPEG (EXIF orientation applied, EXIF dropped, at most 1600 px)."""
    if upload.size > MAX_PHOTO_SIZE:
        raise ValidationError(_('File too large (maximum 8 MB).'))
    bad = ValidationError(_('Upload a JPEG, PNG or WEBP image.'))
    try:
        with Image.open(upload) as img:
            if img.format not in ALLOWED_FORMATS:
                raise bad
            if img.width * img.height > MAX_PHOTO_PIXELS:
                raise ValidationError(_('The image is too large.'))
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'LA', 'P'):  # transparent areas become white
                img = img.convert('RGBA')
                flat = Image.new('RGB', img.size, 'white')
                flat.paste(img, mask=img.getchannel('A'))
                img = flat
            img = img.convert('RGB')
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = BytesIO()
            img.save(out, 'JPEG', quality=QUALITY)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise bad
    return ContentFile(out.getvalue(), name='photo.jpg')
