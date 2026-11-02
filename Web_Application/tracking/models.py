import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from orders.models import Process

from .photos import photo_upload_to


class Tag(models.Model):
    uid = models.CharField(_('UID'), max_length=100, unique=True)
    active = models.BooleanField(_('active'), default=True)
    note = models.CharField(_('note'), max_length=200, blank=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('tag')
        verbose_name_plural = _('tags')

    def __str__(self):
        return self.uid

    def clean(self):
        self.uid = self.uid.strip().upper()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class TagAssignmentQuerySet(models.QuerySet):
    def at(self, moment):
        return self.filter(Q(start_at__lte=moment) & (Q(end_at__isnull=True) | Q(end_at__gt=moment)))


class TagAssignment(models.Model):
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT, verbose_name=_('tag'))
    item = models.ForeignKey('orders.OrderItem', on_delete=models.PROTECT, verbose_name=_('item'))
    start_at = models.DateTimeField(_('start'), default=timezone.now)
    end_at = models.DateTimeField(_('end'), null=True, blank=True)
    # The assignment whose tag this one replaced (same physical box)
    replaces = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='replaced_by', verbose_name=_('replaces'))

    objects = TagAssignmentQuerySet.as_manager()

    class Meta:
        verbose_name = _('tag assignment')
        verbose_name_plural = _('tag assignments')
        constraints = [
            models.UniqueConstraint(
                fields=['tag'], condition=Q(end_at__isnull=True), name='one_open_assignment_per_tag',
            ),
            models.CheckConstraint(
                condition=Q(end_at__isnull=True) | Q(end_at__gt=F('start_at')), name='end_after_start',
            ),
        ]

    def __str__(self):
        return f'{self.tag} / {self.item}'

    def release(self):
        if self.end_at is None:
            self.end_at = timezone.now()
            self.save(update_fields=['end_at'])


class NamedResource(models.Model):
    """Shared shape of Worker and Machine: a name, its Japanese name and an active flag."""
    name = models.CharField(_('name'), max_length=100, unique=True)
    name_ja = models.CharField(_('Japanese name'), max_length=100, blank=True)
    active = models.BooleanField(_('active'), default=True)

    class Meta:
        abstract = True
        ordering = ['name']

    def __str__(self):
        return self.name_ja if self.name_ja and get_language().startswith('ja') else self.name


class Worker(NamedResource):
    class Meta(NamedResource.Meta):
        verbose_name = _('worker')
        verbose_name_plural = _('workers')


class Machine(NamedResource):
    class Meta(NamedResource.Meta):
        verbose_name = _('machine')
        verbose_name_plural = _('machines')


class DefectCause(NamedResource):
    class Meta(NamedResource.Meta):
        verbose_name = _('defect cause')
        verbose_name_plural = _('defect causes')


def generate_api_key():
    return secrets.token_urlsafe(32)


class ScannerDevice(models.Model):
    name = models.CharField(_('name'), max_length=100, unique=True)
    location = models.CharField(_('location'), max_length=200, blank=True)
    api_key = models.CharField(_('API key'), max_length=64, unique=True, editable=False, default=generate_api_key)
    active = models.BooleanField(_('active'), default=True)
    last_seen = models.DateTimeField(_('last seen'), null=True, blank=True)

    class Meta:
        verbose_name = _('scanner device')
        verbose_name_plural = _('scanner devices')

    def __str__(self):
        return self.name


