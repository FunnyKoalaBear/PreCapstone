from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return str(self.user)


class SiteSetting(models.Model):
    """The one row (pk=1) of thresholds that planners can change."""
    FIELDS = ('silent_after_hours', 'void_window_days', 'min_rate_quantity')

    silent_after_hours = models.PositiveIntegerField(
        _('silent after (hours)'), default=24, validators=[MinValueValidator(1), MaxValueValidator(168)],
        help_text=_('A scanner that has not reported for this many hours is marked silent on the dashboard and the Devices list.'))
    void_window_days = models.PositiveIntegerField(
        _('void window (days)'), default=7, validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text=_('The dashboard counts the events that were voided within this many days.'))
    min_rate_quantity = models.PositiveIntegerField(
        _('minimum pieces for a rate'), default=20, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text=_('A defect rate from fewer pieces than this is greyed out as "few data" in the analysis.'))

    class Meta:
        verbose_name = _('site setting')

    def __str__(self):
        return 'Site settings'

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def save(self, *args, **kwargs):
        self.pk = 1  # never a second row
        super().save(*args, **kwargs)

    def reset(self):
        """Back to the defaults (not saved)."""
        for name in self.FIELDS:
            setattr(self, name, self._meta.get_field(name).default)
