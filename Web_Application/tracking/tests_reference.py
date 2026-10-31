"""The original per-item status code, kept only as the correctness oracle for item_statuses. The app never calls it."""
from .models import Event
from .status import CANCELLED, COMPLETE, LABELS, SHIPPED, _replay, _roll_up, _steps, box_chains, events_for_box


def _item_event(item, event_type):
    """The latest live event of this item-level type (CANCELLED or SHIPPED), or None."""
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    return (Event.objects.filter(event_type=event_type, item=item).exclude(pk__in=corrected)
            .select_related('created_by').order_by('-occurred_at', '-pk').first())


def reference_item_status(item):
    steps = _steps(item)
    assignments = item.tagassignment_set.select_related('tag').order_by('start_at', 'pk')
    boxes = [_replay(steps, g, list(events_for_box(g)), n) for n, g in enumerate(box_chains(assignments).values(), 1)]  # a box keeps its status across replaced tags
    boxes += [_replay(steps, [], []) for _ in range(max(item.box_count - len(boxes), 0 if boxes else 1))]
    status = _roll_up(boxes)
    if cancel := _item_event(item, Event.Type.CANCELLED):
        status.code, status.label, status.cancel_event = CANCELLED, LABELS[CANCELLED], cancel
    elif status.code == COMPLETE and (ship := _item_event(item, Event.Type.SHIPPED)):
        status.code, status.label, status.ship_event = SHIPPED, LABELS[SHIPPED], ship  # shipping counts only after COMPLETE
    return status


def _row(r):
    return (r.step.pk, r.state, r.started_at, r.finished_at, r.paused, r.partial)


def _box(b):
    return {
        'assignment': b.assignment and b.assignment.pk, 'number': b.number, 'code': b.code, 'code_label': str(b.code_label),
        'current_step': b.current_step and b.current_step.pk, 'rows': [_row(r) for r in b.rows], 'warnings': list(b.warnings),
        'last_event_at': b.last_event_at, 'assignments': [a.pk for a in b.assignments],
    }


def plain(st):
    """An ItemStatus as plain data, so two statuses can be compared field by field."""
    return {
        'code': st.code, 'label': str(st.label), 'current_step': st.current_step and st.current_step.pk, 'rows': [_row(r) for r in st.rows],
        'warnings': list(st.warnings), 'last_event_at': st.last_event_at, 'cancel_event': st.cancel_event and st.cancel_event.pk,
        'ship_event': st.ship_event and st.ship_event.pk, 'boxes': [_box(b) for b in st.boxes],
    }


def status_diff(a, b):
    """The name of the first field where two ItemStatus differ (with both values), or None when equal."""
    pa, pb = plain(a), plain(b)
    for key in pa:
        if key == 'boxes':
            if len(pa[key]) != len(pb[key]):
                return f'boxes: {len(pa[key])} != {len(pb[key])} boxes'
            for i, (x, y) in enumerate(zip(pa[key], pb[key])):
                for k in x:
                    if x[k] != y[k]:
                        return f'boxes[{i}].{k}: {x[k]!r} != {y[k]!r}'
        elif pa[key] != pb[key]:
            return f'{key}: {pa[key]!r} != {pb[key]!r}'
    return None


def first_difference(items, statuses):
    """(item, field) of the first item whose batch status differs from the reference, or None."""
    for item in items:
        if diff := status_diff(statuses[item.pk], reference_item_status(item)):
            return item, diff
    return None
