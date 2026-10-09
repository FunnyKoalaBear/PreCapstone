from django import forms
from django.forms import inlineformset_factory

from .models import Order, OrderItem


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['customer', 'customer_order_number', 'due_date', 'notes']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


OrderItemFormSet = inlineformset_factory(
    Order, OrderItem, fields=['part_name', 'quantity', 'drawing_file'],
    extra=0, can_delete=True, min_num=1, validate_min=True,
)
