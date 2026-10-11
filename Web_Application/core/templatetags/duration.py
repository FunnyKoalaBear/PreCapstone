from django import template

register = template.Library()


@register.filter
def duration(td):
    """Format a timedelta as '2d 3h', '4h 12m', '35m' or '<1m'."""
    if td is None:
        return ''
    mins = int(td.total_seconds() // 60)
    d, h, m = mins // 1440, mins // 60 % 24, mins % 60
    if d:
        return f'{d}d {h}h'
    if h:
        return f'{h}h {m}m'
    return f'{m}m' if m else '<1m'
