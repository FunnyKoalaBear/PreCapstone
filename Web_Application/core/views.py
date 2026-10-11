from django.shortcuts import render
from django.utils import timezone

from orders.models import OrderItem
from tracking.status import (
    AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, current_row, days_late, due_back, idle_for, is_stagnant,
    item_status,
)


def home(request):
    if not request.user.is_staff:
        return render(request, 'core/home.html')
    today, now = timezone.localdate(), timezone.now()
    entries = [(i, item_status(i)) for i in OrderItem.objects.select_related('order__customer')]
    # An order is complete only when all its items are
    done = {}
    for item, st in entries:
        done[item.order_id] = done.get(item.order_id, True) and st.code == COMPLETE
    in_house, at_vendor, counts, overdue = [], [], dict.fromkeys((IN_PROGRESS, WAITING, AT_VENDOR, NOT_STARTED), 0), set()
    for item, st in entries:
        order = item.order
        if done[order.pk]:
            continue
        late = order.due_date < today
        if late:
            overdue.add(order.pk)
        if st.code in counts:
            counts[st.code] += 1
        row = current_row(st)
        if st.code not in (WAITING, IN_PROGRESS, AT_VENDOR) or row is None:
            continue
        process = row.step.process
        entry = {
            'item': item, 'order': order, 'code': st.code, 'label': st.label, 'process': process,
            'vendor': process.vendor_name, 'last_event_at': st.last_event_at, 'due_date': order.due_date,
            'idle': idle_for(st, now), 'stagnant': is_stagnant(st, now),
            'late': late, 'out_since': row.started_at,
            'days_out': (now - row.started_at).days if row.started_at else None,
        }
        if st.code == AT_VENDOR:
            entry['due_back'] = due_back(row.started_at, process.vendor_lead_days)
            entry['late_days'] = days_late(entry['due_back'], today)
            at_vendor.append(entry)
        else:
            in_house.append(entry)
    # Stagnant first (longest idle first), then due date
    in_house.sort(key=lambda e: (not e['stagnant'], -(e['idle'].total_seconds() if e['stagnant'] else 0), e['due_date'], e['order'].job_number, e['item'].item_no))
    # Late first (most days late first), then oldest out-since
    at_vendor.sort(key=lambda e: (-e['late_days'], e['out_since'] is None, e['out_since']))
    return render(request, 'core/home.html', {
        'in_house': in_house, 'at_vendor': at_vendor,
        'counters': {
            'in_progress': counts[IN_PROGRESS], 'waiting': counts[WAITING], 'at_vendor': counts[AT_VENDOR],
            'not_started': counts[NOT_STARTED], 'overdue': len(overdue),
            'stagnant': sum(e['stagnant'] for e in in_house),
            'vendor_overdue': sum(1 for e in at_vendor if e['late_days']),
        },
    })
