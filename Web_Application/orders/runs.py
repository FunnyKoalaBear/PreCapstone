from django.utils import timezone

from tracking.status import CANCELLED, COMPLETE, SHIPPED, item_status, timeline

from .models import OrderItem

LIMIT = 5


def previous_runs(item, limit=LIMIT):
    """The latest earlier, not cancelled items with the same part number (newest first), with what each step really took."""
    if not item.part_number:
        return []
    earlier = OrderItem.objects.filter(part_number__iexact=item.part_number, pk__lt=item.pk).select_related('order').order_by('-pk')
    runs = []
    for run in earlier:
        st = item_status(run)
        if st.code == CANCELLED:
            continue
        # Same per-step duration as the order page: started to finished, sent out to returned for outsourced steps
        steps = [{'process': r.step.process, 'vendor': r.step.vendor_label, 'duration': t['duration']} for r, t in zip(st.rows, timeline(st.rows))]
        starts = [r.started_at for r in st.rows if r.started_at]
        finished = max((r.finished_at for r in st.rows if r.finished_at), default=None) if st.code in (COMPLETE, SHIPPED) else None
        first, last = (min(starts) if starts else None), st.last_event_at
        runs.append({
            'item': run, 'order': run.order, 'steps': steps, 'finished': timezone.localdate(finished) if finished else None,
            'total_days': (timezone.localdate(last) - timezone.localdate(first)).days if first and last else None,
        })
        if len(runs) == limit:
            break
    return runs
