from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from .models import Event

NOT_STARTED, WAITING, IN_PROGRESS, AT_VENDOR, COMPLETE = 'NOT_STARTED', 'WAITING', 'IN_PROGRESS', 'AT_VENDOR', 'COMPLETE'
PAUSED, CANCELLED, SHIPPED = 'PAUSED', 'CANCELLED', 'SHIPPED'
# Item codes that no longer need work
CLOSED = (COMPLETE, SHIPPED, CANCELLED)

LABELS = {
    NOT_STARTED: _('Not started'),
    WAITING: _('Waiting'),
    IN_PROGRESS: _('In progress'),
    AT_VENDOR: _('At vendor'),
    COMPLETE: _('Complete'),
    PAUSED: _('Paused'),
    CANCELLED: _('Cancelled'),
    SHIPPED: _('Shipped'),
}

PENDING, STEP_IN_PROGRESS, STEP_AT_VENDOR, DONE = 'pending', 'in_progress', 'at_vendor', 'done'

STEP_LABELS = {
    PENDING: _('Pending'),
    STEP_IN_PROGRESS: _('In progress'),
    STEP_AT_VENDOR: _('At vendor'),
    DONE: _('Done'),
    'paused': _('Paused'),
}


@dataclass
class StepRow:
    step: object
    state: str = PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    paused: bool = False
    partial: bool = False


@dataclass
class BoxStatus:
    assignment: object
    label: str
    code: str
    code_label: str
    current_step: object = None
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    last_event_at: datetime | None = None
    assignments: list = field(default_factory=list)


@dataclass
class ItemStatus:
    code: str
    label: str
    current_step: object = None
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    last_event_at: datetime | None = None
    boxes: list = field(default_factory=list)
    cancel_event: object = None
    ship_event: object = None


NO_STATUS_TYPES = (Event.Type.CORRECTION, Event.Type.RESULT, Event.Type.CANCELLED, Event.Type.SHIPPED)


def _window(a):
    """Q for events on the assignment's tag while it was assigned."""
    q = Q(tag_uid=a.tag.uid, occurred_at__gte=a.start_at)
    return q & Q(occurred_at__lt=a.end_at) if a.end_at else q


def _live(q):
    """Scan events matching q, minus those voided by a CORRECTION, oldest first. RESULT events never change status."""
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    return Event.objects.filter(q).exclude(pk__in=corrected).exclude(event_type__in=NO_STATUS_TYPES).order_by('occurred_at', 'pk')


def events_for_box(assignments):
    """Events of one (item, box) pair: every tag it had, each within its own window, in time order."""
    q = Q()
    for a in assignments:
        q |= _window(a)
    return _live(q) if q else Event.objects.none()


def events_with_voids(assignments):
    """Like events_for_box but voided events are kept, each with .voided_by (its CORRECTION or None)."""
    q = Q()
    for a in assignments:
        q |= _window(a)
    events = list(Event.objects.filter(q).exclude(event_type=Event.Type.CORRECTION).select_related('process', 'device', 'worker', 'machine', 'created_by').order_by('occurred_at', 'pk')) if q else []
    voids = {c.corrects_id: c for c in Event.objects.filter(event_type=Event.Type.CORRECTION, corrects__in=events)}
    for e in events:
        e.voided_by = voids.get(e.pk)
    return events


def events_for_assignment(assignment):
    """Events scanned on one tag during its assignment window."""
    return events_for_box([assignment])


def events_for_item(item):
    """Events of all the item's boxes (open and ended assignments)."""
    q = Q()
    for a in item.tagassignment_set.select_related('tag'):
        q |= _window(a)
    return _live(q) if q else Event.objects.none()


