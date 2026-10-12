import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from orders.models import Process


class Box(models.Model):
    label = models.CharField(_('label'), max_length=100, unique=True)
    notes = models.TextField(_('notes'), blank=True)
    active = models.BooleanField(_('active'), default=True)
    created_at = models.DateTimeField(_('created at'), auto_now_add=True)

    class Meta:
        verbose_name = _('box')
        verbose_name_plural = _('boxes')

    def __str__(self):
        return self.label


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
    box = models.ForeignKey(Box, on_delete=models.PROTECT, verbose_name=_('box'))
    item = models.ForeignKey('orders.OrderItem', on_delete=models.PROTECT, verbose_name=_('item'))
    start_at = models.DateTimeField(_('start'), default=timezone.now)
    end_at = models.DateTimeField(_('end'), null=True, blank=True)

    objects = TagAssignmentQuerySet.as_manager()

    class Meta:
        verbose_name = _('tag assignment')
        verbose_name_plural = _('tag assignments')
        constraints = [
            models.UniqueConstraint(
                fields=['tag'], condition=Q(end_at__isnull=True), name='one_open_assignment_per_tag',
            ),
            models.UniqueConstraint(
                fields=['box'], condition=Q(end_at__isnull=True), name='one_open_assignment_per_box',
            ),
            models.CheckConstraint(
                condition=Q(end_at__isnull=True) | Q(end_at__gt=F('start_at')), name='end_after_start',
            ),
        ]

    def __str__(self):
        return f'{self.tag} / {self.box} / {self.item}'

    def release(self):
        if self.end_at is None:
            self.end_at = timezone.now()
            self.save(update_fields=['end_at'])


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
    device = models.ForeignKey(ScannerDevice, on_delete=models.PROTECT, verbose_name=_('device'))
    tag_uid = models.CharField(_('tag UID'), max_length=100)
    process = models.ForeignKey(Process, on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('process'))
    event_type = models.CharField(_('event type'), max_length=20, choices=Type.choices)
    corrects = models.ForeignKey(
        'self', on_delete=models.PROTECT, null=True, blank=True, verbose_name=_('corrects'),
    )

    class Meta:
        verbose_name = _('event')
        verbose_name_plural = _('events')
        ordering = ['occurred_at']

    def __str__(self):
        return f'{self.occurred_at:%Y-%m-%d %H:%M} {self.event_type} {self.tag_uid}'

    def clean(self):
        self.tag_uid = self.tag_uid.strip().upper()
        if self.event_type == self.Type.CORRECTION:
            if not self.corrects_id:
                raise ValidationError({'corrects': _('A correction must reference the event it corrects.')})
            if self.process_id:
                raise ValidationError({'process': _('A correction must not have a process.')})
        elif self.event_type in self.REQUIRED_KIND:
            kind = self.REQUIRED_KIND[self.event_type]
            if not self.process_id or self.process.kind != kind:
                raise ValidationError({'process': _('This event type requires a process of kind: %(kind)s.') % {'kind': kind.label}})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(_('Events are append-only and cannot be changed.'))
        self.tag_uid = self.tag_uid.strip().upper()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_('Events are append-only and cannot be deleted.'))
