from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.db.models import Count, F, Q
from django.utils.translation import gettext

from orders.models import OrderItem, Process

from .models import Event, Machine, ScannerDevice, TagAssignment, Worker
from .services import resolve_tag
from .status import box_captions


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


class CorrectEventForm(forms.Form):
    """Void one event, optionally replacing it with a manual event of the same time."""
    reason = forms.CharField(label=_('Reason'), widget=forms.Textarea(attrs={'rows': 3}))
    mode = forms.ChoiceField(label=_('Correction'), widget=forms.RadioSelect, initial='void',
                             choices=[('void', _('Void only')), ('replace', _('Void and replace'))])
    replacement = forms.ChoiceField(label=_('Replace with'), required=False)

    def __init__(self, *args, allow_replace=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_replace:
            self.fields['mode'].choices = [('void', _('Void only'))]
        # One option per active process and event type valid for its kind
        self.processes = {str(p.pk): p for p in Process.objects.filter(active=True).order_by('kind', 'name')}
        self.fields['replacement'].choices = [('', '---------')] + [
            (f'{p.pk}:{t}', f'{Event.Type(t).label} - {p}')
            for p in self.processes.values() for t, kind in Event.REQUIRED_KIND.items() if kind == p.kind
        ]

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'replace':
            if not data.get('replacement'):
                self.add_error('replacement', _('Choose the replacement event.'))
            else:
                pk, event_type = data['replacement'].split(':')
                data['replacement'] = {'process': self.processes[pk], 'event_type': event_type}
        else:
            data['replacement'] = None
        return data


class ResultForm(forms.Form):
    """Quantity done and defects at one step of an item, for one of its linked boxes."""
    box = forms.ChoiceField(label=_('Tag'))
    process = forms.ModelChoiceField(label=_('Process'), queryset=Process.objects.none())
    worker = forms.ModelChoiceField(label=_('Worker'), queryset=Worker.objects.filter(active=True), required=False)
    machine = forms.ModelChoiceField(label=_('Machine'), queryset=Machine.objects.filter(active=True), required=False)
    quantity = forms.IntegerField(label=_('Quantity done'), min_value=1)
    defects = forms.IntegerField(label=_('Defects'), min_value=0, initial=0, required=False)

    def __init__(self, item, *args, **kwargs):
        kwargs.setdefault('prefix', f'result{item.pk}')
        super().__init__(*args, **kwargs)
        self.item = item
        self.assignments = {str(a.pk): a for a in item.tagassignment_set.filter(end_at__isnull=True).select_related('tag', 'item')}
        captions = box_captions(list(self.assignments.values()))
        self.fields['box'].choices = [(pk, f'{a.tag} ({captions[a.pk]})' if captions[a.pk] else str(a.tag)) for pk, a in self.assignments.items()]
        self.fields['process'].queryset = Process.objects.filter(pk__in=item.steps.values('process')).order_by('name')
        self.fields['quantity'].initial = item.quantity

    def clean(self):
        data = super().clean()
        if data.get('box') in self.assignments:
            data['assignment'] = self.assignments[data['box']]
        return data
