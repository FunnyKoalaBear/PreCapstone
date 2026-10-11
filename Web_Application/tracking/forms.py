from django import forms
from django.utils.translation import gettext_lazy as _

from django.db.models import Count, F, Q
from django.utils.translation import gettext

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
        # Only items that still have a free box slot
        field = self.fields['item']
        field.queryset = (
            OrderItem.objects.annotate(linked=Count('tagassignment', filter=Q(tagassignment__end_at__isnull=True)))
            .filter(linked__lt=F('box_count')).select_related('order').order_by('order__job_number', 'item_no')
        )
        field.label_from_instance = lambda i: gettext('%(code)s - %(part)s (%(n)s of %(total)s boxes linked)') % {
            'code': i.item_code, 'part': i.part_name, 'n': i.linked, 'total': i.box_count,
        }
