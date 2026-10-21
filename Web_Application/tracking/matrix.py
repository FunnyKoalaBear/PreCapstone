from django.utils import timezone

from orders.models import OrderItem, Process

from .status import CLOSED, DONE, STEP_LABELS, current_row, item_status


def _cells(status, from_current):
    """(cells, width): leading finished steps collapse into one 'done_run' cell, or are dropped when starting at the current step."""
    rows, current = status.rows, current_row(status)
    if from_current:
        start = next((i for i, r in enumerate(rows) if r is current), 0)
        run, shown = 0, rows[start:]
    else:
        run = next((i for i, r in enumerate(rows) if r.state != DONE), len(rows))
        shown = rows[run:]
    cells = [{'state': DONE, 'label': STEP_LABELS[DONE], 'run': run}] if run else []
    for r in shown:
        vendor = r.step.effective_vendor
        state = 'paused' if r.paused else r.state
        cells.append({
            'state': state, 'label': STEP_LABELS[state], 'partial': r.partial and r.state != DONE, 'process': r.step.process, 'current': r is current,
            'vendor': (vendor.display_name if vendor else r.step.process.vendor_name) if r.step.process.kind == Process.Kind.OUTSOURCED else '',
        })
    return cells, (len(shown) if from_current else len(rows))


def matrix(limit=25, status='', customer=None, due_from=None, due_to=None, late=False, from_current=False, today=None):
    """Unfinished items by order due date with their route as cells. Returns rows, column count and whether more rows exist."""
    today = today or timezone.localdate()
    items = OrderItem.objects.select_related('order__customer').order_by('order__due_date', 'order__job_number', 'item_no')
    if customer:
        items = items.filter(order__customer_id=customer)
    if due_from:
        items = items.filter(order__due_date__gte=due_from)
    if due_to:
        items = items.filter(order__due_date__lte=due_to)
    if late:
        items = items.filter(order__due_date__lt=today)
    rows, more = [], False
    for item in items.iterator(chunk_size=100):
        st = item_status(item)
        if st.code in CLOSED or (status and st.code != status):
            continue
        if len(rows) == limit:
            more = True
            break
        cells, width = _cells(st, from_current)
        rows.append({'item': item, 'order': item.order, 'status': st, 'cells': cells, 'width': width, 'late': item.order.due_date < today})
    columns = max((r['width'] for r in rows), default=0)
    for r in rows:
        r['pad'] = range(columns - r['width'])
    return {'rows': rows, 'columns': range(columns), 'more': more, 'from_current': from_current}
