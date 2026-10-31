from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from core import audit
from tracking.models import Event

from .models import Process, Vendor


class ProcessForm(forms.ModelForm):
    class Meta:
        model = Process
        fields = ['name', 'name_ja', 'kind', 'default_vendor', 'vendor_lead_days', 'stagnation_hours']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = self.instance
        self.fields['default_vendor'].queryset = Vendor.objects.filter(Q(active=True) | Q(pk=p.default_vendor_id)).order_by('code')
        if p.pk and is_locked(p):  # event types depend on the kind
            self.fields['kind'].disabled = True
            self.fields['kind'].help_text = _('The kind cannot be changed because this process is used in a route or an event.')

    def clean_name(self):
        name = self.cleaned_data['name']
        if Process.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('The name "%(name)s" already exists.') % {'name': name})
        return name


def is_locked(process):
    return process.routestep_set.exists() or Event.objects.filter(process=process).exists()


def _tab_processes(request):
    q, back = request.GET.get('q', '').strip(), reverse('order_list') + '?tab=processes'
    target = None
    if request.method == 'POST':
        pk, action = request.POST.get('process', ''), request.POST.get('action')
        target = get_object_or_404(Process, pk=pk) if pk.isdigit() else None
        if target and action in ('deactivate', 'reactivate'):
            target.active = action == 'reactivate'
            target.save(update_fields=['active'])
            audit.log(request.user, target, CHANGE, 'Reactivated.' if target.active else 'Deactivated.')
            messages.success(request, (_('Reactivated "%(name)s".') if target.active else _('Deactivated "%(name)s".')) % {'name': target.name})
            return redirect(back)
        if target and action == 'delete':
            if is_locked(target):
                messages.error(request, _('"%(name)s" is used in a route or an event and cannot be deleted.') % {'name': target.name})
            else:
                audit.log(request.user, target, DELETION, 'Deleted.')  # logged first: the row is gone afterwards
                target.delete()
                messages.success(request, _('Deleted "%(name)s".') % {'name': target.name})
            return redirect(back)
        form = ProcessForm(request.POST, instance=target)
        if form.is_valid():
            process = form.save()
            audit.log(request.user, process, CHANGE if target else ADDITION, 'Edited.' if target else 'Added.')
            messages.success(request, (_('Saved "%(name)s".') if target else _('Added "%(name)s".')) % {'name': process.name})
            return redirect(back)
    else:
        edit = request.GET.get('edit', '')
        target = Process.objects.filter(pk=edit).first() if edit.isdigit() else None
        form = ProcessForm(instance=target)
    rows = Process.objects.select_related('default_vendor').annotate(routes=Count('routestep', distinct=True), events=Count('event', distinct=True)).order_by('kind', 'name')
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(name_ja__icontains=q))
    return {'processes': rows, 'q': q, 'form': form, 'editing': target, 'back': back}
