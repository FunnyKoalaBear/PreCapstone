from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

MAX_DRAWING_SIZE = 10 * 1024 * 1024

validate_drawing_extension = FileExtensionValidator(['pdf', 'png', 'jpg', 'jpeg'])


def validate_drawing_size(f):
    if f.size > MAX_DRAWING_SIZE:
        raise ValidationError(_('File too large (maximum 10 MB).'))


def drawing_upload_to(instance, filename):
    return f'drawings/{instance.order.job_number}/{filename}'
