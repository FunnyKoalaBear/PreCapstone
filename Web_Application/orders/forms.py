from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from tracking.status import events_for_item

from .models import Customer, Order, OrderItem, Process, RouteStep
from .services import create_customer, find_customer, get_or_create_customer, normalize_name


class CustomerForm(forms.Form):
    name = forms.CharField(label=capfirst(_('name')), max_length=200)
    contact = forms.CharField(label=capfirst(_('contact')), max_length=200, required=False)

    def clean_name(self):
        name = normalize_name(self.cleaned_data['name'])
        if find_customer(name):
            raise ValidationError(_('A customer with this name already exists.'))
        return name

    def save(self):
        return create_customer(self.cleaned_data['name'], self.cleaned_data['contact'])


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
        fields = ['part_name', 'quantity', 'box_count', 'drawing_file']
        labels = {'box_count': _('Boxes')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['box_count'].required = False
        self.fields['box_count'].widget.attrs['min'] = 1
        item = self.instance
        # New work offers active processes; an existing route keeps its own steps selectable
        keep = Q(pk__in=item.steps.values('process')) if item.pk else Q()
        self.fields['route'].choices = [
            (p.pk, f'{p} ({p.get_kind_display()})') for p in Process.objects.filter(Q(active=True) | keep).order_by('kind', 'name')
        ]
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

    def clean_box_count(self):
        # A missing value means one box; never fewer than the boxes already linked
        value = self.cleaned_data.get('box_count')
        value = 1 if value is None else value
        if self.instance.pk and value < self.linked_boxes:
            raise ValidationError(_('Box count cannot be lower than the %(n)s boxes already linked.') % {'n': self.linked_boxes})
        return value

    @property
    def route_selected(self):
        """Submitted or saved process ids (as text), or one empty slot, for the template."""
        return [str(v) for v in self['route'].value() or ['']]

    @property
    def route_steps(self):
        return [s.process for s in self.instance.steps.select_related('process')]

    def save(self, commit=True):
        item = super().save(commit)
        if commit and not self.locked:
            route = self.cleaned_data['route']
            if list(item.steps.values_list('process_id', flat=True)) != route:
                item.steps.all().delete()
                RouteStep.objects.bulk_create(RouteStep(item=item, sequence=i, process_id=pk) for i, pk in enumerate(route, 1))
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
