"""Toast helpers: an Undo button on Active toggles. Undo goes through the same permission and audit path as the toggle."""
import secrets
import time

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .permissions import OPERATOR, has_role, role_required

TTL = 60  # seconds an Undo stays valid
KEEP = 5
HANDLERS = {}  # kind -> (role, load(actor, pk), set_active(actor, obj, active, undo) -> message text)


def register(kind, role, load, set_active):
    HANDLERS[kind] = (role, load, set_active)


def _live(request):
    now = time.time()
    return {t: e for t, e in request.session.get('undo', {}).items() if now - e['at'] <= TTL and e['user'] == request.user.pk}


def toggled(request, kind, obj, back, text):
    """Success toast for an Active toggle that already happened, with an Undo button."""
    token = secrets.token_urlsafe(8)
    entries = _live(request)
    entries[token] = {'kind': kind, 'pk': obj.pk, 'state': obj.is_active if kind == 'user' else obj.active,
                      'user': request.user.pk, 'at': time.time(), 'back': back}
    request.session['undo'] = dict(list(entries.items())[-KEEP:])
    messages.success(request, text, extra_tags=f'undo:{token}')


@require_POST
@role_required(OPERATOR)
def undo(request, token):
    entries = _live(request)
    entry = entries.pop(token, None)  # one use only
    request.session['undo'] = entries
    gone = _("This change can't be undone any more.")
    if not entry or entry['kind'] not in HANDLERS:
        messages.error(request, gone)
        return redirect('home')
    role, load, set_active = HANDLERS[entry['kind']]
    if not has_role(request.user, role):
        raise PermissionDenied
    obj = load(request.user, entry['pk'])
    state = obj and (obj.is_active if entry['kind'] == 'user' else obj.active)
    if obj is None or state != entry['state']:  # deleted, or changed again since
        messages.error(request, gone)
        return redirect(entry['back'])
    try:
        messages.success(request, set_active(request.user, obj, not entry['state'], True))
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect(entry['back'])