def _apply(event, rows, warnings):
    """Move the matching route step to its next state; warn if nothing fits."""
    cands = [r for r in rows if r.step.process_id == event.process_id]
    pick = lambda *states: next((r for s in states for r in cands if r.state == s), None)
    t, E = event.occurred_at, Event.Type
    if event.event_type == E.ARRIVED:
        r = pick(PENDING)
        if r:
            r.state, r.started_at, r.finished_at = DONE, t, t
    elif event.event_type == E.PAUSED:
        r = next((x for x in cands if x.state in (STEP_IN_PROGRESS, STEP_AT_VENDOR) and not x.paused), None)
        if r:
            r.paused = True
    elif event.event_type == E.RESUMED:
        r = next((x for x in cands if x.paused), None)
        if r:
            r.paused = False
    elif event.event_type == E.PARTIAL:
        r = next((x for x in cands if x.state in (STEP_IN_PROGRESS, STEP_AT_VENDOR)), None)
        if r:
            r.partial = True
    elif event.event_type == E.STARTED:
        r = pick(PENDING)
        if r:
            r.state, r.started_at = STEP_IN_PROGRESS, t
    elif event.event_type == E.FINISHED:
        r = pick(STEP_IN_PROGRESS, PENDING)
        if r:
            r.state, r.finished_at, r.paused, r.partial = DONE, t, False, False
    elif event.event_type == E.SENT_OUT:
        r = pick(PENDING)
        if r:
            r.state, r.started_at = STEP_AT_VENDOR, t
    elif event.event_type == E.RETURNED:
        r = pick(STEP_AT_VENDOR, PENDING)
        if r:
            r.state, r.finished_at, r.paused, r.partial = DONE, t, False, False
    else:
        return
    if not cands:
        warnings.append(format_lazy(_('Event for a process not in the route: {}'), event.process))
    elif r is None:
        warnings.append(format_lazy(_('Unexpected {} event for {}'), event.get_event_type_display(), event.process))


def _current(rows):
    """The step being worked on (latest active one), else the first step not done."""
    active = [r for r in rows if r.state in (STEP_IN_PROGRESS, STEP_AT_VENDOR)]
    if active:
        return max(active, key=lambda r: r.started_at)
    return next((r for r in rows if r.state != DONE), None)


def _replay(steps, assignments, events):
    """Status of one (item, box) pair: replay its events over fresh rows of the item's route."""
    rows, warnings = [StepRow(st) for st in steps], []
    for e in events:
        if e.process_id:
            _apply(e, rows, warnings)
    for i, r in enumerate(rows):
        if r.state != PENDING:
            warnings += [format_lazy(_('Step skipped: {}'), p.step.process) for p in rows[:i] if p.state == PENDING]
    current = _current(rows)
    if not rows or all(r.state == PENDING for r in rows):
        code = NOT_STARTED
    elif current is None:
        code = COMPLETE
    elif current.paused:
        code = PAUSED
    else:
        code = {STEP_IN_PROGRESS: IN_PROGRESS, STEP_AT_VENDOR: AT_VENDOR}.get(current.state, WAITING)
    assignment = assignments[-1] if assignments else None
    label = assignment.box.label if assignment else ''
    last = events[-1].occurred_at if events else None
    return BoxStatus(assignment, label, code, LABELS[code], current.step if current else None, rows,
                     list(dict.fromkeys(map(str, warnings))), last, list(assignments))


def _steps(item):
    return list(item.steps.select_related('process').order_by('sequence'))


def box_status(assignment):
    """Status of the (item, box) pair the assignment belongs to."""
    group = list(assignment.item.tagassignment_set.filter(box=assignment.box).select_related('tag', 'box').order_by('start_at', 'pk'))
    return _replay(_steps(assignment.item), group, list(events_for_box(group)))


