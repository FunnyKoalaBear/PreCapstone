from collections import Counter

from django.shortcuts import render

from tracking.overview import active_boxes
from tracking.status import AT_VENDOR, IN_PROGRESS, NOT_STARTED, WAITING


def home(request):
    if not request.user.is_staff:
        return render(request, 'core/home.html')
    boxes = active_boxes()
    codes = Counter(e['code'] for e in boxes)
    in_house = [e for e in boxes if e['code'] in (WAITING, IN_PROGRESS)]
    at_vendor = [e for e in boxes if e['code'] == AT_VENDOR]
    # Stagnant first (longest idle first), then due date
    in_house.sort(key=lambda e: (
        not e['stagnant'], -(e['idle'].total_seconds() if e['stagnant'] else 0),
        e['due_date'], e['order'].job_number, e['item'].item_no, e['box_label'] or '',
    ))
    # Late first (most days late first), then oldest out-since
    at_vendor.sort(key=lambda e: (-e['late_days'], e['started_at'] is None, e['started_at']))
    return render(request, 'core/home.html', {
        'in_house': in_house, 'at_vendor': at_vendor,
        'counters': {
            'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR],
            'not_started': codes[NOT_STARTED], 'overdue': len({e['order'].pk for e in boxes if e['late']}),
            'stagnant': sum(e['stagnant'] for e in boxes),
            'vendor_overdue': sum(1 for e in at_vendor if e['late_days']),
        },
    })
