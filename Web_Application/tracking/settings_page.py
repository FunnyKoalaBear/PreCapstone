import re

from django import forms
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from core import audit
from core.permissions import PLANNER, role_required

from .models import DefectCause, Machine, Worker

# key, label, model: all three share NamedResource, so one view, form and template serve them
TABS = [
    ('workers', gettext_lazy('Workers'), Worker),
    ('machines', gettext_lazy('Machines'), Machine),
    ('causes', gettext_lazy('Defect causes'), DefectCause),
]
NAME_MAX = 100


class ResourceForm(forms.Form):
    name = forms.CharField(label=gettext_lazy('Name'), max_length=NAME_MAX)
    name_ja = forms.CharField(label=gettext_lazy('Japanese name'), max_length=NAME_MAX, required=False)

    def __init__(self, model, *args, pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.model, self.pk = model, pk

    def clean_name(self):
        name = self.cleaned_data['name']  # CharField already strips
        if self.model.objects.filter(name__iexact=name).exclude(pk=self.pk).exists():
            raise forms.ValidationError(_('The name "%(name)s" already exists.') % {'name': name})
        return name


def _tab_url(key):
    return f'{reverse("settings")}?tab={key}'


def _errors(form):
    return ' '.join(m for errs in form.errors.values() for m in errs)


def _add(request, model, back):
    form = ResourceForm(model, request.POST)
    if not form.is_valid():
        messages.error(request, _errors(form))
        return
    obj = model.objects.create(**form.cleaned_data)
    audit.log(request.user, obj, ADDITION, 'Added.')
    messages.success(request, _('Added "%(name)s".') % {'name': obj.name})


def _rename(request, model, obj):
    form = ResourceForm(model, request.POST, pk=obj.pk)
    if not form.is_valid():
        messages.error(request, _errors(form))
        return
    obj.name, obj.name_ja = form.cleaned_data['name'], form.cleaned_data['name_ja']
    obj.save()
    audit.log(request.user, obj, CHANGE, 'Renamed.')
    messages.success(request, _('Saved "%(name)s".') % {'name': obj.name})


def _toggle(request, obj):
    obj.active = not obj.active
    obj.save()
    audit.log(request.user, obj, CHANGE, 'Reactivated.' if obj.active else 'Deactivated.')
    messages.success(request, (_('Reactivated "%(name)s".') if obj.active else _('Deactivated "%(name)s".')) % {'name': obj.name})


def _delete(request, model, obj):
    used = model.objects.filter(pk=obj.pk).aggregate(n=Count('event'))['n']
    if used:
        messages.error(request, _('"%(name)s" is used in %(n)d record(s) and cannot be deleted.') % {'name': obj.name, 'n': used})
        return
    audit.log(request.user, obj, DELETION, 'Deleted.')  # logged first: the row is gone afterwards
    obj.delete()
    messages.success(request, _('Deleted "%(name)s".') % {'name': obj.name})


def _paste(request, model):
    """One name per line, or 'name, 日本語名'; names already listed (or repeated in the paste) are skipped."""
    seen = {n.lower() for n in model.objects.values_list('name', flat=True)}
    ids, skipped = [], 0
    for line in request.POST.get('lines', '').splitlines():
        if not line.strip():
            continue
        name, *rest = (p.strip() for p in re.split('[,，]', line, maxsplit=1))
        name_ja = rest[0] if rest else ''
        if not name or len(name) > NAME_MAX or len(name_ja) > NAME_MAX or name.lower() in seen:
            skipped += 1
            continue
        seen.add(name.lower())
        ids.append(model.objects.create(name=name, name_ja=name_ja).pk)
    if ids:
        audit.log(request.user, model.objects.filter(pk__in=ids), ADDITION, 'Added from a pasted list.')
    messages.success(request, _('Added %(added)d, skipped %(skipped)d (already listed or too long).') % {'added': len(ids), 'skipped': skipped})


@role_required(PLANNER)
def settings_page(request):
    """Settings: one URL, three tabs (?tab=workers|machines|causes) over the same list screen."""
    key = request.GET.get('tab')
    key, label, model = next((t for t in TABS if t[0] == key), TABS[0])
    back = _tab_url(key)
    if request.method == 'POST':
        action, pk = request.POST.get('action'), request.POST.get('pk', '')
        obj = model.objects.filter(pk=pk).first() if pk.isdigit() else None
        with transaction.atomic():
            if action == 'add':
                _add(request, model, back)
            elif action == 'paste':
                _paste(request, model)
            elif obj and action == 'rename':
                _rename(request, model, obj)
            elif obj and action == 'toggle':
                _toggle(request, obj)
            elif obj and action == 'delete':
                _delete(request, model, obj)
        return redirect(back)
    q = request.GET.get('q', '').strip()
    rows = model.objects.annotate(used=Count('event')).order_by('name')
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(name_ja__icontains=q))
    tabs = [{'key': k, 'label': lab, 'url': _tab_url(k)} for k, lab, _m in TABS]
    return render(request, 'tracking/settings.html', {'tab': key, 'tabs': tabs, 'label': label, 'rows': rows, 'q': q, 'back': back})