def _roll_up(boxes):
    """Item status from its boxes; the deciding box supplies step, rows and last scan."""
    codes = {b.code for b in boxes}
    if len(codes) == 1 and codes <= {COMPLETE, NOT_STARTED}:
        code, pick, last = boxes[0].code, boxes[0], boxes[0].last_event_at
    else:
        # Most active code wins; within a code, the most recently scanned box
        code = next((c for c in (IN_PROGRESS, WAITING, AT_VENDOR, PAUSED) if c in codes), None)
        if code:
            pick = max((b for b in boxes if b.code == code), key=lambda b: (b.last_event_at is not None, b.last_event_at))
            last = pick.last_event_at
        else:
            # Only finished and untouched boxes: waiting for the rest
            code, pick = WAITING, next(b for b in boxes if b.code == NOT_STARTED)
            last = max((b.last_event_at for b in boxes if b.last_event_at), default=None)
    warnings = list(dict.fromkeys(w for b in boxes for w in b.warnings))
    return ItemStatus(code, LABELS[code], pick.current_step, pick.rows, warnings, last, boxes)


def _item_event(item, event_type):
    """The latest live event of this item-level type (CANCELLED or SHIPPED), or None."""
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    return (Event.objects.filter(event_type=event_type, item=item).exclude(pk__in=corrected)
            .select_related('created_by').order_by('-occurred_at', '-pk').first())


def item_status(item):
    steps = _steps(item)
    assignments = item.tagassignment_set.select_related('tag', 'box').order_by('start_at', 'pk')
    groups = {}  # a box keeps its status across replaced tags
    for a in assignments:
        groups.setdefault(a.box_id, []).append(a)
    boxes = [_replay(steps, g, list(events_for_box(g))) for g in groups.values()]
    boxes += [_replay(steps, [], []) for _ in range(max(item.box_count - len(boxes), 0 if boxes else 1))]
    status = _roll_up(boxes)
    if cancel := _item_event(item, Event.Type.CANCELLED):
        status.code, status.label, status.cancel_event = CANCELLED, LABELS[CANCELLED], cancel
    elif status.code == COMPLETE and (ship := _item_event(item, Event.Type.SHIPPED)):
        status.code, status.label, status.ship_event = SHIPPED, LABELS[SHIPPED], ship  # shipping counts only after COMPLETE
    return status


def order_summary(order):
    """(code, label): NOT_STARTED, IN_PROGRESS, COMPLETE, SHIPPED (all items shipped) or CANCELLED (every item cancelled) for the whole order."""
    codes = [item_status(i).code for i in order.items.all()]
    if codes and set(codes) == {CANCELLED}:
        return CANCELLED, LABELS[CANCELLED]
    codes = [c for c in codes if c != CANCELLED]  # cancelled items do not count
    done = sum(c in (COMPLETE, SHIPPED) for c in codes)
    if codes and set(codes) == {SHIPPED}:
        return SHIPPED, LABELS[SHIPPED]
    if codes and done == len(codes):
        return COMPLETE, LABELS[COMPLETE]
    if all(c == NOT_STARTED for c in codes):
        return NOT_STARTED, LABELS[NOT_STARTED]
    return IN_PROGRESS, format_lazy(_('In progress ({done}/{total} items complete)'), done=done, total=len(codes))


def order_status(order):
    return order_summary(order)[1]


def current_row(status):
    """The route row of the step an item is on now, or None."""
    return _current(status.rows)


def due_back(out_since, lead_days):
    """JST date an outsourced item should return, or None without lead days."""
    if out_since is None or lead_days is None:
        return None
    return timezone.localtime(out_since).date() + timedelta(days=lead_days)


def step_due_back(step, out_since):
    """Planned due date of the step when set, else out-since plus lead days (vendor, then process)."""
    return step.due_date or due_back(out_since, step.lead_days)


def days_late(due_date, today=None):
    """Whole days past the due-back date; 0 when on time or no date."""
    if due_date is None:
        return 0
    return max(((today or timezone.localdate()) - due_date).days, 0)


