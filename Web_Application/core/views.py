from collections import Counter

from django.shortcuts import render

from core.permissions import OPERATOR, role_required
from tracking.matrix import matrix
from tracking.overview import active_boxes
from tracking.status import AT_VENDOR, IN_PROGRESS, NOT_STARTED, WAITING


HOME_ROWS = 10  # most urgent items shown on the home page


@role_required(OPERATOR)
def home(request):
    boxes = active_boxes()
    codes = Counter(e['code'] for e in boxes)
    return render(request, 'core/home.html', {
        'matrix': matrix(limit=HOME_ROWS),
        'counters': {
            'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR],
            'not_started': codes[NOT_STARTED], 'overdue': len({e['order'].pk for e in boxes if e['late']}),
            'stagnant': sum(e['stagnant'] for e in boxes),
            'vendor_overdue': sum(1 for e in boxes if e['late_days']),
        },
    })
