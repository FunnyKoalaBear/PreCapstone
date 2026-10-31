from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from . import accounts, feedback
from .forms import ROLE_LABELS, AddUserForm, EditUserForm
from .models import Profile
from .permissions import PLANNER, role_required
from .signals import SESSION_KEY


def _target(request, pk):
    return get_object_or_404(accounts.visible_users(request.user), pk=pk)  # hidden users are 404


def _password_page(request, user, password):
    return render(request, 'core/user_password.html', {'target': user, 'password': password})


def _set_active(actor, target, active, undo=False):
    accounts.update_user(actor, target, target.first_name, accounts.current_role(target), active, undo)
    return _('User %(name)s updated.') % {'name': target.username}


feedback.register('user', PLANNER, lambda actor, pk: accounts.visible_users(actor).filter(pk=pk).first(), _set_active)


@role_required(PLANNER)
def user_list(request):
    q = request.GET.get('q', '').strip()
    users = accounts.visible_users(request.user).order_by('username')
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q))
    rows = [(u, ROLE_LABELS.get(accounts.current_role(u), '-')) for u in users]
    return render(request, 'core/user_list.html', {'rows': rows, 'q': q})


@role_required(PLANNER)
def user_add(request):
    form = AddUserForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        user, password = accounts.create_user(request.user, d['username'], d['name'], d['role'])
        return _password_page(request, user, password)  # shown once, never redirected
    return render(request, 'core/user_form.html', {'form': form})


@role_required(PLANNER)
def user_edit(request, pk):
    target = _target(request, pk)
    form = EditUserForm(request.user, target, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        only_active = d['name'] == target.first_name and d['role'] == accounts.current_role(target) and d['is_active'] != target.is_active
        accounts.update_user(request.user, target, d['name'], d['role'], d['is_active'])
        text = _('User %(name)s updated.') % {'name': target.username}
        if only_active:  # a plain Active toggle gets an Undo; role or name edits do not
            feedback.toggled(request, 'user', target, reverse('user_list'), text)
        else:
            messages.success(request, text)
        return redirect('user_list')
    return render(request, 'core/user_form.html', {'form': form, 'target': target})


@role_required(PLANNER)
def user_reset(request, pk):
    target = _target(request, pk)
    if request.method == 'POST':
        return _password_page(request, target, accounts.reset_password(request.user, target))
    return render(request, 'core/user_reset.html', {'target': target})


class PasswordChange(SuccessMessageMixin, PasswordChangeView):
    success_url = reverse_lazy('home')
    success_message = gettext_lazy('Your password was changed.')

    def form_valid(self, form):
        response = super().form_valid(form)
        Profile.objects.filter(user=self.request.user).update(must_change_password=False)
        self.request.session[SESSION_KEY] = False
        return response
