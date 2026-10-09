from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from .forms import AssignTagForm, SimulateScanForm
from .models import Box, Event, Tag, TagAssignment
from .services import assign_tag, record_event, release_assignment
from .status import item_status


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
    return render(request, 'tracking/tag_manage.html', {
        'form': form,
        'open': assignments.filter(end_at__isnull=True).order_by('-start_at'),
        'history': assignments.filter(end_at__isnull=False).order_by('-end_at')[:20],
        'tag_uids': Tag.objects.filter(active=True).values_list('uid', flat=True),
        'box_labels': Box.objects.filter(active=True).values_list('label', flat=True),
    })
