from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.permissions import PLANNER, has_role

from .models import Box, Event, Tag, TagAssignment
from .status import CANCELLED, COMPLETE, item_status, manual_actions, next_action


def resolve_tag(uid, moment):
    """The assignment (tag, box, item) active for this UID at that moment, or None."""
    return (
        TagAssignment.objects.at(moment)
        .filter(tag__uid=uid.strip().upper())
        .select_related('tag', 'box', 'item__order')
        .first()
    )


def next_step(assignment):
    """(box status, (event type, process) or None) for the box this assignment belongs to."""
    box = next(b for b in item_status(assignment.item).boxes if b.assignment and b.assignment.box_id == assignment.box_id)
    return box, next_action(box)


def record_event(device, tag_uid, process, event_type, occurred_at=None):
    occurred_at = occurred_at or timezone.now()
    event = Event(
        device=device, tag_uid=tag_uid.strip().upper(), process=process,
        event_type=event_type, occurred_at=occurred_at,
    )
    event.full_clean()
    with transaction.atomic():
        event.save()
        device.last_seen = timezone.now()
        device.save(update_fields=['last_seen'])
    return event, resolve_tag(event.tag_uid, occurred_at)


def assign_tag(item, box_label, tag_uid):
    """Link a tag and a box to an item from now on; never edits past assignments."""
    label, uid = box_label.strip(), tag_uid.strip().upper()
    if not label or not uid:
        raise ValidationError(_('Box label and tag UID are required.'))
    with transaction.atomic():
        box = Box.objects.get_or_create(label=label)[0]
        tag = Tag.objects.get_or_create(uid=uid)[0]
        if not tag.active:
            raise ValidationError(_('Tag %(tag)s is inactive.') % {'tag': tag})
        if not box.active:
            raise ValidationError(_('Box %(box)s is inactive.') % {'box': box})
        open_ = TagAssignment.objects.filter(end_at__isnull=True).select_related('item__order', 'tag', 'box')
        if open_.filter(item=item).count() >= item.box_count:
            raise ValidationError(_('Item %(item)s already has all %(n)s boxes linked. Raise its box count first.') % {
                'item': item.item_code, 'n': item.box_count,
            })
        if found := open_.filter(tag=tag).first():
            raise ValidationError(_('Tag %(tag)s is already in use for item %(item)s.') % {'tag': tag, 'item': found.item.item_code})
        if found := open_.filter(box=box).first():
            raise ValidationError(_('Box %(box)s is already in use for item %(item)s.') % {'box': box, 'item': found.item.item_code})
        return TagAssignment.objects.create(tag=tag, box=box, item=item, start_at=timezone.now())


def release_assignment(assignment):
    assignment.release()
    return assignment


def replace_tag(assignment, new_uid, mark_lost=True):
    """Swap a lost tag: end the open assignment now and start a new one on the same item and box."""
    uid = new_uid.strip().upper()
    with transaction.atomic():
        old = TagAssignment.objects.select_for_update().select_related('tag', 'box', 'item__order').get(pk=assignment.pk)
        if old.end_at is not None:
            raise ValidationError(_('This assignment has already ended.'))
        if not uid:
            raise ValidationError(_('Box label and tag UID are required.'))
        if uid == old.tag.uid:
            raise ValidationError(_('The new tag must differ from the old tag.'))
        tag = Tag.objects.get_or_create(uid=uid)[0]
        if not tag.active:
            raise ValidationError(_('Tag %(tag)s is inactive.') % {'tag': tag})
        if found := TagAssignment.objects.filter(tag=tag, end_at__isnull=True).select_related('item__order').first():
            raise ValidationError(_('Tag %(tag)s is already in use for item %(item)s.') % {'tag': tag, 'item': found.item.item_code})
        now = timezone.now()
        old.end_at = now
        old.save(update_fields=['end_at'])
        new = TagAssignment.objects.create(tag=tag, box=old.box, item=old.item, start_at=now)
        if mark_lost:
            old.tag.active = False
            old.tag.save(update_fields=['active'])
        return new


def can_correct(user):
    """Only Planners and Admins (superusers included) may correct events."""
    return has_role(user, PLANNER)


def correct_event(event, user, reason, replacement=None):
    """Void an event with a CORRECTION, optionally adding a manual replacement; the original is never touched."""
    if not can_correct(user):
        raise PermissionDenied
    reason = reason.strip()
    with transaction.atomic():
        original = Event.objects.select_for_update().get(pk=event.pk)
        if original.event_type == Event.Type.CORRECTION:
            raise ValidationError(_('A correction cannot be corrected.'))
        if replacement and original.event_type not in Event.REQUIRED_KIND:
            raise ValidationError(_('This event can only be voided.'))
        if Event.objects.filter(event_type=Event.Type.CORRECTION, corrects=original).exists():
            raise ValidationError(_('This event is already voided.'))
        correction = Event(
            device=original.device, tag_uid=original.tag_uid, event_type=Event.Type.CORRECTION,
            corrects=original, occurred_at=timezone.now(), reason=reason, created_by=user,
        )
        correction.full_clean()
        new = None
        if replacement:
            new = Event(
                device=original.device, tag_uid=original.tag_uid, process=replacement['process'],
                event_type=replacement['event_type'], occurred_at=original.occurred_at,
                is_manual=True, reason=reason, created_by=user,
            )
            new.full_clean()
        correction.save()
        if new:
            new.save()
    return correction, new


def record_result(user, assignment, process, quantity, defects=0, worker=None, machine=None):
    """Record how much was done at a step (and how much of it was defective) for the box of an assignment; never changes status."""
    event = Event(
        tag_uid=assignment.tag.uid, process=process, event_type=Event.Type.RESULT, quantity=quantity, defects=defects,
        worker=worker, machine=machine, created_by=user,
    )
    event.full_clean()
    event.save()
    return event


def record_step_state(user, assignment, event_type, reason=''):
    """Pause, resume or mark partial the current step of the box an assignment belongs to (planners only)."""
    if not has_role(user, PLANNER):
        raise PermissionDenied
    box = next_step(assignment)[0]
    process = next((p for t, p in manual_actions(box) if t == event_type), None)
    if process is None:
        raise ValidationError(_('This action is not available for this box right now.'))
    event = Event(tag_uid=assignment.tag.uid, process=process, event_type=event_type, reason=reason.strip(), created_by=user)
    event.full_clean()
    event.save()
    return event


def cancel_item(user, item, reason):
    """Cancel an item; undo it by voiding the event with a correction (planners only)."""
    if not has_role(user, PLANNER):
        raise PermissionDenied
    code = item_status(item).code
    if code == CANCELLED:
        raise ValidationError(_('This item is already cancelled.'))
    if code == COMPLETE:
        raise ValidationError(_('A finished item cannot be cancelled.'))
    event = Event(event_type=Event.Type.CANCELLED, item=item, reason=reason.strip(), created_by=user)
    event.full_clean()
    event.save()
    return event
