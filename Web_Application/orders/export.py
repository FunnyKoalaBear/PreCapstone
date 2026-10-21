import csv

from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from tracking.models import Event
from tracking.status import events_with_voids, item_status

# Same names as the CSV import, then its optional columns, then what only the export knows
ORDER_COLUMNS = [
    'customer', 'customer_order_number', 'due_date', 'part_name', 'quantity', 'material', 'notes', 'route',
    'part_number', 'drawing_number', 'revision', 'job_number', 'item_code', 'status',
]
EVENT_COLUMNS = ['time_jst', 'event_type', 'process', 'device', 'tag_uid', 'voided', 'item_code']
FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r')


class _Echo:
    def write(self, value):
        return value


def safe(cell):
    """Text that Excel could run as a formula gets a leading single quote."""
    return "'" + cell if isinstance(cell, str) and cell.startswith(FORMULA_STARTS) else cell


def csv_response(filename, header, rows):
    """Stream a UTF-8 CSV with a BOM, so Excel shows Japanese correctly."""
    writer = csv.writer(_Echo())

    def stream():
        yield '﻿'
        yield writer.writerow(header)
        for row in rows:
            yield writer.writerow([safe(c) for c in row])

    response = StreamingHttpResponse(stream(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _route_text(item):
    """process:code[:due_date] steps joined by |, the format the import reads."""
    parts = []
    for s in item.steps.select_related('process', 'vendor', 'process__default_vendor'):
        v = s.effective_vendor
        text = f'{s.process.name}:{v.code}' if v else s.process.name
        parts.append(text + (f':{s.due_date:%Y-%m-%d}' if s.due_date else ''))
    return '|'.join(parts)


def order_rows(orders):
    """One row per item of the given orders."""
    for order in orders:
        for item in order.items.all():
            yield [
                order.customer.name, order.customer_order_number, f'{order.due_date:%Y-%m-%d}', item.part_name, item.quantity, item.material,
                order.notes, _route_text(item), item.part_number, item.drawing_number, item.revision, order.job_number, item.item_code,
                str(item_status(item).label),
            ]


def event_rows(order):
    """Every event of the order's items, oldest first: scans on their tags plus cancellations and shipments."""
    rows = []
    for item in order.items.select_related('order'):
        codes = {s.process_id: s.effective_vendor.code for s in item.steps.select_related('vendor', 'process__default_vendor') if s.effective_vendor}
        events = [e for b in item_status(item).boxes if b.assignment for e in events_with_voids(b.assignments)]
        item_events = list(Event.objects.filter(item=item).select_related('created_by'))
        voids = {c.corrects_id: c for c in Event.objects.filter(event_type=Event.Type.CORRECTION, corrects__in=item_events)}
        for e in item_events:
            e.voided_by = voids.get(e.pk)
        for e in events + item_events:
            process = f'{codes[e.process_id]} {e.process}' if e.process_id in codes else str(e.process or '')
            rows.append((e.occurred_at, [
                timezone.localtime(e.occurred_at).strftime('%Y-%m-%d %H:%M:%S'), str(e.get_event_type_display()), process,
                str(e.device or e.created_by or ''), e.tag_uid, _('yes') if e.voided_by else _('no'), item.item_code,
            ]))
    return [row for _when, row in sorted(rows, key=lambda r: r[0])]
