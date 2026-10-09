from django import forms
from django.utils.translation import gettext_lazy as _

from orders.models import OrderItem, Process

from .models import Event, ScannerDevice, TagAssignment


class SimulateScanForm(forms.Form):
    tag_uid = forms.CharField(
        label=_('Tag UID'), max_length=100, widget=forms.TextInput(attrs={'list': 'tag-uids', 'autocomplete': 'off'}),
    )
    device = forms.ModelChoiceField(label=_('Device'), queryset=ScannerDevice.objects.filter(active=True))
    action = forms.ChoiceField(label=_('What happened'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One option per process and event type that is valid for its kind
        self.processes = {str(p.pk): p for p in Process.objects.order_by('kind', 'name')}
        self.fields['action'].choices = [
            (f'{p.pk}:{t}', f'{p.name} - {Event.Type(t).label}')
            for p in self.processes.values()
            for t, kind in Event.REQUIRED_KIND.items()
            if kind == p.kind
        ]

    def clean(self):
        data = super().clean()
        if data.get('action'):
            pk, event_type = data['action'].split(':')
            data['process'], data['event_type'] = self.processes[pk], event_type
        return data


class AssignTagForm(forms.Form):
    item = forms.ModelChoiceField(label=_('Item'), queryset=OrderItem.objects.none())
    box_label = forms.CharField(
        label=_('Box label'), max_length=100, widget=forms.TextInput(attrs={'list': 'box-labels', 'autocomplete': 'off'}),
    )
    tag_uid = forms.CharField(
        label=_('Tag UID'), max_length=100, widget=forms.TextInput(attrs={'list': 'tag-uids', 'autocomplete': 'off'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only items without an open assignment
        busy = TagAssignment.objects.filter(end_at__isnull=True).values('item_id')
        field = self.fields['item']
        field.queryset = OrderItem.objects.exclude(pk__in=busy).select_related('order').order_by('order__job_number', 'item_no')
        field.label_from_instance = lambda i: f'{i.item_code} {i.part_name}'
