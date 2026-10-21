from itertools import zip_longest

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.dateparse import parse_date
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from tracking.status import events_for_item

from .models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from .services import create_customer, find_customer, get_or_create_customer, normalize_name, update_customer


class CustomerForm(forms.Form):
    name = forms.CharField(label=capfirst(_('name')), max_length=200)
    contact = forms.CharField(label=capfirst(_('contact')), max_length=200, required=False)

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance  # set when editing: its own name stays allowed
        if instance and not self.is_bound:
            self.initial = {'name': instance.name, 'contact': instance.contact}

    def clean_name(self):
        name = normalize_name(self.cleaned_data['name'])
        if find_customer(name, exclude=self.instance):
            raise ValidationError(_('A customer with this name already exists.'))
        return name

    def save(self):
        d = self.cleaned_data
        if self.instance:
            return update_customer(self.instance, d['name'], d['contact'])
        return create_customer(d['name'], d['contact'])


class OrderForm(forms.ModelForm):
    new_customer = forms.CharField(label=_('New customer'), required=False, max_length=200)
    field_order = ['customer', 'new_customer']

    class Meta:
        model = Order
        fields = ['customer', 'customer_order_number', 'due_date', 'notes']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['customer'].required = False  # the typed name may stand in for it

    def clean(self):
        data = super().clean()
        name = data['new_customer'] = normalize_name(data.get('new_customer'))
        if name:
            data['customer'] = find_customer(name)  # None: created on save
        elif not data.get('customer') and 'customer' not in self.errors:
            self.add_error('customer', ValidationError(self.fields['customer'].error_messages['required'], code='required'))
        return data

    def save(self, commit=True):
        if self.cleaned_data['new_customer'] and not self.cleaned_data['customer']:
            self.instance.customer = get_or_create_customer(self.cleaned_data['new_customer'])
        return super().save(commit)


class RouteWidget(forms.SelectMultiple):
    def value_from_datadict(self, data, files, name):
        # Keep the submitted order, drop empty "Choose a process" selects
        return [v for v in data.getlist(name) if v]


