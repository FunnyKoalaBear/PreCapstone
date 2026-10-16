import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from orders.models import Process

from .models import Event, ScannerDevice
from .services import record_event, resolve_tag
from .status import item_status


def _error(status, code, detail=None):
    body = {'ok': False, 'error': code}
    if detail:
        body['detail'] = detail
    return JsonResponse(body, status=status)


@csrf_exempt
@require_POST
def scan(request):
    # Key check first: no data is read from a caller we do not know
    device = ScannerDevice.objects.filter(api_key=request.headers.get('X-API-Key', ''), active=True).first()
    if device is None:
        return _error(401, 'bad_key')
    try:
        data = json.loads(request.body)
        uid, event_type = str(data['tag_uid']).strip(), data['event_type']
        process = Process.objects.get(pk=data['process'])
        if not uid or event_type not in Event.Type.values:
            raise ValueError('tag_uid or event_type is invalid')
    except (ValueError, KeyError, TypeError, Process.DoesNotExist) as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    now = timezone.now()
    assignment = resolve_tag(uid, now)
    if assignment is None:
        device.last_seen = now
        device.save(update_fields=['last_seen'])
        return _error(404, 'unassigned_tag')
    try:
        event, assignment = record_event(device, uid, process, event_type, now)
    except ValidationError as e:
        return _error(400, 'invalid', ' '.join(e.messages))
    return JsonResponse({
        'ok': True, 'event_id': event.pk, 'tag_uid': event.tag_uid, 'box': assignment.box.label,
        'item': assignment.item.item_code, 'status': str(item_status(assignment.item).label),
    })
