from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from .forms import SimulateScanForm
from .models import Event, Tag
from .services import record_event
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