class OrderItemForm(forms.ModelForm):
    route = forms.TypedMultipleChoiceField(
        label=_('Route'), coerce=int, choices=(), widget=RouteWidget,
        error_messages={'required': _('Add at least one step to the route.')},
    )

    class Meta:
        model = OrderItem
        fields = ['part_name', 'part_number', 'drawing_number', 'revision', 'quantity', 'material', 'box_count', 'drawing_file']
        labels = {'box_count': _('Boxes')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['box_count'].required = False
        self.fields['box_count'].widget.attrs['min'] = 1
        item = self.instance
        # New work offers active processes; an existing route keeps its own steps selectable
        keep = Q(pk__in=item.steps.values('process')) if item.pk else Q()
        processes = Process.objects.filter(Q(active=True) | keep).order_by('kind', 'name')
        self.fields['route'].choices = [(p.pk, f'{p} ({p.get_kind_display()})') for p in processes]
        self.process_options = [(p.pk, f'{p} ({p.get_kind_display()})', p.default_vendor_id or '') for p in processes]
        used = Q(pk__in=item.steps.values('vendor')) if item.pk else Q()  # a deactivated code stays on routes that already use it
        self.vendor_options = [(v.pk, str(v)) for v in Vendor.objects.filter(Q(active=True) | used)]
        self.route_extra = []
        self.locked = self.undeletable = False
        if item.pk:
            # Route is locked once the item has been scanned; posted route changes are ignored
            self.locked = events_for_item(item).exists()
            self.undeletable = self.locked or item.tagassignment_set.exists()
            self.linked_boxes = item.tagassignment_set.values('box').distinct().count()
            self.initial['route'] = list(item.steps.values_list('process_id', flat=True))
            self.fields['box_count'].widget.attrs['min'] = max(1, self.linked_boxes)
            if self.locked:
                self.fields['route'].required = False
                self.fields['route'].disabled = True

    def _submitted_rows(self):
        """(process, vendor, due date, slip) text tuples as posted, dropping steps without a process."""
        lists = [self.data.getlist(self.add_prefix(n)) for n in ('route', 'route_vendor', 'route_due', 'route_slip')]
        return [(r, v, d.strip(), s.strip()) for r, v, d, s in zip_longest(*lists, fillvalue='') if r]

    def has_changed(self):
        # The per-step extras are not form fields, so compare them with the saved route
        if super().has_changed():
            return True
        return not self.locked and self.instance.pk is not None and self._saved_rows() != self._submitted_rows()

    def _saved_rows(self):
        return [(str(s.process_id), str(s.vendor_id or ''), s.due_date.isoformat() if s.due_date else '', s.slip_number)
                for s in self.instance.steps.all()]

    def clean(self):
        data = super().clean()
        if not self.locked:
            rows, valid = self._submitted_rows(), {str(pk) for pk, _label in self.vendor_options}
            if any(v and v not in valid for _r, v, _d, _s in rows):
                self.add_error(None, ValidationError(_('Choose a valid vendor.')))
            dates = [parse_date(d) if d else None for _r, _v, d, _s in rows]
            if any(d and not parse_date(d) for _r, _v, d, _s in rows):
                self.add_error(None, ValidationError(_('Enter a valid due date.')))
            if any(len(s) > 50 for _r, _v, _d, s in rows):
                self.add_error(None, ValidationError(_('The slip number can have at most 50 characters.')))
            self.route_extra = [(int(v) if v in valid else None, d, s) for (_r, v, _d, s), d in zip(rows, dates)]
        return data

    def clean_box_count(self):
        # A missing value means one box; never fewer than the boxes already linked
        value = self.cleaned_data.get('box_count')
        value = 1 if value is None else value
        if self.instance.pk and value < self.linked_boxes:
            raise ValidationError(_('Box count cannot be lower than the %(n)s boxes already linked.') % {'n': self.linked_boxes})
        return value

    @property
    def route_rows(self):
        """Submitted or saved (process, vendor, due date, slip) text tuples, or one empty slot, for the template."""
        if self.is_bound and not self.locked:
            rows = self._submitted_rows()
        elif self.instance.pk:
            rows = self._saved_rows()
        else:
            rows = []
        return rows or [('', '', '', '')]

    @property
    def route_steps(self):
        return self.instance.steps.select_related('process__default_vendor', 'vendor')

    def save(self, commit=True):
        item = super().save(commit)
        if commit and not self.locked:
            route = self.cleaned_data['route']
            new = [(p, *extra) for p, extra in zip(route, self.route_extra)]
            if list(item.steps.values_list('process_id', 'vendor_id', 'due_date', 'slip_number')) != new:
                item.steps.all().delete()
                RouteStep.objects.bulk_create(
                    RouteStep(item=item, sequence=i, process_id=p, vendor_id=v, due_date=d, slip_number=s)
                    for i, (p, v, d, s) in enumerate(new, 1))
        return item


class BaseOrderItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        for form in self.forms:
            if form.instance.pk and self._should_delete_form(form) and form.undeletable:
                raise ValidationError(_('Item %(item)s cannot be deleted because it has tags or scans.') % {'item': form.instance.item_code})


OrderItemFormSet = inlineformset_factory(
    Order, OrderItem, form=OrderItemForm, formset=BaseOrderItemFormSet, extra=0, can_delete=True, min_num=1, validate_min=True,
)


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ['code', 'name', 'name_ja', 'kind', 'lead_days', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        kind = self.fields['kind']
        kind.required = False
        kind.choices = [('', _('Suggested from the code'))] + list(Vendor.Kind.choices)
        self.fields['code'].widget.attrs.update(inputmode='numeric', maxlength=5)

    def clean(self):
        data = super().clean()
        code = data.get('code', '')
        if not data.get('kind') and code.isdigit():
            data['kind'] = Vendor.Kind.INTERNAL if int(code) <= Vendor.RANGES[Vendor.Kind.INTERNAL][1] else Vendor.Kind.VENDOR
        return data
