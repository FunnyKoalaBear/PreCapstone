from django.db import transaction
from django.utils import timezone

from .models import Event, TagAssignment


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
