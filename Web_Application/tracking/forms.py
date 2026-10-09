from django import forms
from django.utils.translation import gettext_lazy as _

from orders.models import Process

from .models import Event, ScannerDevice


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
