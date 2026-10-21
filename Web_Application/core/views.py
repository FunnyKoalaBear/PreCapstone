from collections import Counter

from django.shortcuts import render

from core.permissions import OPERATOR, role_required
from tracking.overview import active_boxes, board_groups
from tracking.status import AT_VENDOR, IN_PROGRESS, NOT_STARTED, WAITING


@role_required(OPERATOR)
def home(request):
    boxes = active_boxes()
    codes = Counter(e['code'] for e in boxes)
    in_house, outsourced = board_groups()
    return render(request, 'core/home.html', {
        'in_house': in_house, 'outsourced': outsourced,
        'counters': {
            'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR],
            'not_started': codes[NOT_STARTED], 'overdue': len({e['order'].pk for e in boxes if e['late']}),
            'stagnant': sum(e['stagnant'] for e in boxes),
            'vendor_overdue': sum(1 for e in boxes if e['late_days']),
        },
    })
