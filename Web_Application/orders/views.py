from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core.permissions import OPERATOR, PLANNER, role_required
from tracking.forms import ResultForm
from tracking.planning import step_plan
from tracking.status import (
    CANCELLED, COMPLETE, IN_PROGRESS, LABELS, MANUAL_BUTTONS, NOT_STARTED, STEP_LABELS, days_late, events_with_voids, gantt, item_status,
    manual_actions, order_summary, step_due_back, timeline,
)

from . import importer, pdf
from .forms import CustomerForm, OrderForm, OrderItemFormSet
from .models import Customer, Order, OrderChange, OrderItem, Vendor

SORTS = {
    'due': ('due_date', 'job_number'),
    'newest': ('-job_number',),
    'customer': ('customer__name', 'due_date'),
}
SORT_LABELS = [('due', gettext_lazy('Due date')), ('newest', gettext_lazy('Newest')), ('customer', gettext_lazy('Customer'))]
STATUS_CODES = [NOT_STARTED, IN_PROGRESS, COMPLETE, CANCELLED]


@role_required(PLANNER)
def customer_list(request):
    form = CustomerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        customer = form.save()
        messages.success(request, _('Customer %(name)s added.') % {'name': customer.name})
        return redirect('customer_list')
    q = request.GET.get('q', '').strip()
    customers = Customer.objects.annotate(order_count=Count('order')).order_by('name')
    if q:
        customers = customers.filter(name__icontains=q)
    return render(request, 'orders/customer_list.html', {'form': form, 'customers': customers, 'q': q})


@role_required(PLANNER)
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    today, rows = timezone.localdate(), []
    for o in customer.order_set.prefetch_related('items').order_by('-job_number'):
        code, label = order_summary(o)
        rows.append({'order': o, 'code': code, 'label': label, 'late': o.due_date < today and code not in (COMPLETE, CANCELLED)})
    return render(request, 'orders/customer_detail.html', {'customer': customer, 'rows': rows})


@role_required(PLANNER)
def customer_edit(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, _('Customer %(name)s updated.') % {'name': customer.name})
        return redirect('customer_detail', pk=customer.pk)
    return render(request, 'orders/customer_edit.html', {'form': form, 'customer': customer})


@role_required(PLANNER)
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


ORDER_FIELDS = ['customer', 'customer_order_number', 'due_date', 'notes']
ITEM_FIELDS = ['part_name', 'part_number', 'drawing_number', 'revision', 'quantity', 'material', 'box_count']


def _order_values(order):
    return {f: str(getattr(order, f) if getattr(order, f) is not None else '') for f in ORDER_FIELDS}


def _item_values(item):
    values = {f: str(getattr(item, f)) for f in ITEM_FIELDS}
    values['drawing_file'] = item.drawing_file.name or ''
    values['route'] = ' > '.join(
        s.process.name + (f' [{s.vendor.code}]' if s.vendor else '') + (f' due {s.due_date:%Y-%m-%d}' if s.due_date else '')
        + (f' slip {s.slip_number}' if s.slip_number else '') for s in item.steps.select_related('process', 'vendor'))
    return values


def _log_changes(order, user, old_order, old_items):
    """Append one OrderChange per changed field, added item and deleted item."""
    rows = []
    row = lambda field, old, new, item=None, code='': rows.append(OrderChange(
        order=order, item=item, item_code=code, field=field, old_value=old, new_value=new, changed_by=user))
    new_order = _order_values(order)
    rows += [OrderChange(order=order, field=f, old_value=old_order[f], new_value=new_order[f], changed_by=user)
             for f in ORDER_FIELDS if old_order[f] != new_order[f]]
    seen = set()
    for item in order.items.all():
        seen.add(item.pk)
        if item.pk not in old_items:
            row('item_added', '', f'{item.item_code} {item.part_name}', item, item.item_code)
            continue
        new = _item_values(item)
        for f, old in old_items[item.pk].items():
            if f != 'code' and old != new[f]:
                row(f, old, new[f], item, item.item_code)
    for pk, old in old_items.items():
        if pk not in seen:
            row('item_deleted', f"{old['code']} {old['part_name']}", '', None, old['code'])
    OrderChange.objects.bulk_create(rows)


