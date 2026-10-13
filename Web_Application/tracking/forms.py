from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.db.models import Count, F, Q
from django.utils.translation import gettext

from orders.models import OrderItem, Process

from .models import Event, ScannerDevice, TagAssignment
from .services import resolve_tag


class SimulateScanForm(forms.Form):
    tag_uid = forms.CharField(label=_('Tag UID'), max_length=100)
    device = forms.ModelChoiceField(label=_('Device'), queryset=ScannerDevice.objects.filter(active=True))
    action = forms.ChoiceField(label=_('What happened'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One option per process and event type that is valid for its kind
        # Active processes, plus any inactive one in the route of the posted tag's item
        uid = self.data.get('tag_uid', '') if self.is_bound else ''
        a = resolve_tag(uid, timezone.now()) if uid.strip() else None
        keep = Q(pk__in=a.item.steps.values('process')) if a else Q()
        self.processes = {str(p.pk): p for p in Process.objects.filter(Q(active=True) | keep).order_by('kind', 'name')}
        self.fields['action'].choices = [
            (f'{p.pk}:{t}', f'{p} - {Event.Type(t).label}')
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


class PickTagForm(forms.Form):
    """Device and tag picker; submitting it (GET) shows the next action for that tag."""
    device = forms.ModelChoiceField(label=_('Device'), queryset=ScannerDevice.objects.filter(active=True))
    tag = forms.CharField(
        label=_('Tag'), required=False,
        widget=forms.TextInput(attrs={'list': 'tag-picks', 'autocomplete': 'off', 'placeholder': _('Search or pick a tag')}),
    )


class AssignTagForm(forms.Form):
    item = forms.ModelChoiceField(label=_('Item'), queryset=OrderItem.objects.none())
    box_label = forms.CharField(
        label=_('Box label'), max_length=100, widget=forms.TextInput(attrs={'list': 'box-labels', 'autocomplete': 'off'}),
    )
    tag_uid = forms.CharField(label=_('Tag UID'), max_length=100)

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
