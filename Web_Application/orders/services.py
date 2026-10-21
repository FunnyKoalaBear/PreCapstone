import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import Customer, Order, OrderItem, RouteStep


def normalize_name(name):
    """Trim and collapse any run of spaces (full-width included) to one space."""
    return ' '.join(str(name or '').split())


def customer_key(name):
    """Name with case, width and spacing ignored, for matching customers."""
    return unicodedata.normalize('NFKC', normalize_name(name)).casefold()


def find_customer(name, exclude=None):
    """The customer (other than `exclude`) whose name matches ignoring case, width and spacing, or None."""
    key = customer_key(name)
    if not key:
        return None
    others = Customer.objects.exclude(pk=exclude.pk) if exclude else Customer.objects.all()
    return next((c for c in others if customer_key(c.name) == key), None)


def create_customer(name, contact=''):
    """Create a customer; a name that already exists (see find_customer) is an error."""
    name = normalize_name(name)
    if find_customer(name):
        raise ValidationError(_('A customer with this name already exists.'))
    return Customer.objects.create(name=name, contact=str(contact or '').strip())


def update_customer(customer, name, contact=''):
    """Rename or re-contact a customer; a name used by another customer is an error."""
    name = normalize_name(name)
    if find_customer(name, exclude=customer):
        raise ValidationError(_('A customer with this name already exists.'))
    customer.name, customer.contact = name, str(contact or '').strip()
    customer.save()
    return customer


def get_or_create_customer(name):
    """The existing customer with this name, else a new one."""
    return find_customer(name) or create_customer(name)


@transaction.atomic
def create_order(customer, customer_order_number, due_date, notes, items):
    """Create an order with its items and routes. items: dicts with part_name, quantity, steps [(process, vendor, due_date)] and optional material, part_number, drawing_number, revision."""
    order = Order.objects.create(customer=customer, customer_order_number=customer_order_number, due_date=due_date, notes=notes)
    for data in items:
        item = OrderItem.objects.create(order=order, part_name=data['part_name'], quantity=data['quantity'], material=data.get('material', ''),
            part_number=data.get('part_number', ''), drawing_number=data.get('drawing_number', ''), revision=data.get('revision', ''))
        RouteStep.objects.bulk_create(
            RouteStep(item=item, sequence=i, process=process, vendor=vendor, due_date=due)
            for i, (process, vendor, due) in enumerate(data['steps'], 1))
    return order
