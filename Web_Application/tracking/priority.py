from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.utils.translation import ngettext

from .status import DONE, STEP_AT_VENDOR, days_late, due_back

DAYS_PER_STEP = 1  # days assumed for a step without vendor lead days
WATCH_SLACK = 2  # slack (days) at or below this is WATCH

OVERDUE, CRITICAL, WATCH, ON_TRACK = 'OVERDUE', 'CRITICAL', 'WATCH', 'ON_TRACK'
TIERS = [OVERDUE, CRITICAL, WATCH, ON_TRACK]
TIER_LABELS = {OVERDUE: gettext_lazy('Overdue'), CRITICAL: gettext_lazy('Critical'), WATCH: gettext_lazy('Watch'), ON_TRACK: gettext_lazy('On track')}


def priority(order, status, today=None):
    """Delay risk of one item: tier, days_left, days_needed, slack and reasons."""
    today = today or timezone.localdate()
    left = [r for r in status.rows if r.state != DONE]
    needed, reasons = 0, []
    for r in left:
        lead = r.step.process.vendor_lead_days
        if r.state == STEP_AT_VENDOR:
            back = due_back(r.started_at, lead)
            late = days_late(back, today)
            needed += max((back - today).days, 0) if back else DAYS_PER_STEP
            if late:
                reasons.append(ngettext('Vendor %(n)d day late', 'Vendor %(n)d days late', late) % {'n': late})
        else:
            needed += lead or DAYS_PER_STEP
    days_left = (order.due_date - today).days
    slack = days_left - needed
    tier = OVERDUE if days_left < 0 else CRITICAL if slack < 0 else WATCH if slack <= WATCH_SLACK else ON_TRACK
    head = _('Due in %(d)d days, %(s)d steps left') if days_left >= 0 else _('Overdue by %(d)d days, %(s)d steps left')
    reasons.insert(0, head % {'d': abs(days_left), 's': len(left)})
    if status.warnings:
        reasons.append(_('%(n)d warnings') % {'n': len(status.warnings)})
    return {'tier': tier, 'days_left': days_left, 'days_needed': needed, 'slack': slack, 'reasons': reasons}
