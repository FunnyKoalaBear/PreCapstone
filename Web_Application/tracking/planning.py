from django.utils import timezone

from orders.models import OrderItem

from .status import item_status


def step_plan(rows):
    """Per row: planned and actual days counted from the previous step's finish, and their difference (None when unknown).

    Planned = the step's due date minus that finish date; actual = the step's own finish date minus it. Both are JST dates.
    """
    out, prev_end = [], None
    for r in rows:
        start = timezone.localdate(prev_end) if prev_end else None
        planned = (r.step.due_date - start).days if start and r.step.due_date else None
        actual = (timezone.localdate(r.finished_at) - start).days if start and r.finished_at else None
        out.append({'planned': planned, 'actual': actual, 'diff': actual - planned if None not in (planned, actual) else None})
        prev_end = r.finished_at or prev_end
    return out


def process_averages():
    """Per process: number of finished steps with a plan, and the average planned days, actual days and difference."""
    totals = {}
    for item in OrderItem.objects.all():
        for box in item_status(item).boxes:
            for row, plan in zip(box.rows, step_plan(box.rows)):
                if plan['diff'] is None:
                    continue
                t = totals.setdefault(row.step.process_id, {'process': row.step.process, 'n': 0, 'planned': 0, 'actual': 0})
                t['n'] += 1
                t['planned'] += plan['planned']
                t['actual'] += plan['actual']
    return sorted((
        {'process': t['process'], 'n': t['n'], 'planned': t['planned'] / t['n'], 'actual': t['actual'] / t['n'],
         'diff': (t['actual'] - t['planned']) / t['n']}
        for t in totals.values()), key=lambda r: r['process'].name)
