import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import get_language, gettext_lazy

from core.permissions import ADMIN, OPERATOR, PLANNER, role_required
from orders.models import Customer, OrderItem, Process, Vendor

from .forms import AssignTagForm, CorrectEventForm, PickTagForm, ResultForm, SimulateScanForm
from .models import Box, Event, Tag, TagAssignment
from .overview import active_boxes
from .analysis import Filters, facts_json, plan_rows, result_rows, slowest, step_facts
from .priority import TIER_LABELS, TIERS, priority
from .shipping import months_json, shipping_overview
from .services import assign_tag, can_correct, correct_event as correct_event_service, next_step, record_event, record_result, record_step_state, release_assignment, replace_tag, resolve_tag, cancel_item, ship_item
from .status import AT_VENDOR, CANCELLED, COMPLETE, IN_PROGRESS, LABELS, NOT_STARTED, PAUSED, WAITING, current_row, item_status


def _next_step(uid):
    """What the simulate page offers for a tag: unassigned, finished, or the single next action."""
    a = resolve_tag(uid, timezone.now())
    if a is None:
        return {'state': 'unassigned'}
    box, todo = next_step(a)
    info = {'assignment': a, 'status': box}
    if todo is None:
        return {'state': 'finished', **info}
    etype, process = todo
    return {'state': 'next', 'value': f'{process.pk}:{etype}', 'label': f'{Event.Type(etype).label} - {process}', **info}


@role_required(PLANNER)
def correct_event(request, pk):
    if not can_correct(request.user):
        raise PermissionDenied
    event = get_object_or_404(Event.objects.select_related('device', 'process'), pk=pk, event_type__in=[t for t in Event.Type.values if t != Event.Type.CORRECTION])
    nxt = request.GET.get('next', '')
    back = nxt if url_has_allowed_host_and_scheme(nxt, {request.get_host()}) else reverse('order_list')
    form = CorrectEventForm(request.POST or None, allow_replace=event.event_type in Event.REQUIRED_KIND)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            correct_event_service(event, request.user, d['reason'], d['replacement'])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, _('Event corrected.'))
            return redirect(back)
    return render(request, 'tracking/correct_event.html', {'form': form, 'event': event, 'back': back})


@role_required(ADMIN)
def simulate(request):
    form = SimulateScanForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            event, assignment = record_event(d['device'], d['tag_uid'], d['process'], d['event_type'])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if assignment:
                messages.success(request, _('Saved: box %(box)s, item %(item)s, status: %(status)s') % {
                    'box': assignment.box, 'item': assignment.item.item_code,
                    'status': item_status(assignment.item).label,
                })
            else:
                messages.warning(request, _('Event saved, but tag %(uid)s matches no item.') % {'uid': event.tag_uid})
            return redirect('simulate')
    uid = request.GET.get('tag', '').strip().upper()
    open_ = TagAssignment.objects.filter(end_at__isnull=True).select_related('tag', 'box', 'item__order').order_by('tag__uid')
    return render(request, 'tracking/simulate.html', {
        'form': form, 'pick': PickTagForm(initial={'device': request.GET.get('device'), 'tag': uid}),
        'uid': uid, 'device': request.GET.get('device', ''), 'step': _next_step(uid) if uid else None,
        'picks': [(a.tag.uid, f'{a.tag.uid} - {a.item.item_code} - box {a.box.label} - {a.item.part_name}') for a in open_],
        'events': Event.objects.select_related('device', 'process').order_by('-occurred_at', '-pk')[:10],
    })


def _tags_url(request):
    """The Tags page URL, keeping the search a release/replace form was posted from."""
    q = request.POST.get('q', '').strip()
    return reverse('tag_manage') + (f'?{urlencode({"q": q})}' if q else '')


