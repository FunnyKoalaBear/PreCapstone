from django.utils import timezone

from orders.models import OrderItem, Process

from .status import AT_VENDOR, COMPLETE, IN_PROGRESS, WAITING, current_row, days_late, due_back, idle_for, is_stagnant, item_status

QUEUE_WAITING = 3  # waiting boxes at which a process card shows "queue building"


def active_boxes(now=None, today=None):
    """One dict per unfinished box (placeholders included), with its own status, timing and flags."""
    now, today = now or timezone.now(), today or timezone.localdate()
    out = []
    for item in OrderItem.objects.select_related('order__customer'):
        st = item_status(item)
        for box in st.boxes:
            if box.code == COMPLETE:
                continue
            row = current_row(box)
            process = row.step.process if row else None
            e = {
                'item': item, 'order': item.order, 'box': box, 'box_label': box.label if box.assignment else None,
                'multi': len(st.boxes) > 1, 'code': box.code, 'label': box.code_label, 'process': process,
                'vendor': process.vendor_name if process else '',
                'started_at': row.started_at if row else None, 'last_event_at': box.last_event_at,
                'idle': idle_for(box, now), 'stagnant': is_stagnant(box, now),
                'due_date': item.order.due_date, 'late': item.order.due_date < today,
                'due_back': None, 'late_days': 0,
            }
            e['days_out'] = (now - e['started_at']).days if e['started_at'] else None
            if box.code == AT_VENDOR:
                e['due_back'] = due_back(e['started_at'], process.vendor_lead_days)
                e['late_days'] = days_late(e['due_back'], today)
            out.append(e)
    out.sort(key=lambda e: (e['due_date'], e['order'].job_number, e['item'].item_no, e['box_label'] or ''))
    return out


def process_board(now=None, today=None):
    """Per in-house/outsourced process: running boxes (longest first) and waiting boxes (longest idle first)."""
    now = now or timezone.now()
    boxes = active_boxes(now, today)
    longest = lambda d: -d.total_seconds() if d else 0
    cards = []
    kinds = (Process.Kind.MACHINING, Process.Kind.OUTSOURCED)
    for p in Process.objects.filter(kind__in=kinds).order_by('name'):
        running = [dict(e, running_for=now - e['started_at'] if e['started_at'] else None)
                   for e in boxes if e['process'] == p and e['code'] in (IN_PROGRESS, AT_VENDOR)]
        waiting = [e for e in boxes if e['process'] == p and e['code'] == WAITING]
        running.sort(key=lambda e: (e['running_for'] is None, longest(e['running_for'])))
        waiting.sort(key=lambda e: (e['idle'] is None, longest(e['idle'])))
        cards.append({'process': p, 'outsourced': p.kind == Process.Kind.OUTSOURCED, 'running': running,
                      'waiting': waiting, 'queue': len(waiting) >= QUEUE_WAITING})
    return cards


def board_groups(now=None, today=None):
    """The process board cards split into (in_house, outsourced)."""
    cards = process_board(now, today)
    return [c for c in cards if not c['outsourced']], [c for c in cards if c['outsourced']]
