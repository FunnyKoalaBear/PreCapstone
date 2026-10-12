import unicodedata

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .models import Customer


def normalize_name(name):
    """Trim and collapse any run of spaces (full-width included) to one space."""
    return ' '.join(str(name or '').split())


def find_customer(name):
    """The customer whose name matches ignoring case, width and spacing, or None."""
    key = unicodedata.normalize('NFKC', normalize_name(name)).casefold()
    if not key:
        return None
    return next((c for c in Customer.objects.all() if unicodedata.normalize('NFKC', normalize_name(c.name)).casefold() == key), None)


def create_customer(name, contact=''):
    """Create a customer; a name that already exists (see find_customer) is an error."""
    name = normalize_name(name)
    if find_customer(name):
        raise ValidationError(_('A customer with this name already exists.'))
    return Customer.objects.create(name=name, contact=str(contact or '').strip())


def get_or_create_customer(name):
    """The existing customer with this name, else a new one."""
    return find_customer(name) or create_customer(name)
