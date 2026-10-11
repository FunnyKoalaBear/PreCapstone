from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from .models import Order, OrderItem, Process, RouteStep


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['customer', 'customer_order_number', 'due_date', 'notes']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


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
        self.fields['route'].choices = [
            (p.pk, f'{p.name} ({p.get_kind_display()})') for p in Process.objects.order_by('kind', 'name')
        ]

    def clean_box_count(self):
        # A missing value means one box
        value = self.cleaned_data.get('box_count')
        return 1 if value is None else value

    @property
    def route_selected(self):
        """Submitted process ids, or one empty slot, for the template."""
        return self['route'].value() or ['']

    def save(self, commit=True):
        item = super().save(commit)
        if commit:
            RouteStep.objects.bulk_create(
                RouteStep(item=item, sequence=i, process_id=pk) for i, pk in enumerate(self.cleaned_data['route'], 1)
            )
        return item


OrderItemFormSet = inlineformset_factory(
    Order, OrderItem, form=OrderItemForm, extra=0, can_delete=True, min_num=1, validate_min=True,
)