@role_required(PLANNER)
def tag_manage(request):
    form = AssignTagForm(request.POST if request.POST.get('action') == 'assign' else None)
    if request.method == 'POST':
        if request.POST.get('action') == 'release':
            pk = request.POST.get('assignment', '')
            if not pk.isdigit():
                raise Http404
            a = release_assignment(get_object_or_404(TagAssignment, pk=pk, end_at__isnull=True))
            messages.success(request, _('Tag %(tag)s released from box %(box)s.') % {'tag': a.tag, 'box': a.box})
            return redirect(_tags_url(request))
        if request.POST.get('action') == 'replace':
            pk = request.POST.get('assignment', '')
            if not pk.isdigit():
                raise Http404
            old = get_object_or_404(TagAssignment.objects.select_related('tag', 'box'), pk=pk)
            try:
                new = replace_tag(old, request.POST.get('new_uid', ''), mark_lost=request.POST.get('mark_lost') == 'on')
            except ValidationError as e:
                messages.error(request, ' '.join(e.messages))
            else:
                messages.success(request, _('Tag %(old)s replaced by %(new)s in box %(box)s.') % {'old': old.tag, 'new': new.tag, 'box': new.box})
            return redirect(_tags_url(request))
        if form.is_valid():
            d = form.cleaned_data
            try:
                a = assign_tag(d['item'], d['box_label'], d['tag_uid'])
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(request, _('Tag %(tag)s linked to box %(box)s, item %(item)s.') % {
                    'tag': a.tag, 'box': a.box, 'item': a.item.item_code,
                })
                return redirect('tag_manage')
    assignments = TagAssignment.objects.select_related('tag', 'box', 'item__order__customer')
    open_ = list(assignments.filter(end_at__isnull=True).order_by('item__order__job_number', 'item__item_no', 'start_at'))
    seen = {}
    for a in open_:  # position of the box within its item
        seen[a.item_id] = a.nth = seen.get(a.item_id, 0) + 1
    q, in_use = request.GET.get('q', '').strip().lower(), len(open_)
    if q:
        hay = lambda a: [a.item.item_code, a.item.order.job_number, a.item.part_name, a.item.order.customer.name, a.box.label, a.tag.uid]
        open_ = [a for a in open_ if any(q in h.lower() for h in hay(a))]
    return render(request, 'tracking/tag_manage.html', {
        'form': form,
        'open': open_ if q else [], 'q': q, 'in_use': in_use,
        'tag_uids': Tag.objects.filter(active=True).values_list('uid', flat=True),
        'box_labels': Box.objects.filter(active=True).values_list('label', flat=True),
    })


HISTORY_RANGES = [
    ('1m', 30, gettext_lazy('Last month')), ('3m', 91, gettext_lazy('Last 3 months')),
    ('6m', 182, gettext_lazy('Last 6 months')), ('1y', 365, gettext_lazy('Last year')),
]
HISTORY_CAP = 200


def _history_range(request, now):
    """(range key, start, end, from text, to text); bad custom dates fall back to 3 months."""
    key = request.GET.get('range')
    if key == 'custom':
        try:
            d1, d2 = date.fromisoformat(request.GET.get('from', '')), date.fromisoformat(request.GET.get('to', ''))
        except ValueError:
            d1 = d2 = None
        if d1 and d2 and d1 <= d2:
            aware = lambda d: timezone.make_aware(datetime.combine(d, time.min))  # JST via TIME_ZONE
            return key, aware(d1), aware(d2 + timedelta(days=1)), d1.isoformat(), d2.isoformat()
        key = '3m'
    days = {k: n for k, n, _label in HISTORY_RANGES}
    if key not in days:
        key = '3m'
    return key, now - timedelta(days=days[key]), now, '', ''


@role_required(PLANNER)
def tag_history(request):
    now = timezone.now()
    key, start, end, d_from, d_to = _history_range(request, now)
    q = request.GET.get('q', '').strip()
    rows = TagAssignment.objects.select_related('tag', 'box', 'item__order').filter(
        Q(end_at__isnull=True) | Q(end_at__gte=start), start_at__lt=end)
    if q:
        rows = rows.filter(Q(tag__uid__icontains=q) | Q(box__label__icontains=q))
    rows = list(rows.order_by('-start_at', '-pk')[:HISTORY_CAP + 1])
    return render(request, 'tracking/tag_history.html', {
        'rows': rows[:HISTORY_CAP], 'capped': len(rows) > HISTORY_CAP, 'cap': HISTORY_CAP, 'q': q, 'range': key,
        'ranges': [(k, label) for k, _n, label in HISTORY_RANGES] + [('custom', gettext_lazy('Custom'))],
        'd_from': d_from, 'd_to': d_to,
    })


PRIORITY_SORTS = {
    'risk': lambda r: (TIERS.index(r['tier']), r['slack']),
    'process': lambda r: (r['process'].name if r['process'] else '￿', TIERS.index(r['tier']), r['slack']),
    'waiting': lambda r: (r['idle'] is None, -r['idle'].total_seconds() if r['idle'] else 0, TIERS.index(r['tier']), r['slack']),
}
PRIORITY_SORT_CHOICES = [('risk', gettext_lazy('Risk')), ('process', gettext_lazy('Current process')), ('waiting', gettext_lazy('Idle for'))]


