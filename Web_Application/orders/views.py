from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from tracking.status import (
    COMPLETE, IN_PROGRESS, LABELS, NOT_STARTED, STEP_LABELS, days_late, due_back, events_for_item, item_status,
    order_summary,
)

from .forms import OrderForm, OrderItemFormSet
from .models import Order

SORTS = {
    'due': ('due_date', 'job_number'),
    'newest': ('-job_number',),
    'customer': ('customer__name', 'due_date'),
}
SORT_LABELS = [('due', gettext_lazy('Due date')), ('newest', gettext_lazy('Newest')), ('customer', gettext_lazy('Customer'))]
STATUS_CODES = [NOT_STARTED, IN_PROGRESS, COMPLETE]


@staff_member_required
def order_create(request):
    data, files = request.POST or None, request.FILES or None
    form, formset = OrderForm(data), OrderItemFormSet(data, files)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save()
            formset.instance = order
            formset.save()
        messages.success(request, _('Order %(job)s created.') % {'job': order.job_number})
        return redirect('order_list')
    return render(request, 'orders/order_form.html', {'form': form, 'formset': formset})


@staff_member_required
def order_list(request):
    q, status = request.GET.get('q', '').strip(), request.GET.get('status', '')
    sort, late = request.GET.get('sort', 'due'), request.GET.get('late') == '1'
    if sort not in SORTS:
        sort = 'due'
    orders = Order.objects.select_related('customer').prefetch_related('items').order_by(*SORTS[sort])
    if q:
        orders = orders.filter(
            Q(job_number__icontains=q) | Q(customer__name__icontains=q)
            | Q(customer_order_number__icontains=q) | Q(items__part_name__icontains=q)
        ).distinct()
    today, rows = timezone.localdate(), []
    for o in orders:
        code, label = order_summary(o)
        is_late = o.due_date < today and code != COMPLETE
        if (status in STATUS_CODES and code != status) or (late and not is_late):
            continue
        rows.append({'order': o, 'code': code, 'label': label, 'late': is_late, 'items': len(o.items.all())})
    return render(request, 'orders/order_list.html', {
        'rows': rows, 'q': q, 'status': status, 'sort': sort, 'late': late,
        'status_choices': [(c, LABELS[c]) for c in STATUS_CODES], 'sort_choices': SORT_LABELS,
    })


def _step_entry(row, today):
    entry = {'row': row, 'label': STEP_LABELS[row.state], 'due_back': None, 'late_days': 0}
    if row.state == 'at_vendor':
        entry['due_back'] = due_back(row.started_at, row.step.process.vendor_lead_days)
        entry['late_days'] = days_late(entry['due_back'], today)
    return entry


@staff_member_required
def order_detail(request, job_number):
    order = get_object_or_404(Order.objects.select_related('customer'), job_number=job_number)
    code, label = order_summary(order)
    today, items = timezone.localdate(), []
    for item in order.items.all():
        st = item_status(item)
        items.append({
            'item': item, 'status': st,
            'steps': [_step_entry(r, today) for r in st.rows],
            'events': events_for_item(item).select_related('process', 'device'),
            'assignment': item.tagassignment_set.filter(end_at__isnull=True).select_related('tag', 'box').first(),
        })
    return render(request, 'orders/order_detail.html', {
        'order': order, 'code': code, 'label': label, 'items': items,
        'late': order.due_date < timezone.localdate() and code != COMPLETE,
    })
