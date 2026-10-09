from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from .models import Event

NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, COMPLETE = 'NOT_STARTED', 'WAITING', 'IN_PROGRESS', 'AT_VENDOR', 'COMPLETE'

LABELS = {
    NOT_STARTED: _('Not started'),
    WAITING: _('Waiting'),
    IN_PROGRESS: _('In progress'),
    AT_VENDOR: _('At vendor'),
    COMPLETE: _('Complete'),
}

PENDING, STEP_IN_PROGRESS, STEP_AT_VENDOR, DONE = 'pending', 'in_progress', 'at_vendor', 'done'

STEP_LABELS = {
    PENDING: _('Pending'),
    STEP_IN_PROGRESS: _('In progress'),
    STEP_AT_VENDOR: _('At vendor'),
    DONE: _('Done'),
}


@dataclass
class StepRow:
    step: object
    state: str = PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class ItemStatus:
    code: str
    label: str
    current_step: object = None
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    last_event_at: datetime | None = None


def events_for_item(item):
    """Events scanned on the item's tags while those tags were assigned to it."""
    q = Q()
    for a in item.tagassignment_set.select_related('tag'):
        part = Q(tag_uid=a.tag.uid, occurred_at__gte=a.start_at)
        if a.end_at:
            part &= Q(occurred_at__lt=a.end_at)
        q |= part
    if not q:
        return Event.objects.none()
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    return Event.objects.filter(q).exclude(pk__in=corrected).order_by('occurred_at', 'pk')


def _apply(event, rows, warnings):
    """Move the matching route step to its next state; warn if nothing fits."""
    cands = [r for r in rows if r.step.process_id == event.process_id]
    pick = lambda *states: next((r for s in states for r in cands if r.state == s), None)
    t, E = event.occurred_at, Event.Type
    if event.event_type == E.ARRIVED:
        r = pick(PENDING)
        if r:
            r.state, r.started_at, r.finished_at = DONE, t, t
    elif event.event_type == E.STARTED:
        r = pick(PENDING)
        if r:
            r.state, r.started_at = STEP_IN_PROGRESS, t
    elif event.event_type == E.FINISHED:
        r = pick(STEP_IN_PROGRESS, PENDING)
        if r:
            r.state, r.finished_at = DONE, t
    elif event.event_type == E.SENT_OUT:
        r = pick(PENDING)
        if r:
            r.state, r.started_at = STEP_AT_VENDOR, t
    elif event.event_type == E.RETURNED:
        r = pick(STEP_AT_VENDOR, PENDING)
        if r:
            r.state, r.finished_at = DONE, t
    else:
        return
    if not cands:
        warnings.append(format_lazy(_('Event for a process not in the route: {}'), event.process))
    elif r is None:
        warnings.append(format_lazy(_('Unexpected {} event for {}'), event.get_event_type_display(), event.process))


def item_status(item):
    rows = [StepRow(s) for s in item.steps.select_related('process').order_by('sequence')]
    warnings, events = [], list(events_for_item(item))
    for e in events:
        if e.process_id:
            _apply(e, rows, warnings)
    for i, r in enumerate(rows):
        if r.state != PENDING:
            warnings += [format_lazy(_('Step skipped: {}'), p.step.process) for p in rows[:i] if p.state == PENDING]
    current = next((r for r in rows if r.state != DONE), None)
    if not rows or all(r.state == PENDING for r in rows):
        code = NOT_STARTED
    elif current is None:
        code = COMPLETE
    else:
        code = {STEP_IN_PROGRESS: IN_PROGRESS, STEP_AT_VENDOR: AT_VENDOR}.get(current.state, WAITING)
    last = events[-1].occurred_at if events else None
    return ItemStatus(code, LABELS[code], current.step if current else None, rows, list(dict.fromkeys(map(str, warnings))), last)


def order_summary(order):
    """(code, label): NOT_STARTED, IN_PROGRESS or COMPLETE for the whole order."""
    codes = [item_status(i).code for i in order.items.all()]
    done = codes.count(COMPLETE)
    if codes and done == len(codes):
        return COMPLETE, LABELS[COMPLETE]
    if all(c == NOT_STARTED for c in codes):
        return NOT_STARTED, LABELS[NOT_STARTED]
    return IN_PROGRESS, format_lazy(_('In progress ({done}/{total} items complete)'), done=done, total=len(codes))


def order_status(order):
    return order_summary(order)[1]
