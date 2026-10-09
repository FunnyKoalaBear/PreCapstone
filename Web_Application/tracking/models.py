from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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