class Event(models.Model):
    class Type(models.TextChoices):
        ARRIVED = 'ARRIVED', _('Arrived')
        STARTED = 'STARTED', _('Started')
        FINISHED = 'FINISHED', _('Finished')
        SENT_OUT = 'SENT_OUT', _('Sent out')
        RETURNED = 'RETURNED', _('Returned')
        CORRECTION = 'CORRECTION', _('Correction')
        RESULT = 'RESULT', _('Result')
        PAUSED = 'PAUSED', _('Paused')
        RESUMED = 'RESUMED', _('Resumed')
        PARTIAL = 'PARTIAL', _('Partial')
        CANCELLED = 'CANCELLED', _('Cancelled')
        SHIPPED = 'SHIPPED', _('Shipped')

    # Planner actions on the current step of a box; they need a process but no particular kind
    STEP_STATE_TYPES = (Type.PAUSED, Type.RESUMED, Type.PARTIAL)
    # Item-level events: they name the item, not a tag or a process
    ITEM_TYPES = (Type.CANCELLED, Type.SHIPPED)

    # Required process kind per event type
    REQUIRED_KIND = {
        Type.ARRIVED: Process.Kind.MATERIAL,
        Type.STARTED: Process.Kind.MACHINING,
        Type.FINISHED: Process.Kind.MACHINING,
        Type.SENT_OUT: Process.Kind.OUTSOURCED,
        Type.RETURNED: Process.Kind.OUTSOURCED,
    }

    occurred_at = models.DateTimeField(_('occurred at'), default=timezone.now)
    received_at = models.DateTimeField(_('received at'), auto_now_add=True)
    device = models.ForeignKey(ScannerDevice, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('device'))
    tag_uid = models.CharField(_('tag UID'), max_length=100, blank=True)
    process = models.ForeignKey(Process, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('process'))
    event_type = models.CharField(_('event type'), max_length=20, choices=Type.choices)
    corrects = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('corrects'),
    )
    reason = models.TextField(_('reason'), blank=True)
    is_manual = models.BooleanField(_('manual'), default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('created by'),
    )
    # CANCELLED and SHIPPED events name the item (they have no tag)
    item = models.ForeignKey('orders.OrderItem', on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('item'))
    # RESULT events only; defects are part of the quantity done
    worker = models.ForeignKey(Worker, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('worker'))
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('machine'))
    defect_cause = models.ForeignKey(DefectCause, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('main cause'))
    quantity = models.PositiveIntegerField(_('quantity done'), null=True, blank=True)
    defects = models.PositiveIntegerField(_('defects'), null=True, blank=True)
    note = models.TextField(_('note'), blank=True, validators=[MaxLengthValidator(1000)])
    photo = models.ImageField(_('photo'), upload_to=photo_upload_to, null=True, blank=True)

    class Meta:
        verbose_name = _('event')
        verbose_name_plural = _('events')
        ordering = ['occurred_at']
        permissions = [('correct_event', _('Can correct events'))]
        indexes = [models.Index(fields=['tag_uid', 'occurred_at'], name='event_tag_time_idx')]

    def __str__(self):
        return f'{self.occurred_at:%Y-%m-%d %H:%M} {self.event_type} {self.tag_uid}'

    def clean(self):
        self.tag_uid = self.tag_uid.strip().upper()
        if not self.device_id and not self.created_by_id:
            raise ValidationError({'device': _('An event needs a scanner device or a user.')})
        if not self.tag_uid and not self._tagless():
            raise ValidationError({'tag_uid': _('A tag UID is required.')})
        if self.event_type in self.ITEM_TYPES:
            if not self.item_id or self.process_id:
                raise ValidationError(_('A cancellation or shipment names an item and no process.'))
        elif self.item_id:
            raise ValidationError({'item': _('Only a cancellation or shipment names an item.')})
        if self.event_type == self.Type.RESULT:
            self._clean_result()
        elif self.worker_id or self.machine_id or self.defect_cause_id or self.quantity is not None or self.defects is not None or self.note or self.photo:
            raise ValidationError(_('Worker, machine, defect cause, quantity, defects, note and photo are only for results.'))
        if (self.event_type in (self.Type.CORRECTION, self.Type.CANCELLED) or self.is_manual) and not self.reason.strip():
            raise ValidationError({'reason': _('A reason is required.')})
        if self.event_type == self.Type.CORRECTION:
            if not self.corrects_id:
                raise ValidationError({'corrects': _('A correction must reference the event it corrects.')})
            if self.process_id:
                raise ValidationError({'process': _('A correction must not have a process.')})
        elif self.event_type in self.STEP_STATE_TYPES and not self.process_id:
            raise ValidationError({'process': _('This event type requires a process.')})
        elif self.event_type in self.REQUIRED_KIND:
            kind = self.REQUIRED_KIND[self.event_type]
            if not self.process_id or self.process.kind != kind:
                raise ValidationError({'process': _('This event type requires a process of kind: %(kind)s.') % {'kind': kind.label}})

    def _tagless(self):
        """Only item-level events, and the correction that voids one, may have no tag."""
        if self.event_type == self.Type.CORRECTION and self.corrects_id:
            return self.corrects.event_type in self.ITEM_TYPES
        return self.event_type in self.ITEM_TYPES

    def _clean_result(self):
        if not self.process_id:
            raise ValidationError({'process': _('A result needs a process.')})
        if not self.quantity:
            raise ValidationError({'quantity': _('The quantity done must be at least 1.')})
        if self.defects is None:
            self.defects = 0
        if self.defects > self.quantity:
            raise ValidationError({'defects': _('Defects cannot be more than the quantity done.')})
        if self.defect_cause_id and not self.defects:
            raise ValidationError({'defect_cause': _('A main cause needs at least one defect.')})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_('Events are append-only and cannot be changed.'))
        self.tag_uid = self.tag_uid.strip().upper()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_('Events are append-only and cannot be deleted.'))