@role_required(PLANNER)
def order_edit(request, job_number):
    order = get_object_or_404(Order, job_number=job_number)
    # Snapshot before the forms touch the instances
    old_order = _order_values(order)
    old_items = {i.pk: {**_item_values(i), 'code': i.item_code} for i in order.items.all()}
    data, files = request.POST or None, request.FILES or None
    form, formset = OrderForm(data, instance=order), OrderItemFormSet(data, files, instance=order)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            form.save()
            formset.save()
            _log_changes(order, request.user, old_order, old_items)
        messages.success(request, _('Order %(job)s updated.') % {'job': order.job_number})
        return redirect('order_detail', job_number=order.job_number)
    return render(request, 'orders/order_form.html', {'form': form, 'formset': formset, 'order': order})


@role_required(OPERATOR)
def order_list(request):
    q, status = request.GET.get('q', '').strip(), request.GET.get('status', '')
    sort, late = request.GET.get('sort', 'due'), request.GET.get('late') == '1'
    if sort not in SORTS:
        sort = 'due'
    orders = Order.objects.select_related('customer').prefetch_related('items').order_by(*SORTS[sort])
    if q:
        orders = orders.filter(
            Q(job_number__icontains=q) | Q(customer__name__icontains=q)
            | Q(customer_order_number__icontains=q) | Q(items__part_name__icontains=q) | Q(items__material__icontains=q)
            | Q(items__part_number__icontains=q) | Q(items__drawing_number__icontains=q)
        ).distinct()
    today, rows = timezone.localdate(), []
    for o in orders:
        code, label = order_summary(o)
        is_late = o.due_date < today and code not in (COMPLETE, CANCELLED)
        if (status in STATUS_CODES and code != status) or (late and not is_late):
            continue
        rows.append({'order': o, 'code': code, 'label': label, 'late': is_late, 'items': len(o.items.all()),
                     'boxes': sum(i.box_count for i in o.items.all()) if any(i.box_count > 1 for i in o.items.all()) else None})
    return render(request, 'orders/order_list.html', {
        'rows': rows, 'q': q, 'status': status, 'sort': sort, 'late': late,
        'status_choices': [(c, LABELS[c]) for c in STATUS_CODES], 'sort_choices': SORT_LABELS,
    })


def _step_entry(row, today, timing, plan, current=None):
    state = 'paused' if row.paused else row.state
    entry = {'row': row, 'label': STEP_LABELS[state], 'badge': state, 'due_back': None, 'late_days': 0, 'current': row.step == current, 'plan': plan, **timing}
    if row.state == 'at_vendor':
        entry['due_back'] = step_due_back(row.step, row.started_at)
        entry['late_days'] = days_late(entry['due_back'], today)
    return entry


def _box_entry(b, today):
    a = b.assignment
    steps = [_step_entry(r, today, t, p, b.current_step) for r, t, p in zip(b.rows, timeline(b.rows), step_plan(b.rows))]
    chart = gantt(b.rows)
    for s, bar in zip(steps, chart['rows'] if chart else []):
        s['bar'] = bar
    return {
        'box': b, 'steps': steps, 'chart': chart,
        'events': events_with_voids(b.assignments) if a else [],
        'open': bool(a and a.end_at is None),
        'actions': [MANUAL_BUTTONS[t] for t, _p in manual_actions(b)] if a and a.end_at is None else [],
    }


