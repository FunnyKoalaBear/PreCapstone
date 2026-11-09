import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from orders.models import OrderItem, PrintJob, Process
from orders.printing import queue_print

from .models import Event, ScannerDevice, TagAssignment
from .services import assign_tag, assignable_items, next_step, record_event, resolve_tag
from .status import item_status

ASSIGN_LIST_MAX = 50


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
        'ok': True, 'state': state, 'tag_uid': assignment.tag.uid,
        'item': assignment.item.item_code, 'status': str(item_status(assignment.item).label),
    }
    if event:
        body.update(event_id=event.pk, tag_uid=event.tag_uid, event_type=event.event_type, process_id=process.pk, process_name=process.name)
    return JsonResponse(body)


def _device(request):
    return ScannerDevice.objects.filter(api_key=request.headers.get('X-API-Key', ''), active=True).first()


@require_GET
def assignable_items_list(request):
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    try:
        offset, limit = int(request.GET.get('offset', 0)), int(request.GET.get('limit', 20))
        if offset < 0 or not 1 <= limit <= ASSIGN_LIST_MAX:
            raise ValueError(f'offset >= 0 and 1 <= limit <= {ASSIGN_LIST_MAX}')
    except ValueError as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    qs = assignable_items()
    items = [{
        'id': i.pk, 'item': i.item_code, 'part': i.part_name, 'customer': i.order.customer.name,
        'due': i.order.due_date.isoformat(), 'linked': i.linked, 'boxes': i.box_count,
    } for i in qs[offset:offset + limit]]
    _touch(device, timezone.now())
    return JsonResponse({'ok': True, 'total': qs.count(), 'offset': offset, 'items': items})


@csrf_exempt
@require_POST
def assign(request):
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    try:
        data = json.loads(request.body)
        uid, pk = str(data['tag_uid']).strip(), data['item']
        if not uid:
            raise ValueError('tag_uid is empty')
        if not isinstance(pk, int) or isinstance(pk, bool):
            raise TypeError('item must be an integer id')
        item = OrderItem.objects.select_related('order').get(pk=pk)
    except (ValueError, KeyError, TypeError, AttributeError, OrderItem.DoesNotExist) as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    try:
        # Link and queue the sheet together: no print for a refused tag, no link without a print
        with transaction.atomic():
            a = assign_tag(item, uid)
            queue_print(item, PrintJob.Source.ASSIGN, a)
    except ValidationError as e:
        # Tag in use or inactive, or all boxes already linked: nothing saved
        _touch(device, timezone.now())
        return _error(409, 'refused', ' '.join(e.messages))
    _touch(device, timezone.now())
    linked = TagAssignment.objects.filter(item=item, end_at__isnull=True).count()
    return JsonResponse({'ok': True, 'state': 'assigned', 'tag_uid': a.tag.uid, 'item': item.item_code, 'linked': linked, 'boxes': item.box_count, 'print': 'queued'})


@csrf_exempt
@require_POST
def scan(request):
    # Key check first: no data is read from a caller we do not know
    device = _device(request)
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
