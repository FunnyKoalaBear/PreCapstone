from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from . import accounts
from .permissions import ADMIN, OPERATOR, PLANNER

ROLE_LABELS = {OPERATOR: _('Operator'), PLANNER: _('Planner'), ADMIN: _('Admin')}


class _RoleForm(forms.Form):
    name = forms.CharField(label=_('Name'), max_length=150, required=False)
    role = forms.ChoiceField(label=_('Role'))

    def __init__(self, actor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields['role'].choices = [(r, ROLE_LABELS[r]) for r in accounts.role_choices(actor)]


class AddUserForm(_RoleForm):
    username = forms.CharField(label=_('Username'), max_length=150)
    field_order = ['username', 'name', 'role']

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        User._meta.get_field('username').run_validators(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(_('A user with that username already exists.'))
        return username


class EditUserForm(_RoleForm):
    is_active = forms.BooleanField(label=_('Active'), required=False)

    def __init__(self, actor, target, *args, **kwargs):
        super().__init__(actor, *args, initial={
            'name': target.first_name, 'role': accounts.current_role(target), 'is_active': target.is_active}, **kwargs)
        self.target = target
        if target.pk == actor.pk:  # disabled fields ignore posted values
            self.fields['role'].disabled = self.fields['is_active'].disabled = True

    def clean(self):
        data = super().clean()
        if not self.errors:
            try:
                accounts.check_change(self.actor, self.target, data['role'], data['is_active'])
            except ValidationError as e:
                self.add_error(None, e)
        return data