def timeline(rows, now=None):
    """Per step: duration (timedelta or None), running flag, and wait since the last finished step."""
    now, out, prev_end = now or timezone.now(), [], None
    for r in rows:
        running = r.state in (STEP_IN_PROGRESS, STEP_AT_VENDOR) and r.started_at is not None
        end = now if running else r.finished_at
        duration = end - r.started_at if r.started_at and end else None
        if duration == timedelta(0) and not running:
            duration = None  # arrived steps start and finish together: no time spent
        wait = r.started_at - prev_end if r.started_at and prev_end else None
        out.append({'duration': duration, 'running': running, 'wait_before': wait if wait and wait >= timedelta(0) else None})
        prev_end = r.finished_at or prev_end
    return out


def gantt(rows, now=None):
    """Bar-chart layout for one item's rows, or None when no step has started."""
    now = now or timezone.now()
    started = [r.started_at for r in rows if r.started_at]
    if not started:
        return None
    live = lambda r: r.state in (STEP_IN_PROGRESS, STEP_AT_VENDOR)
    running = any(live(r) and r.started_at for r in rows)
    start = min(started)
    end = max([r.finished_at for r in rows if r.finished_at] + [now] * running + [start])
    span = max((end - start).total_seconds(), 60)
    pct = lambda t: min(max((t - start).total_seconds() / span * 100, 0), 100)
    out, prev_end = [], None
    for r in rows:
        bar = {'kind': PENDING, 'left': 0, 'width': 0, 'wait_left': None, 'wait_width': None}
        if r.started_at:
            stop = now if live(r) else r.finished_at
            left = pct(r.started_at)
            if stop is None or (stop == r.started_at and not live(r)):
                bar.update(kind='instant', left=left)
            else:
                width = max(pct(stop) - left, 1)
                bar.update(kind=r.state if live(r) else DONE, left=min(left, 100 - width), width=width)
            if prev_end and r.started_at > prev_end:
                bar.update(wait_left=pct(prev_end), wait_width=pct(r.started_at) - pct(prev_end))
        out.append(bar)
        prev_end = r.finished_at or prev_end
    mid = start + (end - start) / 2
    return {'rows': out, 'start': start, 'mid': mid, 'end': end, 'now_pct': pct(now) if running else None}


def idle_for(status, now=None):
    """Time since the item's last event, or None without events."""
    return (now or timezone.now()) - status.last_event_at if status.last_event_at else None


def is_stagnant(status, now=None):
    """True for WAITING/IN_PROGRESS items idle longer than the step's limit (or the default)."""
    idle = idle_for(status, now)
    if status.code not in (WAITING, IN_PROGRESS) or idle is None or status.current_step is None:
        return False
    hours = status.current_step.process.stagnation_hours or settings.STAGNATION_DEFAULT_HOURS
    return idle > timedelta(hours=hours)


def next_action(box):
    """(event type, process) that moves this box one step forward, or None when finished."""
    row = _current(box.rows)
    if row is None or box.code == COMPLETE:
        return None
    E, K = Event.Type, row.step.process.kind
    if row.state == STEP_IN_PROGRESS:
        return E.FINISHED, row.step.process
    if row.state == STEP_AT_VENDOR:
        return E.RETURNED, row.step.process
    return {'MATERIAL': E.ARRIVED, 'MACHINING': E.STARTED, 'OUTSOURCED': E.SENT_OUT}[K], row.step.process


# Planner buttons on a box: event type -> (form value, label)
MANUAL_BUTTONS = {
    Event.Type.PAUSED: ('pause', _('Pause')),
    Event.Type.RESUMED: ('resume', _('Resume')),
    Event.Type.PARTIAL: ('partial', _('Mark partial')),
}


def manual_actions(box):
    """(event type, process) pairs a planner may record on the box's current step now."""
    row = _current(box.rows)
    if row is None or row.state not in (STEP_IN_PROGRESS, STEP_AT_VENDOR):
        return []
    E = Event.Type
    return [(E.RESUMED, row.step.process)] if row.paused else [(E.PAUSED, row.step.process), (E.PARTIAL, row.step.process)]
