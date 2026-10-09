from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from .forms import OrderForm, OrderItemFormSet


@staff_member_required
def order_create(request):
    data, files = request.POST or None, request.FILES or None
    form, formset = OrderForm(data), OrderItemFormSet(data, files)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            order = form.save()
            formset.instance = order
            formset.save()
        messages.success(request, _('Order %(job)s created.') % {'job': order.job_number})
        return redirect('order_create')
    return render(request, 'orders/order_form.html', {'form': form, 'formset': formset})