@role_required(OPERATOR)
def priorities(request):
    idle = {(e['item'].pk, e['box_label']): e['idle'] for e in active_boxes()}
    rows = []
    for item in OrderItem.objects.select_related('order__customer').order_by('order__due_date'):
        st = item_status(item)
        if st.code == CANCELLED:
            continue
        for box in st.boxes:
            a = box.assignment
            # Unlinked placeholders and finished boxes are left out
            if box.code == COMPLETE or a is None or (a.end_at and box.code == NOT_STARTED):
                continue
            row = current_row(box)
            p = priority(item.order, box)
            rows.append({
                'item': item, 'status': box, 'process': row.step.process if row else None,
                'box_label': box.label, 'multi': len(st.boxes) > 1, **p, 'label': TIER_LABELS[p['tier']],
                'idle': idle.get((item.pk, box.label)), 'risk_key': TIERS.index(p['tier']) * 100000 + p['slack'] + 50000,
            })
    sort = request.GET.get('sort') if request.GET.get('sort') in PRIORITY_SORTS else 'risk'
    rows.sort(key=PRIORITY_SORTS[sort])
    counts = {t: sum(r['tier'] == t for r in rows) for t in TIERS}
    tier = request.GET.get('tier', '')
    if tier in TIERS:
        rows = [r for r in rows if r['tier'] == tier]
    return render(request, 'tracking/priorities.html', {
        'rows': rows, 'tier': tier, 'total': sum(counts.values()), 'sort': sort, 'sort_choices': PRIORITY_SORT_CHOICES,
        'tiers': [(t, t.lower(), TIER_LABELS[t], counts[t]) for t in TIERS],
    })


BOX_STATUSES = {
    'in_progress': (IN_PROGRESS, gettext_lazy('In progress')), 'waiting': (WAITING, gettext_lazy('Waiting')),
    'at_vendor': (AT_VENDOR, gettext_lazy('At vendor')), 'not_started': (NOT_STARTED, gettext_lazy('Not started')),
    'paused': (PAUSED, gettext_lazy('Paused')),
}


BOX_SORTS = {
    'process': lambda e: (e['process'].name if e['process'] else '￿', e['due_date'], e['order'].job_number, e['item'].item_no),
    'due': lambda e: (e['due_date'], e['order'].job_number, e['item'].item_no, e['box_label'] or ''),
    'customer': lambda e: (str(e['order'].customer), e['due_date'], e['order'].job_number, e['item'].item_no),
    'waiting': lambda e: (e['idle'] is None, -e['idle'].total_seconds() if e['idle'] else 0, e['due_date'], e['order'].job_number),
}
BOX_SORT_CHOICES = [
    ('process', gettext_lazy('Current process')), ('due', gettext_lazy('Due date')),
    ('customer', gettext_lazy('Customer')), ('waiting', gettext_lazy('Idle for')),
]


@role_required(OPERATOR)
def boxes(request):
    status, stagnant, vendor_late = request.GET.get('status'), request.GET.get('stagnant') == '1', request.GET.get('vendor_late') == '1'
    sort = request.GET.get('sort') if request.GET.get('sort') in BOX_SORTS else 'due'
    rows, parts = active_boxes(), []
    if status in BOX_STATUSES:
        rows = [r for r in rows if r['code'] == BOX_STATUSES[status][0]]
    if vendor_late:
        rows = [r for r in rows if r['late_days']]
        parts.append(_('Vendor overdue'))
    elif status in BOX_STATUSES:
        parts.append(BOX_STATUSES[status][1])
    if stagnant:
        rows = [r for r in rows if r['stagnant']]
        parts.append(_('Stagnant'))
    rows.sort(key=BOX_SORTS[sort])
    keep = {k: v for k, v in (('status', status), ('stagnant', '1' if stagnant else ''), ('vendor_late', '1' if vendor_late else '')) if v}
    return render(request, 'tracking/boxes.html', {
        'rows': rows, 'title': ', '.join(map(str, parts)) or _('Active boxes'), 'sort': sort,
        'sort_choices': BOX_SORT_CHOICES, 'keep': keep,
    })


@role_required(OPERATOR)
def record_result_view(request, pk):
    item = get_object_or_404(OrderItem.objects.select_related('order'), pk=pk)
    back = redirect('order_detail', job_number=item.order.job_number)
    if request.method != 'POST':
        return back
    form = ResultForm(item, request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(m for errs in form.errors.values() for m in errs))
        return back
    d = form.cleaned_data
    try:
        record_result(request.user, d['assignment'], d['process'], d['quantity'], d['defects'] or 0, d['worker'], d['machine'])
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, _('Result recorded.'))
    return back


ANALYSIS_TABS = [('results', gettext_lazy('Results')), ('plan', gettext_lazy('Plan vs actual'))]
PLAN_SORTS = [('process', gettext_lazy('Process')), ('delay', gettext_lazy('Biggest delay'))]
FILTER_KEYS = ('date_from', 'date_to', 'customer', 'process', 'vendor')


