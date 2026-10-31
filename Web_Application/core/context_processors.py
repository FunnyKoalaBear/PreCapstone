from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from . import accounts
from .forms import ROLE_LABELS
from .permissions import ADMIN, OPERATOR, PLANNER, ROLES, role_level

# (group, [(label, url name, icon, minimum role or None, url names that count as this item)])
NAV = [
    (_('Work'), [
        (_('Home'), 'home', 'home', None, {'home', 'boxes', 'matrix'}),
        (_('Orders'), 'order_list', 'orders', OPERATOR,
         {'order_list', 'order_create', 'order_import', 'order_edit', 'order_detail', 'order_sheet', 'order_export', 'order_events_export',
          'route_copy', 'code_list', 'item_drawing', 'item_action'}),
        (_('Priorities'), 'priorities', 'priorities', OPERATOR, {'priorities'}),
    ]),
    (_('Review'), [
        (_('Analysis'), 'analysis', 'analysis', OPERATOR,
         {'analysis', 'results', 'plan_summary', 'result_detail', 'result_photo', 'record_result', 'correct_event'}),
        (_('Shipping'), 'shipping', 'shipping', PLANNER, {'shipping'}),
        (_('Dashboard'), 'dashboard', 'dashboard', PLANNER, {'dashboard'}),
    ]),
    (_('Manage'), [
        (_('Customers'), 'customer_list', 'customers', PLANNER, {'customer_list', 'customer_detail', 'customer_edit'}),
        (_('Tags'), 'tag_manage', 'tags', PLANNER,
         {'tag_manage', 'tag_history', 'device_list', 'device_add', 'device_edit', 'device_regenerate'}),
        (_('Users'), 'user_list', 'users', PLANNER, {'user_list', 'user_add', 'user_edit', 'user_reset'}),
        (_('Settings'), 'settings', 'settings', PLANNER, {'settings'}),
    ]),
    (_('Developer'), [
        (_('Simulate scan'), 'simulate', 'simulate', ADMIN, {'simulate'}),
        ('Style guide', 'styleguide', 'palette', ADMIN, {'styleguide'}),  # English only: a developer page
    ]),
]


def _level(request):
    if not hasattr(request, '_role_level'):  # one group query per request, shared by both processors
        request._role_level = role_level(request.user)
    return request._role_level


def roles(request):
    level = _level(request)
    return {'can': {r.lower(): level >= ROLES.index(r) for r in (OPERATOR, PLANNER, ADMIN)}}


def nav(request):
    """Sidebar groups for the user's role, with the item of the current page marked active."""
    if not request.user.is_authenticated:
        return {'nav': [], 'account': None}
    level = _level(request)
    current = request.resolver_match.url_name if request.resolver_match else None
    groups = []
    for label, entries in NAV:
        items = [{'label': text, 'url': reverse(name), 'icon': icon, 'active': current in names}
                 for text, name, icon, role, names in entries if role is None or level >= ROLES.index(role)]
        if items:
            groups.append({'label': label, 'items': items})
    user = request.user
    account = {'initials': accounts.initials(user), 'full_name': user.get_full_name(), 'role': ROLE_LABELS[ROLES[level]] if level >= 0 else ''}
    return {'nav': groups, 'account': account}
