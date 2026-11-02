from datetime import timedelta

from .models import SiteSetting


def silent_after():
    """How long a scanner may stay unseen before it counts as silent."""
    return timedelta(hours=SiteSetting.get().silent_after_hours)


def void_window():
    """How far back the dashboard counts voided events."""
    return timedelta(days=SiteSetting.get().void_window_days)


def min_rate_quantity():
    """A defect rate from fewer pieces than this is greyed out as 'few data'."""
    return SiteSetting.get().min_rate_quantity
