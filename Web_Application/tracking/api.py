import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from orders.drawing_screen import MAX_SIDE, DrawingUnreadable, drawing_rgb565
from orders.models import OrderItem, PrintJob, Process, RouteStep
from orders.printing import queue_print

from .models import Event, ScannerDevice, Tag, TagAssignment
from .services import assign_tag, assignable_items, next_step, record_event, release_assignment, resolve_tag
from .status import item_status

ASSIGN_LIST_MAX = 50


def _error(status, code, detail=None, **extra):
    body = {'ok': False, 'error': code}
    if detail:
        body['detail'] = detail
    body.update(extra)
    return JsonResponse(body, status=status)


def _refusal(uid, item):
    """Why assign_tag refused (checked in the same order): tag_inactive, item_full or tag_in_use (+ the item it is on)."""
    tag = Tag.objects.filter(uid=uid.upper()).first()
    if tag and not tag.active:
        return {'reason': 'tag_inactive'}
    if TagAssignment.objects.filter(item=item, end_at__isnull=True).count() >= item.box_count:
        return {'reason': 'item_full'}
    current = TagAssignment.objects.filter(tag=tag, end_at__isnull=True).select_related('item__order').first() if tag else None
    if current:
        return {'reason': 'tag_in_use', 'current_item': current.item.item_code}
    return {'reason': 'other'}


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
    page = list(qs[offset:offset + limit])
    # Route step count per item in one query (the setter shows it on its details screen)
    steps = dict(RouteStep.objects.filter(item__in=page).values_list('item').annotate(n=Count('pk')))
    items = [{
        'id': i.pk, 'item': i.item_code, 'part': i.part_name, 'customer': i.order.customer.name,
        'due': i.order.due_date.isoformat(), 'linked': i.linked, 'boxes': i.box_count,
        'quantity': i.quantity, 'order_number': i.order.customer_order_number,
        'ordered': timezone.localdate(i.order.created_at).isoformat(), 'steps': steps.get(i.pk, 0),
    } for i in page]
    _touch(device, timezone.now())
    return JsonResponse({'ok': True, 'total': qs.count(), 'offset': offset, 'items': items})


@require_GET
def item_drawing_pixels(request, pk):
    """The item's drawing fitted into w x h as raw RGB565 (2 bytes per pixel, high byte first), for the setter screen."""
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    try:
        width, height = int(request.GET.get('w', 480)), int(request.GET.get('h', 320))
        if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
            raise ValueError(f'1 <= w, h <= {MAX_SIDE}')
    except ValueError as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    _touch(device, timezone.now())
    item = OrderItem.objects.select_related('order').filter(pk=pk).first()
    if item is None:
        return _error(404, 'no_item')
    if not item.drawing_file:
        return _error(404, 'no_drawing')
    try:
        data = drawing_rgb565(item, width, height)
    except DrawingUnreadable as e:
        return _error(422, 'unreadable', str(e))
    response = HttpResponse(data, content_type='application/octet-stream')
    response['Content-Length'] = str(len(data))  # the ESP32 checks it before drawing
    return response


@csrf_exempt
@require_POST
def assign(request):
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    try:
        data = json.loads(request.body)
        uid, pk = str(data['tag_uid']).strip(), data['item']
        replace = data.get('replace') is True
        if not uid:
            raise ValueError('tag_uid is empty')
        if not isinstance(pk, int) or isinstance(pk, bool):
            raise TypeError('item must be an integer id')
        item = OrderItem.objects.select_related('order').get(pk=pk)
    except (ValueError, KeyError, TypeError, AttributeError, OrderItem.DoesNotExist) as e:
        return _error(400, 'invalid', f'{type(e).__name__}: {e}')
    released = None
    try:
        # Link and queue the sheet together: no print for a refused tag, no link without a print.
        # replace: first end the tag's link to another item (the operator confirmed); a refusal undoes that too.
        with transaction.atomic():
            if replace:
                current = (TagAssignment.objects.select_for_update().select_related('item__order')
                           .filter(tag__uid=uid.upper(), end_at__isnull=True).exclude(item=item).first())
                if current:
                    release_assignment(current)
                    released = current.item.item_code
            a = assign_tag(item, uid)
            queue_print(item, PrintJob.Source.ASSIGN, a)
    except ValidationError as e:
        # Tag in use or inactive, or all boxes already linked: nothing saved
        _touch(device, timezone.now())
        return _error(409, 'refused', ' '.join(e.messages), **_refusal(uid, item))
    _touch(device, timezone.now())
    linked = TagAssignment.objects.filter(item=item, end_at__isnull=True).count()
    body = {'ok': True, 'state': 'assigned', 'tag_uid': a.tag.uid, 'item': item.item_code, 'linked': linked, 'boxes': item.box_count, 'print': 'queued'}
    if released:
        body['released'] = released
    return JsonResponse(body)


@require_GET
def tag_info(request, uid):
    """What a scanner shows after a card tap: the box's item and the step the next scan of it would record. Saves nothing."""
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    now = timezone.now()
    _touch(device, now)
    assignment = resolve_tag(uid, now)
    if assignment is None:
        return _error(404, 'unassigned_tag')
    box, todo = next_step(assignment)
    item, order = assignment.item, assignment.item.order
    steps = len(box.rows)
    current = next((n for n, r in enumerate(box.rows, 1) if box.current_step and r.step.pk == box.current_step.pk), steps)
    drawing = ' / '.join(v for v in (item.drawing_number, item.revision) if v)
    return JsonResponse({
        'ok': True, 'state': 'finished' if todo is None else 'open', 'tag_uid': assignment.tag.uid,
        'item': item.item_code, 'part': item.part_name, 'part_number': item.part_number, 'drawing': drawing,
        'material': item.material, 'quantity': item.quantity, 'customer': order.customer.name,
        'order_number': order.customer_order_number, 'ordered': timezone.localdate(order.created_at).isoformat(),
        'due': order.due_date.isoformat(), 'step': current, 'steps': steps,
        'process': todo[1].name if todo else '', 'event_type': todo[0] if todo else '',
        'status': str(box.code_label), 'box': box.caption,
    })


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


@require_GET
def scan_preview(request):
    """What a tag-only scan would record now, saving nothing (the scanner asks the operator before it scans)."""
    device = _device(request)
    if device is None:
        return _error(401, 'bad_key')
    uid = request.GET.get('tag_uid', '').strip()
    if not uid:
        return _error(400, 'invalid', 'tag_uid is missing or empty')
    now = timezone.now()
    assignment = resolve_tag(uid, now)
    _touch(device, now)
    if assignment is None:
        return _error(404, 'unassigned_tag')
    box, todo = next_step(assignment)
    item, order = assignment.item, assignment.item.order
    steps = [r.step for r in box.rows]
    body = {
        'ok': True, 'state': 'finished' if todo is None else 'next', 'tag_uid': assignment.tag.uid,
        'item': item.item_code, 'part': item.part_name, 'part_number': item.part_number,
        'drawing_number': item.drawing_number, 'revision': item.revision, 'material': item.material,
        'quantity': item.quantity, 'customer': order.customer.name, 'order_number': order.customer_order_number,
        'ordered': timezone.localdate(order.created_at).isoformat(), 'due': order.due_date.isoformat(),
        'status': str(item_status(item).label), 'step': len(steps), 'steps': len(steps),
    }
    if todo:
        event_type, process = todo
        body.update(step=steps.index(box.current_step) + 1, event_type=event_type,
                    process_id=process.pk, process_name=process.name)
    return JsonResponse(body)
