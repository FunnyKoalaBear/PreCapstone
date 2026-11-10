from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date

from orders.models import OrderItem

from .shipping import _matches
from .status import COMPLETE, SHIPPED, item_statuses

PERIODS = ('today', 'week', 'month')


def parse_day(text):
    """A date from YYYY-MM-DD text, or None for empty or impossible input."""
    try:
        return parse_date(text)
    except ValueError:
        return None


def period_range(period, today=None):
    """(first, last) JST dates of today / this week (Monday start) / this month, or None for an unknown period."""
    today = today or timezone.localdate()
    start = {'today': today, 'week': today - timedelta(days=today.weekday()), 'month': today.replace(day=1)}.get(period)
    return (start, today) if start else None


def completed_on(st):
    """JST date the item completed, or None when it is not COMPLETE or SHIPPED."""
    return timezone.localdate(st.completed_at) if st.completed_at else None


def completed_rows(done_from=None, done_to=None, customer=None, q=''):
    """Complete or shipped items, newest completion first, limited by completion date range, customer id and search text."""
    items = OrderItem.objects.select_related('order__customer')
    items = list(items.filter(order__customer_id=customer) if customer else items)
    statuses = item_statuses(items)
    rows = []
    for item in items:
        st = statuses[item.pk]
        day = completed_on(st) if st.code in (COMPLETE, SHIPPED) else None
        if day and _matches(item, customer, q) and not (done_from and day < done_from) and not (done_to and day > done_to):
            rows.append({'item': item, 'order': item.order, 'completed': day, 'late': day > item.order.due_date, 'shipped': st.code == SHIPPED, 'at': st.completed_at})
    rows.sort(key=lambda r: (r['at'], r['item'].item_code), reverse=True)
    return rows
