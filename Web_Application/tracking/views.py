from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from orders.models import OrderItem

from .forms import AssignTagForm, SimulateScanForm
from .models import Box, Event, Tag, TagAssignment
from .priority import TIER_LABELS, TIERS, priority
from .services import assign_tag, record_event, release_assignment
from .status import COMPLETE, NOT_STARTED, current_row, item_status


@staff_member_required
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
    return render(request, 'tracking/simulate.html', {
        'form': form,
        'events': Event.objects.select_related('device', 'process').order_by('-occurred_at', '-pk')[:10],
        'tag_uids': Tag.objects.filter(active=True).values_list('uid', flat=True),
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
    assignments = TagAssignment.objects.select_related('tag', 'box', 'item__order')
    open_ = list(assignments.filter(end_at__isnull=True).order_by('item__order__job_number', 'item__item_no', 'start_at'))
    seen = {}
    for a in open_:  # position of the box within its item
        seen[a.item_id] = a.nth = seen.get(a.item_id, 0) + 1
    return render(request, 'tracking/tag_manage.html', {
        'form': form,
        'open': open_,
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
