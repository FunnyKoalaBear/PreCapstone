import secrets

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import Profile
from .permissions import ADMIN, OPERATOR, PLANNER, ROLES, role_level

ASSIGNABLE = [OPERATOR, PLANNER]
# No I, O, l, o, 0, 1: they look alike
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'


def temp_password():
    return ''.join(secrets.choice(ALPHABET) for _i in range(12))


def current_role(user):
    """Highest role name, or None; reads user.groups.all() so a prefetch avoids one query per row."""
    if user.is_superuser:
        return ADMIN
    names = {g.name for g in user.groups.all()}
    return max((r for r in ROLES if r in names), key=ROLES.index, default=None)


def is_admin(user):
    return current_role(user) == ADMIN


def visible_users(actor):
    """Planners never see Admin users; Admins see everyone."""
    users = User.objects.prefetch_related('groups')
    if role_level(actor) == ROLES.index(ADMIN):
        return users
    return users.exclude(is_superuser=True).exclude(groups__name=ADMIN)


def role_choices(actor):
    return ROLES if role_level(actor) == ROLES.index(ADMIN) else ASSIGNABLE


def _is_last_planner(target):
    others = User.objects.filter(is_active=True, groups__name=PLANNER).exclude(pk=target.pk).exclude(is_superuser=True).exclude(groups__name=ADMIN)
    return target.is_active and current_role(target) == PLANNER and not others.exists()


def check_change(actor, target, role, active):
    if role not in role_choices(actor):
        raise ValidationError(_('You cannot assign this role.'))
    if target is None:
        return
    if target.pk == actor.pk and (role != current_role(target) or not active):
        raise ValidationError(_('You cannot change your own role or deactivate yourself.'))
    if _is_last_planner(target) and (role != PLANNER or not active):
        raise ValidationError(_('The last active Planner cannot be deactivated or demoted.'))


def _log(actor, target, flag, message):
    LogEntry.objects.log_actions(actor.pk, User.objects.filter(pk=target.pk), flag, message)


@transaction.atomic
def create_user(actor, username, name, role):
    """Create the user with a temporary password; returns (user, password)."""
    check_change(actor, None, role, True)
    password = temp_password()
    user = User.objects.create_user(username, first_name=name, password=password)
    user.groups.set([Group.objects.get(name=role)])
    Profile.objects.filter(user=user).update(must_change_password=True)
    _log(actor, user, ADDITION, f'Created as {role}.')
    return user, password


@transaction.atomic
def update_user(actor, target, name, role, active):
    check_change(actor, target, role, active)
    changes = []
    if role != current_role(target):
        target.groups.set([Group.objects.get(name=role)])
        changes.append(f'role {role}')
    if active != target.is_active:
        changes.append('activated' if active else 'deactivated')
    if name != target.first_name:
        changes.append('name')
    target.first_name, target.is_active = name, active
    target.save()
    if changes:
        _log(actor, target, CHANGE, 'Changed: ' + ', '.join(changes) + '.')


@transaction.atomic
def reset_password(actor, target):
    password = temp_password()
    target.set_password(password)
    target.save()
    Profile.objects.filter(user=target).update(must_change_password=True)
    _log(actor, target, CHANGE, 'Password reset.')
    return password