@role_required(OPERATOR)
def analysis(request):
    """Analysis page: one URL, tabs (?tab=results|plan) sharing the date / customer / process / vendor filters."""
    g = request.GET
    tab = g.get('tab') if g.get('tab') in dict(ANALYSIS_TABS) else 'results'
    f = Filters.from_params(g)
    facts = step_facts(f)
    keep = {k: g[k] for k in FILTER_KEYS if g.get(k)}
    ctx = {
        'tab': tab, 'values': {k: g.get(k, '') for k in FILTER_KEYS}, 'filtered': bool(keep),
        'tabs': [{'key': k, 'label': label, 'url': f"{reverse('analysis')}?{urlencode({'tab': k, **keep})}"} for k, label in ANALYSIS_TABS],
        'customers': Customer.objects.order_by('name'), 'processes': Process.objects.order_by('name'), 'vendors': Vendor.objects.order_by('code'),
    }
    if tab == 'plan':
        sort = g.get('sort') if g.get('sort') in dict(PLAN_SORTS) else 'process'
        rows = plan_rows(facts, sort)
        ctx.update(rows=rows, sort=sort, sort_choices=PLAN_SORTS, top=slowest(rows))
    else:
        rows, total = result_rows(f, facts)
        flag = lambda name: next((r for r in rows if name in r['hot']), None)
        ctx.update(rows=rows, total=total, longest_wait=flag('wait'), longest_processing=flag('processing'))
    if settings.DEMO_EXPORT:
        ctx['facts_json'] = facts_json()
    return render(request, 'tracking/analysis.html', ctx)


def _old_analysis_url(tab):
    return lambda request: redirect(f"{reverse('analysis')}?{urlencode({**request.GET.dict(), 'tab': tab})}")


results_summary, plan_summary = _old_analysis_url('results'), _old_analysis_url('plan')


def _month(text):
    return text if re.fullmatch(r'\d{4}-\d{2}', text or '') else ''


@role_required(PLANNER)
def shipping(request):
    g = request.GET
    num = lambda k: int(g[k]) if g.get(k, '').isdigit() else None
    ready_p = {'r_customer': g.get('r_customer', ''), 'r_q': g.get('r_q', '').strip(), 'due_from': g.get('due_from', ''), 'due_to': g.get('due_to', '')}
    month_p = {'m_customer': g.get('m_customer', ''), 'm_q': g.get('m_q', '').strip(), 'month_from': _month(g.get('month_from')), 'month_to': _month(g.get('month_to'))}
    ready, months = shipping_overview(
        ready_customer=num('r_customer'), ready_q=ready_p['r_q'], due_from=parse_date(ready_p['due_from']), due_to=parse_date(ready_p['due_to']),
        month_customer=num('m_customer'), month_q=month_p['m_q'], month_from=month_p['month_from'], month_to=month_p['month_to'])
    hidden = lambda p: [(k, v) for k, v in p.items() if v]
    facts = months_json() if settings.DEMO_EXPORT else ''
    return render(request, 'tracking/shipping.html', {
        'ready': ready, 'months': months, 'customers': Customer.objects.order_by('name'), 'r': ready_p, 'm': month_p,
        'ready_hidden': hidden(ready_p), 'month_hidden': hidden(month_p), 'facts_json': facts,
    })


STEP_ACTIONS = {
    'pause': (Event.Type.PAUSED, gettext_lazy('Step paused.')),
    'resume': (Event.Type.RESUMED, gettext_lazy('Step resumed.')),
    'partial': (Event.Type.PARTIAL, gettext_lazy('Step marked partial.')),
}


@role_required(PLANNER)
def item_action(request, pk):
    """Planner buttons on the order page: pause, resume, mark partial (per box) or cancel the item."""
    item = get_object_or_404(OrderItem.objects.select_related('order'), pk=pk)
    back = redirect('order_detail', job_number=item.order.job_number)
    if request.method != 'POST':
        return back
    action, reason = request.POST.get('action', ''), request.POST.get('reason', '')
    try:
        if action == 'cancel':
            cancel_item(request.user, item, reason)
            messages.success(request, _('Item %(item)s cancelled.') % {'item': item.item_code})
        elif action == 'ship':
            ship_item(request.user, item)
            messages.success(request, _('Item %(item)s marked as shipped.') % {'item': item.item_code})
        elif action in STEP_ACTIONS:
            box = request.POST.get('box', '')
            assignment = get_object_or_404(TagAssignment.objects.select_related('tag', 'box', 'item'), pk=box if box.isdigit() else 0, item=item, end_at__isnull=True)
            event_type, done = STEP_ACTIONS[action]
            record_step_state(request.user, assignment, event_type, reason)
            messages.success(request, done)
        else:
            raise Http404
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return back
