from django.utils import timezone

from orders.models import OrderItem

from .status import COMPLETE, SHIPPED, item_statuses


def _matches(item, customer, q):
    """Customer id and a search text over job / item code, part, customer and customer order number."""
    if customer and item.order.customer_id != customer:
        return False
    text = ' '.join((item.item_code, item.part_name, item.part_number, item.order.customer.name, item.order.customer_order_number)).lower()
    return q.lower() in text


def shipping_overview(today=None, ready_customer=None, ready_q='', due_from=None, due_to=None,
                      month_customer=None, month_q='', month_from='', month_to='', done_from=None, done_to=None):
    """(ready, months): complete items not yet shipped by due date, and per JST month the shipped count and on-time rate (newest first).

    Ready can be limited by customer, search text, due-date range and completion-date range (JST dates); months by customer, search text and a YYYY-MM range.
    """
    today = today or timezone.localdate()
    ready, months = [], {}
    items = list(OrderItem.objects.select_related('order__customer').order_by('order__due_date', 'order__job_number', 'item_no'))
    statuses = item_statuses(items)
    for item in items:
        st = statuses[item.pk]
        if st.code == COMPLETE:
            due, done = item.order.due_date, timezone.localdate(st.completed_at) if st.completed_at else None
            if (_matches(item, ready_customer, ready_q) and not (due_from and due < due_from) and not (due_to and due > due_to)
                    and not (done_from and (done is None or done < done_from)) and not (done_to and (done is None or done > done_to))):
                ready.append({'item': item, 'order': item.order, 'late': due < today, 'completed': done, 'waiting': (today - done).days if done else None})
        elif st.code == SHIPPED and _matches(item, month_customer, month_q):
            shipped_on = timezone.localtime(st.ship_event.occurred_at).date()
            key = shipped_on.strftime('%Y-%m')
            if (month_from and key < month_from) or (month_to and key > month_to):
                continue
            m = months.setdefault(key, {'shipped': 0, 'on_time': 0})
            m['shipped'] += 1
            m['on_time'] += shipped_on <= item.order.due_date
    rows = [{'month': k, **v, 'rate': round(100 * v['on_time'] / v['shipped'])} for k, v in sorted(months.items(), reverse=True)]
    return ready, rows
