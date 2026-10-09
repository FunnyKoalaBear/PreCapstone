from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from .forms import OrderForm


@staff_member_required
def order_create(request):
    form = OrderForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        order = form.save()
        messages.success(request, _('Order %(job)s created.') % {'job': order.job_number})
        return redirect('order_create')
    return render(request, 'orders/order_form.html', {'form': form})