@role_required(OPERATOR)
def order_detail(request, job_number):
    order = get_object_or_404(Order.objects.select_related('customer'), job_number=job_number)
    code, label = order_summary(order)
    today, items = timezone.localdate(), []
    for item in order.items.all():
        st = item_status(item)
        boxes = [_box_entry(b, today) for b in st.boxes]
        linked = sum(b['open'] for b in boxes)
        items.append({
            'item': item, 'status': st, 'boxes': boxes,
            'single': item.box_count == 1 and len(boxes) == 1,
            'linked': linked, 'result_form': ResultForm(item) if linked else None, 'cancelled': st.cancel_event,
            'assignment': next((b['box'].assignment for b in boxes if b['open']), None),
        })
    return render(request, 'orders/order_detail.html', {
        'order': order, 'code': code, 'label': label, 'items': items,
        'changes': order.changes.select_related('changed_by'),
        'late': order.due_date < timezone.localdate() and code not in (COMPLETE, CANCELLED),
    })


@role_required(OPERATOR)
def item_drawing(request, pk):
    item = get_object_or_404(OrderItem, pk=pk)
    if not item.drawing_file:
        raise Http404
    return FileResponse(item.drawing_file.open('rb'), filename=item.drawing_file.name.rsplit('/', 1)[-1])


@role_required(PLANNER)
def code_list(request):
    q, kind = request.GET.get('q', '').strip(), request.GET.get('kind', '')
    vendors = Vendor.objects.all()
    if q:
        vendors = vendors.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(name_ja__icontains=q))
    if kind in Vendor.Kind.values:
        vendors = vendors.filter(kind=kind)
    return render(request, 'orders/code_list.html', {'vendors': vendors, 'q': q, 'kind': kind, 'kinds': Vendor.Kind.choices})


@role_required(PLANNER)
def route_copy(request):
    """Steps of the latest earlier, not cancelled item with this part number, as JSON for the order form."""
    part_number = request.GET.get('part_number', '').strip()
    current = request.GET.get('item', '')
    earlier = OrderItem.objects.filter(part_number__iexact=part_number).order_by('-pk')
    if current.isdigit():
        earlier = earlier.filter(pk__lt=int(current))
    source = next((i for i in earlier.select_related('order') if part_number and item_status(i).code != CANCELLED), None)
    if not source:
        return JsonResponse({'found': False, 'steps': []})
    steps = list(source.steps.select_related('process'))
    active = [s for s in steps if s.process.active]
    return JsonResponse({
        'found': True, 'job': source.item_code, 'skipped': len(steps) - len(active),
        'steps': [{'process': s.process_id, 'vendor': s.vendor_id} for s in active],
    })


SESSION_KEY = 'order_import'


@role_required(PLANNER)
def order_import(request):
    preview, error = None, None
    if request.method == 'POST' and 'confirm' in request.POST:
        saved = request.session.get(SESSION_KEY)
        if not saved:
            messages.error(request, _('The upload expired. Choose the file again.'))
            return redirect('order_import')
        preview = importer.parse(saved['text'], saved['create'])
        if preview.ok:
            orders, items = importer.run_import(preview)
            request.session.pop(SESSION_KEY, None)
            messages.success(request, _('Imported %(orders)d orders with %(items)d items.') % {'orders': orders, 'items': items})
            return redirect('order_list')
    elif request.method == 'POST':
        upload, create = request.FILES.get('file'), request.POST.get('create_customers') == 'on'
        text = None
        if not upload:
            error = _('Choose a CSV file.')
        elif upload.size > importer.MAX_BYTES:
            error = _('The file is too large (limit 2 MB).')
        elif (text := importer.decode(upload.read())) is None:
            error = _('Could not read the file. Save it as UTF-8 or Shift-JIS CSV.')
        if text is not None:
            request.session[SESSION_KEY] = {'text': text, 'create': create}
            preview = importer.parse(text, create)
    return render(request, 'orders/order_import.html', {'preview': preview, 'error': error})


@role_required(PLANNER)
def order_sheet(request, job_number):
    order = get_object_or_404(Order, job_number=job_number)
    response = HttpResponse(pdf.order_pdf(order), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{order.job_number}.pdf"'
    return response
