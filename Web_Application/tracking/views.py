from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from orders.models import OrderItem

from .forms import AssignTagForm, PickTagForm, SimulateScanForm
from .models import Box, Event, Tag, TagAssignment
from .overview import active_boxes
from .priority import TIER_LABELS, TIERS, priority
from .services import assign_tag, record_event, release_assignment, replace_tag, resolve_tag
from .status import AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, current_row, item_status, next_action


def _next_step(uid):
    """What the simulate page offers for a tag: unassigned, finished, or the single next action."""
    a = resolve_tag(uid, timezone.now())
    if a is None:
        return {'state': 'unassigned'}
    st = item_status(a.item)
    box = next(b for b in st.boxes if b.assignment and b.assignment.box_id == a.box_id)
    todo = next_action(box)
    info = {'assignment': a, 'status': box}
    if todo is None:
        return {'state': 'finished', **info}
    etype, process = todo
    return {'state': 'next', 'value': f'{process.pk}:{etype}', 'label': f'{Event.Type(etype).label} - {process}', **info}


@staff_member_required
def simulate(request):
    form = SimulateScanForm(request.POST or None, initial={'tag_uid': request.GET.get('tag', ''), 'device': request.GET.get('device')})
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


@staff_member_required
def tag_manage(request):
    form = AssignTagForm(request.POST if request.POST.get('action') == 'assign' else None)
    if request.method == 'POST':
        if request.POST.get('action') == 'release':
            pk = request.POST.get('assignment', '')
            if not pk.isdigit():
                raise Http404
            a = release_assignment(get_object_or_404(TagAssignment, pk=pk, end_at__isnull=True))
            messages.success(request, _('Tag %(tag)s released from box %(box)s.') % {'tag': a.tag, 'box': a.box})
            return redirect('tag_manage')
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
            return redirect('tag_manage')
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
    q = request.GET.get('q', '').strip().lower()
    if q:
        hay = lambda a: [a.item.item_code, a.item.order.job_number, a.item.part_name, a.item.order.customer.name, a.box.label, a.tag.uid]
        open_ = [a for a in open_ if any(q in h.lower() for h in hay(a))]
    return render(request, 'tracking/tag_manage.html', {
        'form': form,
        'open': open_, 'q': q,
        'history': assignments.filter(end_at__isnull=False).order_by('-end_at')[:20],
        'tag_uids': Tag.objects.filter(active=True).values_list('uid', flat=True),
        'box_labels': Box.objects.filter(active=True).values_list('label', flat=True),
    })


@staff_member_required
def priorities(request):
    rows = []
    for item in OrderItem.objects.select_related('order__customer').order_by('order__due_date'):
        st = item_status(item)
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
            })
    rows.sort(key=lambda r: (TIERS.index(r['tier']), r['slack']))
    counts = {t: sum(r['tier'] == t for r in rows) for t in TIERS}
    tier = request.GET.get('tier', '')
    if tier in TIERS:
        rows = [r for r in rows if r['tier'] == tier]
    return render(request, 'tracking/priorities.html', {
        'rows': rows, 'tier': tier, 'total': sum(counts.values()),
        'tiers': [(t, t.lower(), TIER_LABELS[t], counts[t]) for t in TIERS],
    })


BOX_STATUSES = {
    'in_progress': (IN_PROGRESS, gettext_lazy('In progress')), 'waiting': (WAITING, gettext_lazy('Waiting')),
    'at_vendor': (AT_VENDOR, gettext_lazy('At vendor')), 'not_started': (NOT_STARTED, gettext_lazy('Not started')),
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


@staff_member_required
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
