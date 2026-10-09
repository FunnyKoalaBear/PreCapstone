from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Box, Event, Tag, TagAssignment


def resolve_tag(uid, moment):
    """The assignment (tag, box, item) active for this UID at that moment, or None."""
    return (
        TagAssignment.objects.at(moment)
        .filter(tag__uid=uid.strip().upper())
        .select_related('tag', 'box', 'item__order')
        .first()
    )


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
        if open_.filter(item=item).exists():
            raise ValidationError(_('Item %(item)s already has a tag assigned.') % {'item': item.item_code})
        if found := open_.filter(tag=tag).first():
            raise ValidationError(_('Tag %(tag)s is already in use for item %(item)s.') % {'tag': tag, 'item': found.item.item_code})
        if found := open_.filter(box=box).first():
            raise ValidationError(_('Box %(box)s is already in use for item %(item)s.') % {'box': box, 'item': found.item.item_code})
        return TagAssignment.objects.create(tag=tag, box=box, item=item, start_at=timezone.now())


def release_assignment(assignment):
    assignment.release()
    return assignment
