from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

OPERATOR, PLANNER, ADMIN = 'Operator', 'Planner', 'Admin'
ROLES = [OPERATOR, PLANNER, ADMIN]


def role_level(user):
    """Index of the user's highest role in ROLES (-1 = none); superusers are Admin."""
    if not user.is_active:
        return -1
    if user.is_superuser:
        return len(ROLES) - 1
    names = set(user.groups.filter(name__in=ROLES).values_list('name', flat=True))
    return max((ROLES.index(n) for n in names), default=-1)


def has_role(user, *roles):
    """Roles are cumulative: a Planner also passes for Operator pages."""
    return role_level(user) >= min(ROLES.index(r) for r in roles)


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_role(request.user, *roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
