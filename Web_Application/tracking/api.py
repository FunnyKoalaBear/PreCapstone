import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from orders.models import Process

from .models import Event, ScannerDevice
from .services import next_step, record_event, resolve_tag
from .status import item_status


def _error(status, code, detail=None):
    body = {'ok': False, 'error': code}
    if detail:
        body['detail'] = detail
    return JsonResponse(body, status=status)


def _touch(device, now):
    device.last_seen = now
    device.save(update_fields=['last_seen'])


def _reply(state, assignment, event=None, process=None):
    body = {
        'ok': True, 'state': state, 'tag_uid': assignment.tag.uid, 'box': assignment.box.label,
        'item': assignment.item.item_code, 'status': str(item_status(assignment.item).label),
    }
    if event:
        body.update(event_id=event.pk, tag_uid=event.tag_uid, event_type=event.event_type, process_id=process.pk, process_name=process.name)
    return JsonResponse(body)


@csrf_exempt
@require_POST
def scan(request):
    # Key check first: no data is read from a caller we do not know
    device = ScannerDevice.objects.filter(api_key=request.headers.get('X-API-Key', ''), active=True).first()
    if device is None:
        return _error(401, 'bad_key')
    try:
        data = json.loads(request.body)
        uid = str(data['tag_uid']).strip()
        event_type, pid = data.get('event_type'), data.get('process')
        if not uid:
            raise ValueError('tag_uid is empty')
        if (event_type is None) != (pid is None):
            raise ValueError('send both process and event_type, or neither')
        process = Process.objects.get(pk=pid) if pid is not None else None
        if event_type is not None and event_type not in Event.Type.values:
            raise ValueError('event_type is invalid')
    except (ValueError, KeyError, TypeError, AttributeError, Process.DoesNotExist) as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    now = timezone.now()
    assignment = resolve_tag(uid, now)
    if assignment is None:
        _touch(device, now)
        return _error(404, 'unassigned_tag')
    if process is None:
        # Auto mode: the server picks the next step
        _, todo = next_step(assignment)
        if todo is None:
            _touch(device, now)
            return _reply('finished', assignment)
        window = settings.SCAN_REPEAT_SECONDS
        if window > 0 and Event.objects.filter(tag_uid=assignment.tag.uid, occurred_at__gte=now - timedelta(seconds=window)).exists():
            _touch(device, now)
            return _reply('duplicate', assignment)
        event_type, process = todo
    try:
        event, assignment = record_event(device, uid, process, event_type, now)
    except ValidationError as e:
        return _error(400, 'invalid', ' '.join(e.messages))
    return _reply('recorded', assignment, event, process)
