from django import template
from django.utils.translation import gettext as _

register = template.Library()


@register.filter
def duration(td):
    """Format a timedelta as '2d 3h', '4h 12m', '35m' or '<1m' (translated)."""
    if td is None:
        return ''
    mins = int(td.total_seconds() // 60)
    d, h, m = mins // 1440, mins // 60 % 24, mins % 60
    if d:
        return _('%(d)dd %(h)dh') % {'d': d, 'h': h}
    if h:
        return _('%(h)dh %(m)dm') % {'h': h, 'm': m}
    return _('%(m)dm') % {'m': m} if m else _('<1m')
