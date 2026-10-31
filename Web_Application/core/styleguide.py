from django import forms
from django.contrib import messages
from django.shortcuts import redirect, render

from .permissions import ADMIN, role_required

PILLS = [('complete', 'Complete'), ('in-progress', 'In progress'), ('waiting', 'Waiting'), ('at-vendor', 'At vendor'), ('paused', 'Paused'),
         ('not-started', 'Not started'), ('partial', 'Partial'), ('shipped', 'Shipped'), ('cancelled', 'Cancelled'), ('overdue', 'Overdue'),
         ('risk-critical', 'Critical'), ('risk-watch', 'Watch'), ('risk-on-track', 'On track')]
TYPE_SCALE = [('text-2xl', '1.5rem'), ('text-xl', '1.2rem'), ('text-lg', '1.05rem'), ('text-base', '1rem'), ('text-table', '0.9375rem'), ('text-sm', '0.8125rem')]


class SampleForm(forms.Form):
    name = forms.CharField(label='Name', max_length=20, help_text='Up to 20 characters.')
    quantity = forms.IntegerField(label='Quantity', min_value=1)
    note = forms.CharField(label='Note', required=False, widget=forms.Textarea(attrs={'rows': 2}))
    active = forms.BooleanField(label='Active', required=False)


@role_required(ADMIN)
def styleguide(request):
    """Look-and-feel check page for both themes. English only on purpose: it is a developer tool, exempt from the translation rule."""
    if request.method == 'POST':
        messages.info(request, 'The sample action was confirmed. Nothing was changed.')
        return redirect('styleguide')
    form = SampleForm({'name': '', 'quantity': '-2', 'active': 'on'})  # bound with bad data so the errors show
    form.is_valid()
    rows = [('2611-001-01', 'Bracket', 120, '2026-11-20', 'complete', 'Complete'), ('2611-002-01', 'Shaft', 8, '2026-11-24', 'in-progress', 'In progress'),
            ('2611-003-01', 'Flange', 1500, '2026-11-02', 'overdue', 'Overdue'), ('2611-004-01', 'Plate', 43, '2026-12-01', 'cancelled', 'Cancelled')]
    return render(request, 'core/styleguide.html', {'form': form, 'pills': PILLS, 'type_scale': TYPE_SCALE, 'rows': rows})
