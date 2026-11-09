import logging

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from core.permissions import OPERATOR, PLANNER, has_role, role_required
from tracking.forms import ResultForm
from tracking.planning import step_plan
from tracking.status import (
    CANCELLED, CLOSED, COMPLETE, IN_PROGRESS, LABELS, MANUAL_BUTTONS, NOT_STARTED, SHIPPED, STEP_LABELS, days_late, events_with_voids, gantt, item_statuses,
    iter_statuses, manual_actions, order_completed_at, order_summary, step_due_back, timeline,
)

from . import export, importer, pdf
from .printing import queue_print
from .forms import CustomerForm, ImportForm, OrderForm, OrderItemFormSet, VendorForm
from .models import Customer, Order, OrderChange, OrderItem, PrintJob, Vendor
from .process_tab import _tab_processes
from .runs import previous_runs

logger = logging.getLogger(__name__)

SORTS = {
    'due': ('due_date', 'job_number'),
    'newest': ('-job_number',),
    'customer': ('customer__name', 'due_date'),
    'completed': ('-job_number',),  # re-sorted by completion date after the statuses are known
}
SORT_LABELS = [('due', gettext_lazy('Due date')), ('newest', gettext_lazy('Newest')), ('customer', gettext_lazy('Customer')),
               ('completed', gettext_lazy('Recently completed'))]
STATUS_CODES = [NOT_STARTED, IN_PROGRESS, COMPLETE, SHIPPED, CANCELLED]


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


def _completions(orders):
    """({order id: (code, label)}, {order id: completion datetime or None}) from one derivation of the item statuses."""
    statuses = item_statuses([i for o in orders for i in o.items.all()])
    return {o.pk: order_summary(o, statuses) for o in orders}, {o.pk: order_completed_at(o, statuses) for o in orders}


def _day(when):
    return timezone.localdate(when) if when else None


@role_required(PLANNER)
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    today, rows = timezone.localdate(), []
    orders = list(customer.order_set.prefetch_related('items').order_by('-job_number'))
    summaries, done = _completions(orders)
    for o in orders:
        code, label = summaries[o.pk]
        rows.append({'order': o, 'code': code, 'label': label, 'late': o.due_date < today and code not in CLOSED, 'completed': _day(done[o.pk])})
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


def _filtered_orders(params):
    """(rows, q, status, sort, late) for the order list; the CSV export reuses it so both show the same orders."""
    q, status = params.get('q', '').strip(), params.get('status', '')
    sort, late = params.get('sort', 'due'), params.get('late') == '1'
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
    orders = list(orders)
    summaries, done = _completions(orders)
    for o in orders:
        code, label = summaries[o.pk]
        is_late = o.due_date < today and code not in CLOSED
        if (status in STATUS_CODES and code != status) or (late and not is_late):
            continue
        rows.append({'order': o, 'code': code, 'label': label, 'late': is_late, 'items': len(o.items.all()), 'completed': _day(done[o.pk]), 'completed_at': done[o.pk],
                     'boxes': sum(i.box_count for i in o.items.all()) if any(i.box_count > 1 for i in o.items.all()) else None})
    if sort == 'completed':  # newest completion first, orders without one last (the sort is stable: job number order within ties)
        rows = sorted((r for r in rows if r['completed_at']), key=lambda r: r['completed_at'], reverse=True) + [r for r in rows if not r['completed_at']]
    return rows, q, status, sort, late


def _tab_orders(request):
    rows, q, status, sort, late = _filtered_orders(request.GET)
    return {
        'rows': rows, 'q': q, 'status': status, 'sort': sort, 'late': late,
        'status_choices': [(c, LABELS[c]) for c in STATUS_CODES], 'sort_choices': SORT_LABELS,
    }


def _tab_new(request):
    data, files = request.POST or None, request.FILES or None
    form, formset = OrderForm(data), OrderItemFormSet(data, files)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save()
            formset.instance = order
            formset.save()
        messages.success(request, _('Order %(job)s created.') % {'job': order.job_number})
        return redirect(_tab_url('orders', q=order.job_number))
    return {'form': form, 'formset': formset}


