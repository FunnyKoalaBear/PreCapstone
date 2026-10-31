from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render
from django.utils.dateparse import parse_date
from django.utils import timezone

from core.rules import silent_after
from core.permissions import OPERATOR, PLANNER, ROLES, role_level, role_required
from orders.models import Customer, OrderItem
from tracking.matrix import matrix
from tracking.models import Event, ScannerDevice
from tracking.overview import active_boxes, open_items
from tracking.status import (
    AT_VENDOR, CANCELLED, COMPLETE, IN_PROGRESS, LABELS, NOT_STARTED, PAUSED, SHIPPED, WAITING, item_statuses,
)


MATRIX_PAGE = 50  # rows per "Show more"
MATRIX_STATUSES = [NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, PAUSED]


@role_required(OPERATOR)
def home(request):
    g = request.GET
    status = g.get('status') if g.get('status') in MATRIX_STATUSES else ''
    customer = int(g['customer']) if g.get('customer', '').isdigit() else None
    due_from, due_to = parse_date(g.get('due_from', '')), parse_date(g.get('due_to', ''))
    late, from_current = g.get('late') == '1', g.get('current') == '1'
    n = min(max(int(g['n']), MATRIX_PAGE), 500) if g.get('n', '').isdigit() else MATRIX_PAGE
    items = list(open_items())
    statuses = item_statuses(items)  # derived once, shared by the counters and the matrix
    boxes = active_boxes(items=items, statuses=statuses)
    codes = Counter(e['code'] for e in boxes)
    keep = {k: v for k, v in g.items() if k != 'n' and v}
    return render(request, 'core/home.html', {
        'matrix': matrix(n, status, customer, due_from, due_to, late, from_current, items=open_items(), statuses=statuses),
        'status': status, 'customer': customer, 'due_from': g.get('due_from', ''), 'due_to': g.get('due_to', ''),
        'late': late, 'from_current': from_current, 'customers': Customer.objects.order_by('name'),
        'status_choices': [(c, LABELS[c]) for c in MATRIX_STATUSES], 'more_url': '?' + urlencode({**keep, 'n': n + MATRIX_PAGE}),
        'counters': {
            'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR],
            'not_started': codes[NOT_STARTED], 'paused': codes[PAUSED], 'overdue': len({e['order'].pk for e in boxes if e['late']}),
            'stagnant': sum(e['stagnant'] for e in boxes),
            'vendor_overdue': sum(1 for e in boxes if e['late_days']),
        },
    })


DASHBOARD_STATUSES = [NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, PAUSED, COMPLETE, SHIPPED, CANCELLED]
VOID_WINDOW = timedelta(days=7)


@role_required(PLANNER)
def dashboard(request):
    now = timezone.now()
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(now), datetime.min.time()))
    roles = Counter(ROLES[level] if level >= 0 else None for level in (role_level(u) for u in User.objects.filter(is_active=True)))
    statuses = Counter(st.code for st in item_statuses(OrderItem.objects.all()).values())
    silent = ScannerDevice.objects.filter(active=True).filter(Q(last_seen__isnull=True) | Q(last_seen__lt=now - silent_after())).order_by('name')
    return render(request, 'core/dashboard.html', {
        'roles': [(r, roles[r]) for r in ROLES] + [(None, roles[None])],
        'statuses': [(c, LABELS[c], statuses[c]) for c in DASHBOARD_STATUSES],
        'events_today': Event.objects.filter(received_at__gte=today_start).count(),
        'silent_devices': silent,
        'voided': Event.objects.filter(event_type=Event.Type.CORRECTION, occurred_at__gte=now - VOID_WINDOW).count(),
    })
