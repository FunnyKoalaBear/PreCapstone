import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.utils.translation import gettext as _

from .models import Customer, Order, Process, Vendor
from .services import create_order, customer_key, get_or_create_customer, normalize_name

MAX_BYTES = 2 * 1024 * 1024
# Field -> header names accepted in the file. Change this when the real company format arrives.
COLUMNS = {
    'customer': ('customer',), 'customer_order_number': ('customer_order_number',), 'due_date': ('due_date',),
    'part_name': ('part_name',), 'part_number': ('part_number',), 'drawing_number': ('drawing_number',), 'revision': ('revision',), 'quantity': ('quantity',), 'material': ('material',), 'notes': ('notes',), 'route': ('route',),
}
REQUIRED = ('customer', 'due_date', 'part_name', 'quantity', 'route')
STEP_SEPARATOR, PART_SEPARATOR = '|', ':'
# Which vendor-code kind each process kind needs
CODE_KIND = {Process.Kind.MATERIAL: Vendor.Kind.SUPPLIER, Process.Kind.MACHINING: Vendor.Kind.INTERNAL, Process.Kind.OUTSOURCED: Vendor.Kind.VENDOR}


@dataclass
class Row:
    number: int  # file row, the header being row 1
    data: dict
    errors: list = field(default_factory=list)
    customer: object = None  # existing Customer, or None
    customer_name: str = ''
    due: date | None = None
    quantity: int = 0
    steps: list = field(default_factory=list)  # (Process, Vendor, date or None)

    @property
    def ok(self):
        return not self.errors


@dataclass
class Preview:
    file_errors: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    groups: list = field(default_factory=list)  # lists of rows that become one order
    new_customers: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.file_errors and bool(self.rows) and all(r.ok for r in self.rows)

    @property
    def error_rows(self):
        return sum(not r.ok for r in self.rows)


def decode(raw):
    """File text: UTF-8 (with or without BOM), else Shift-JIS (cp932); None when neither fits."""
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def parse_date(text):
    """date from YYYY-MM-DD or YYYY/MM/DD, else None."""
    m = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', unicodedata.normalize('NFKC', text or '').strip())
    try:
        return date(*map(int, m.groups())) if m else None
    except ValueError:
        return None


def _key(text):
    return unicodedata.normalize('NFKC', text or '').strip().casefold()


def _read(text):
    """(header map field -> column index, data rows) or an error message."""
    rows = [r for r in csv.reader(io.StringIO(text, newline=''))]
    if not rows:
        return None, None, _('The file is empty.')
    header = [_key(h) for h in rows[0]]
    index, errors = {}, []
    for name, aliases in COLUMNS.items():
        found = next((header.index(a) for a in aliases if a in header), None)
        if found is not None:
            index[name] = found
        elif name in REQUIRED:
            errors.append(_('Missing column: %(name)s') % {'name': aliases[0]})
    return index, rows[1:], '; '.join(errors) or None


def _route(row, text, processes, vendors):
    """Fill row.steps from the route text, adding an error per bad step."""
    if not text.strip():
        row.errors.append(_('Route required'))
        return
    for raw in text.split(STEP_SEPARATOR):
        raw = raw.strip()
        parts = [p.strip() for p in raw.split(PART_SEPARATOR)]
        if len(parts) not in (2, 3) or not all(parts[:2]):
            row.errors.append(_('Malformed route step "%(step)s"') % {'step': raw})
            continue
        process, vendor = processes.get(_key(parts[0])), vendors.get(parts[1])
        if not process:
            row.errors.append(_('Unknown process "%(name)s"') % {'name': parts[0]})
        elif not process.active:
            row.errors.append(_('Process "%(name)s" is no longer active') % {'name': parts[0]})
        elif not vendor:
            row.errors.append(_('Unknown code %(code)s') % {'code': parts[1]})
        elif not vendor.active:
            row.errors.append(_('Code %(code)s is no longer active') % {'code': vendor.code})
        elif vendor.kind != CODE_KIND[process.kind]:
            row.errors.append(_('Code %(code)s does not fit %(process)s: it needs a %(kind)s code') % {
                'code': vendor.code, 'process': parts[0], 'kind': CODE_KIND[process.kind].label})
        else:
            due = parse_date(parts[2]) if len(parts) == 3 else None
            if len(parts) == 3 and not due:
                row.errors.append(_('Invalid due date in route step "%(step)s"') % {'step': raw})
                continue
            row.steps.append((process, vendor, due))


