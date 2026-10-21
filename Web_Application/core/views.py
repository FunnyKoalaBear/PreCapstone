from collections import Counter
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from core.permissions import ADMIN, OPERATOR, ROLES, role_level, role_required
from orders.models import OrderItem
from tracking.matrix import matrix
from tracking.models import Event, ScannerDevice
from tracking.overview import active_boxes
from tracking.status import (
    AT_VENDOR, CANCELLED, COMPLETE, IN_PROGRESS, LABELS, NOT_STARTED, PAUSED, SHIPPED, WAITING, item_status,
)


HOME_ROWS = 10  # most urgent items shown on the home page


@role_required(OPERATOR)
def home(request):
    boxes = active_boxes()
    codes = Counter(e['code'] for e in boxes)
    return render(request, 'core/home.html', {
        'matrix': matrix(limit=HOME_ROWS),
        'counters': {
            'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR],
            'not_started': codes[NOT_STARTED], 'paused': codes[PAUSED], 'overdue': len({e['order'].pk for e in boxes if e['late']}),
            'stagnant': sum(e['stagnant'] for e in boxes),
            'vendor_overdue': sum(1 for e in boxes if e['late_days']),
        },
    })


DASHBOARD_STATUSES = [NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, PAUSED, COMPLETE, SHIPPED, CANCELLED]
SILENT_AFTER = timedelta(hours=24)  # a scanner that has not reported for this long is listed
VOID_WINDOW = timedelta(days=7)


@role_required(ADMIN)
def dashboard(request):
    now = timezone.now()
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(now), datetime.min.time()))
    roles = Counter(ROLES[level] if level >= 0 else None for level in (role_level(u) for u in User.objects.filter(is_active=True)))
    statuses = Counter(item_status(i).code for i in OrderItem.objects.all())
    silent = ScannerDevice.objects.filter(active=True).filter(Q(last_seen__isnull=True) | Q(last_seen__lt=now - SILENT_AFTER)).order_by('name')
    return render(request, 'core/dashboard.html', {
        'roles': [(r, roles[r]) for r in ROLES] + [(None, roles[None])],
        'statuses': [(c, LABELS[c], statuses[c]) for c in DASHBOARD_STATUSES],
        'events_today': Event.objects.filter(received_at__gte=today_start).count(),
        'silent_devices': silent,
        'voided': Event.objects.filter(event_type=Event.Type.CORRECTION, occurred_at__gte=now - VOID_WINDOW).count(),
    })
