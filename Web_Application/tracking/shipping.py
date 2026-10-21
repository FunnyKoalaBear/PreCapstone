from django.utils import timezone

from orders.models import OrderItem

from .status import COMPLETE, SHIPPED, item_status


def shipping_overview(today=None):
    """(ready, months): complete items not yet shipped by due date, and per JST month the shipped count and on-time rate (newest first)."""
    today = today or timezone.localdate()
    ready, months = [], {}
    for item in OrderItem.objects.select_related('order__customer').order_by('order__due_date', 'order__job_number', 'item_no'):
        st = item_status(item)
        if st.code == COMPLETE:
            ready.append({'item': item, 'order': item.order, 'late': item.order.due_date < today})
        elif st.code == SHIPPED:
            shipped_on = timezone.localtime(st.ship_event.occurred_at).date()
            m = months.setdefault(shipped_on.strftime('%Y-%m'), {'shipped': 0, 'on_time': 0})
            m['shipped'] += 1
            m['on_time'] += shipped_on <= item.order.due_date
    rows = [{'month': k, **v, 'rate': round(100 * v['on_time'] / v['shipped'])} for k, v in sorted(months.items(), reverse=True)]
    return ready, rows