def parse(text, create_customers=False):
    """Validate the CSV text and group its rows into orders. Nothing is saved."""
    preview = Preview()
    index, data_rows, error = _read(text)
    if error:
        preview.file_errors.append(error)
        return preview
    cell = lambda r, name: (r[index[name]].strip() if name in index and index[name] < len(r) else '')
    customers = {customer_key(c.name): c for c in Customer.objects.all()}
    processes = {}
    for p in Process.objects.all():
        processes[_key(p.name)] = p
        if p.name_ja:
            processes.setdefault(_key(p.name_ja), p)
    vendors = {v.code: v for v in Vendor.objects.all()}
    taken = set(Order.objects.exclude(customer_order_number='').values_list('customer_id', 'customer_order_number'))
    groups, new_names = {}, {}
    for number, r in enumerate(data_rows, 2):
        if not any(c.strip() for c in r):
            continue
        row = Row(number, {n: cell(r, n) for n in COLUMNS})
        d = row.data
        key = customer_key(d['customer'])
        if not key:
            row.errors.append(_('Customer missing'))
        elif key in customers:
            row.customer = customers[key]
        elif create_customers:
            row.customer_name = new_names.setdefault(key, normalize_name(d['customer']))
        else:
            row.errors.append(_('Unknown customer'))
        row.due = parse_date(d['due_date'])
        if not row.due:
            row.errors.append(_('Invalid due date'))
        if not d['part_name']:
            row.errors.append(_('Part name missing'))
        try:
            row.quantity = int(unicodedata.normalize('NFKC', d['quantity']))
            if row.quantity < 1:
                row.errors.append(_('Quantity must be at least 1'))
        except ValueError:
            row.errors.append(_('Quantity must be a whole number'))
        if len(d['material']) > 100:
            row.errors.append(_('Material is too long (at most 100 characters)'))
        for name, limit, label in (('part_number', 50, _('Part number')), ('drawing_number', 50, _('Drawing number')), ('revision', 10, _('Revision'))):
            if len(d[name]) > limit:
                row.errors.append(_('%(field)s is too long (at most %(n)d characters)') % {'field': label, 'n': limit})
        _route(row, d['route'], processes, vendors)
        preview.rows.append(row)
        # Rows with the same customer and order number form one order; a blank number stays alone
        po = d['customer_order_number']
        group = groups.setdefault((key, po), []) if key and po else []
        if row.customer and po and (row.customer.pk, po) in taken:
            row.errors.append(_('An order with this customer order number already exists'))
        if group and row.due and group[0].due and row.due != group[0].due:
            row.errors.append(_('Due date differs from the first row of this order'))
        group.append(row)
        if len(group) == 1:
            preview.groups.append(group)
    preview.new_customers = list(dict.fromkeys(r.customer_name for r in preview.rows if r.customer_name))
    if not preview.rows:
        preview.file_errors.append(_('The file has no data rows.'))
    return preview


@transaction.atomic
def run_import(preview):
    """Create every order of a clean preview in one transaction; returns (orders, items)."""
    if not preview.ok:
        raise ValueError('The preview has errors.')
    orders = items = 0
    for g in preview.groups:
        first = g[0]
        customer = first.customer or get_or_create_customer(first.customer_name)
        notes = '\n'.join(dict.fromkeys(r.data['notes'] for r in g if r.data['notes']))
        create_order(customer, first.data['customer_order_number'], first.due, notes, [
            {'part_name': r.data['part_name'], 'quantity': r.quantity, 'material': r.data['material'], 'steps': r.steps,
             'part_number': r.data['part_number'], 'drawing_number': r.data['drawing_number'], 'revision': r.data['revision']} for r in g])
        orders, items = orders + 1, items + len(g)
    return orders, items