def _tab_import(request):
    preview, error = None, None
    if request.method == 'POST' and 'confirm' in request.POST:
        saved = request.session.get(SESSION_KEY)
        if not saved:
            messages.error(request, _('The upload expired. Choose the file again.'))
            return redirect(_tab_url('import'))
        preview = importer.parse(saved['text'], saved['create'])
        if preview.ok:
            orders, items = importer.run_import(preview)
            request.session.pop(SESSION_KEY, None)
            messages.success(request, _('Imported %(orders)d orders with %(items)d items.') % {'orders': orders, 'items': items})
            return redirect(_tab_url('orders'))
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
    return {'preview': preview, 'error': error, 'import_form': ImportForm(initial={'create_customers': request.POST.get('create_customers') == 'on'})}


KIND_FILTERS = [('', gettext_lazy('All')), ('INTERNAL', gettext_lazy('In-house')), ('EXTERNAL', gettext_lazy('External'))]


def _tab_codes(request):
    g = request.GET
    q, kind, show_inactive = g.get('q', '').strip(), g.get('kind', ''), g.get('inactive') == '1'
    back = _tab_url('codes', q=q, kind=kind, inactive='1' if show_inactive else '')
    if request.method == 'POST':
        pk, action = request.POST.get('vendor', ''), request.POST.get('action')
        target = get_object_or_404(Vendor, pk=pk) if pk.isdigit() else None
        if action in ('deactivate', 'reactivate') and target:
            target.active = action == 'reactivate'
            target.save(update_fields=['active'])
            done = _('Code %(code)s reactivated.') if target.active else _('Code %(code)s deactivated.')
            messages.success(request, done % {'code': target.code})
            return redirect(back)
        form = VendorForm(request.POST, instance=target)
        if form.is_valid():
            vendor = form.save()
            messages.success(request, (_('Code %(code)s updated.') if target else _('Code %(code)s added.')) % {'code': vendor.code})
            return redirect(back)
    else:
        edit = g.get('edit', '')
        target = Vendor.objects.filter(pk=edit).first() if edit.isdigit() else None
        form = VendorForm(instance=target)
    vendors = Vendor.objects.all() if show_inactive else Vendor.objects.filter(active=True)
    if q:
        vendors = vendors.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(name_ja__icontains=q))
    if kind == 'EXTERNAL':
        vendors = vendors.exclude(kind=Vendor.Kind.INTERNAL)
    elif kind in Vendor.Kind.values:
        vendors = vendors.filter(kind=kind)
    return {'vendors': vendors, 'q': q, 'kind': kind, 'kinds': KIND_FILTERS, 'show_inactive': show_inactive,
            'form': form, 'editing': target, 'back': back}


# key, label, minimum role, handler
TABS = [
    ('orders', gettext_lazy('Orders'), OPERATOR, _tab_orders), ('new', gettext_lazy('New order'), PLANNER, _tab_new),
    ('import', gettext_lazy('Import'), PLANNER, _tab_import), ('codes', gettext_lazy('Codes'), PLANNER, _tab_codes),
    ('processes', gettext_lazy('Processes'), PLANNER, _tab_processes),
]


def _tab_url(tab, **params):
    query = urlencode({**({'tab': tab} if tab != 'orders' else {}), **{k: v for k, v in params.items() if v}})
    return reverse('order_list') + (f'?{query}' if query else '')


