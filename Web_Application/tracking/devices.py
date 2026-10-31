from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from core import audit
from core.permissions import PLANNER, role_required
from core.rules import silent_after

from .models import ScannerDevice, generate_api_key


class DeviceForm(forms.ModelForm):
    class Meta:
        model = ScannerDevice
        fields = ['name', 'location']

    def clean_name(self):
        name = self.cleaned_data['name']
        if ScannerDevice.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('The name "%(name)s" already exists.') % {'name': name})
        return name


def _key_page(request, device, key_is_new):
    """The only place a key is ever rendered; it is not stored anywhere but the device row."""
    return render(request, 'tracking/device_key.html', {'device': device, 'api_key': device.api_key, 'new': key_is_new})


@role_required(PLANNER)
def device_list(request):
    if request.method == 'POST':
        device = get_object_or_404(ScannerDevice.objects.annotate(events=Count('event')), pk=request.POST.get('device', 0))
        action = request.POST.get('action')
        if action == 'toggle':
            device.active = not device.active
            device.save(update_fields=['active'])
            audit.log(request.user, device, CHANGE, 'Reactivated.' if device.active else 'Deactivated.')
            messages.success(request, (_('Reactivated "%(name)s".') if device.active else _('Deactivated "%(name)s".')) % {'name': device.name})
        elif action == 'delete':
            if device.events or device.last_seen:
                messages.error(request, _('"%(name)s" has been used and cannot be deleted. Deactivate it instead.') % {'name': device.name})
            else:
                audit.log(request.user, device, DELETION, 'Deleted.')  # logged first: the row is gone afterwards
                device.delete()
                messages.success(request, _('Deleted "%(name)s".') % {'name': device.name})
        return redirect('device_list')
    cutoff = timezone.now() - silent_after()
    devices = list(ScannerDevice.objects.annotate(events=Count('event')).order_by('name'))
    for d in devices:
        d.silent = d.active and (d.last_seen is None or d.last_seen < cutoff)
        d.deletable = not d.events and not d.last_seen
    return render(request, 'tracking/device_list.html', {'devices': devices, 'tab': 'devices'})


@role_required(PLANNER)
@never_cache
def device_add(request):
    form = DeviceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        device = form.save()
        audit.log(request.user, device, ADDITION, 'Added.')  # never the key
        return _key_page(request, device, True)
    return render(request, 'tracking/device_form.html', {'form': form, 'tab': 'devices'})


@role_required(PLANNER)
def device_edit(request, pk):
    device = get_object_or_404(ScannerDevice, pk=pk)
    form = DeviceForm(request.POST or None, instance=device)
    if request.method == 'POST' and form.is_valid():
        form.save()
        audit.log(request.user, device, CHANGE, 'Edited.')
        messages.success(request, _('Saved "%(name)s".') % {'name': device.name})
        return redirect('device_list')
    return render(request, 'tracking/device_form.html', {'form': form, 'device': device, 'tab': 'devices'})


@role_required(PLANNER)
@never_cache
def device_regenerate(request, pk):
    device = get_object_or_404(ScannerDevice, pk=pk)
    if request.method == 'POST':
        device.api_key = generate_api_key()
        device.save(update_fields=['api_key'])
        audit.log(request.user, device, CHANGE, 'API key regenerated.')  # never the key
        return _key_page(request, device, False)
    return render(request, 'tracking/device_regenerate.html', {'device': device, 'tab': 'devices'})
