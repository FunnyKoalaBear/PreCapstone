from django.utils import timezone

from orders.models import OrderItem

from .status import COMPLETE, SHIPPED, item_statuses


def _matches(item, customer, q):
    """Customer id and a search text over job / item code, part, customer and customer order number."""
    if customer and item.order.customer_id != customer:
        return False
    text = ' '.join((item.item_code, item.part_name, item.part_number, item.order.customer.name, item.order.customer_order_number)).lower()
    return q.lower() in text


def shipping_overview(*args, **kw):
    """(ready, months): complete items not yet shipped by due date, and per JST month the shipped count and on-time rate (newest first)."""
    data = shipping_data(*args, **kw)
    return data['ready'], data['months']


def shipping_data(today=None, ready_customer=None, ready_q='', due_from=None, due_to=None,
                  month_customer=None, month_q='', month_from='', month_to='', done_from=None, done_to=None,
                  ship_year=None, ship_month=None):
    """One pass over the items: {ready, months, shipped, years}.

    Ready can be limited by customer, search text, due-date range and completion-date range (JST dates); months by customer, search text and a YYYY-MM range.
    shipped lists the items shipped in ship_year (and ship_month, 0/None = all months) by JST ship date, newest first; years = years with shipments.
    """
    today = today or timezone.localdate()
    ready, months, shipped, years = [], {}, [], {today.year}
    items = list(OrderItem.objects.select_related('order__customer').order_by('order__due_date', 'order__job_number', 'item_no'))
    statuses = item_statuses(items)
    for item in items:
        st = statuses[item.pk]
        if st.code == COMPLETE:
            due, done = item.order.due_date, timezone.localdate(st.completed_at) if st.completed_at else None
            if (_matches(item, ready_customer, ready_q) and not (due_from and due < due_from) and not (due_to and due > due_to)
                    and not (done_from and (done is None or done < done_from)) and not (done_to and (done is None or done > done_to))):
                ready.append({'item': item, 'order': item.order, 'late': due < today, 'completed': done, 'waiting': (today - done).days if done else None})
        elif st.code == SHIPPED:
            event = st.ship_event
            shipped_on = timezone.localtime(event.occurred_at).date()
            years.add(shipped_on.year)
            if ship_year == shipped_on.year and not (ship_month and ship_month != shipped_on.month):
                late = (shipped_on - item.order.due_date).days
                shipped.append({'item': item, 'order': item.order, 'shipped_on': shipped_on, 'at': event.occurred_at, 'on_time': late <= 0, 'late_days': max(late, 0)})
            key = shipped_on.strftime('%Y-%m')
            if not _matches(item, month_customer, month_q) or (month_from and key < month_from) or (month_to and key > month_to):
                continue
            m = months.setdefault(key, {'shipped': 0, 'on_time': 0})
            m['shipped'] += 1
            m['on_time'] += shipped_on <= item.order.due_date
    rows = [{'month': k, **v, 'rate': round(100 * v['on_time'] / v['shipped'])} for k, v in sorted(months.items(), reverse=True)]
    shipped.sort(key=lambda r: r['at'], reverse=True)
    return {'ready': ready, 'months': rows, 'shipped': shipped, 'years': sorted(years, reverse=True)}