def order_list(request):
    """The Orders page: one URL, five tabs (?tab=orders|new|import|codes|processes), each with its own minimum role."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    key = request.GET.get('tab')
    key = key if key in {t[0] for t in TABS} else 'orders'
    _key, _label, role, handler = next(t for t in TABS if t[0] == key)
    if not has_role(request.user, role):
        raise PermissionDenied
    result = handler(request)
    if isinstance(result, HttpResponseBase):
        return result
    tabs = [{'key': k, 'label': label, 'url': _tab_url(k)} for k, label, r, _h in TABS if has_role(request.user, r)]
    return render(request, 'orders/hub.html', {'tab': key, 'tabs': tabs, **result})


class RedirectKeepMethod(HttpResponseRedirect):
    status_code = 308


def _old_url(tab):
    """Old page URL -> its tab; a POST is sent on with 308 so the form data still arrives."""
    def view(request):
        url = _tab_url(tab, **{k: v for k, v in request.GET.items() if k != 'tab'})
        return RedirectKeepMethod(url) if request.method == 'POST' else redirect(url)
    return view


order_create, order_import, code_list = _old_url('new'), _old_url('import'), _old_url('codes')


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
    order_items = list(order.items.all())
    statuses = item_statuses(order_items)
    code, label = order_summary(order, statuses)
    today, items = timezone.localdate(), []
    for item in order_items:
        st = statuses[item.pk]
        boxes = [_box_entry(b, today) for b in st.boxes]
        linked = sum(b['open'] for b in boxes)
        items.append({
            'item': item, 'status': st, 'completed': _day(st.completed_at), 'boxes': boxes, 'runs': previous_runs(item),
            'single': item.box_count == 1 and len(boxes) == 1,
            'linked': linked, 'result_form': ResultForm(item) if linked else None, 'cancelled': st.cancel_event,
            'assignment': next((b['box'].assignment for b in boxes if b['open']), None),
        })
    return render(request, 'orders/order_detail.html', {
        'order': order, 'code': code, 'label': label, 'items': items, 'completed': _day(order_completed_at(order, statuses)),
        'changes': order.changes.select_related('changed_by'),
        'late': order.due_date < timezone.localdate() and code not in CLOSED,
    })


@role_required(OPERATOR)
def item_drawing(request, pk):
    item = get_object_or_404(OrderItem, pk=pk)
    if not item.drawing_file:
        raise Http404
    return FileResponse(item.drawing_file.open('rb'), filename=item.drawing_file.name.rsplit('/', 1)[-1])


@role_required(PLANNER)
def route_copy(request):
    """Steps of the latest earlier, not cancelled item with this part number, as JSON for the order form."""
    part_number = request.GET.get('part_number', '').strip()
    current = request.GET.get('item', '')
    earlier = OrderItem.objects.filter(part_number__iexact=part_number).order_by('-pk')
    if current.isdigit():
        earlier = earlier.filter(pk__lt=int(current))
    source = next((i for i, st in iter_statuses(earlier.select_related('order')) if part_number and st.code != CANCELLED), None)
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
def order_export(request):
    rows = _filtered_orders(request.GET)[0]
    return export.csv_response(f'orders_{timezone.localdate():%Y%m%d}.csv', export.ORDER_COLUMNS, export.order_rows(r['order'] for r in rows))


@role_required(PLANNER)
def order_events_export(request, job_number):
    order = get_object_or_404(Order, job_number=job_number)
    return export.csv_response(f'events_{order.job_number}.csv', export.EVENT_COLUMNS, export.event_rows(order))


@role_required(OPERATOR)
def order_sheet(request, job_number):
    order = get_object_or_404(Order, job_number=job_number)
    response = HttpResponse(pdf.order_pdf(order), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{order.job_number}.pdf"'
    return response


@role_required(OPERATOR)
def item_sheet(request, pk):
    item = get_object_or_404(OrderItem, pk=pk)
    response = HttpResponse(pdf.item_pdf(item), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{item.item_code}.pdf"'
    return response


@role_required(OPERATOR)
@require_POST
def item_print(request, pk):
    item = get_object_or_404(OrderItem.objects.select_related('order'), pk=pk)
    queue_print(item, PrintJob.Source.BUTTON)
    messages.success(request, _('Sheet of %(code)s queued for printing.') % {'code': item.item_code})
    return redirect(reverse('order_detail', args=[item.order.job_number]) + f'#item-{item.pk}')
