from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import get_language

from orders.models import OrderItem, RouteStep

from .models import Event, TagAssignment
from .planning import step_plan
from .status import DONE, item_statuses, timeline

SEVERITY = ((60, 'severe'), (30, 'major'), (10, 'minor'))  # overrun % above which a step is minor / major / severe


def severity(pct):
    return next((name for limit, name in SEVERITY if pct is not None and pct > limit), '')


@dataclass
class Filters:
    start: object = None
    end: object = None
    customer: int = None
    process: int = None
    vendor: int = None

    @classmethod
    def from_params(cls, g):
        num = lambda k: int(g[k]) if g.get(k, '').isdigit() else None
        return cls(parse_date(g.get('date_from', '')), parse_date(g.get('date_to', '')), num('customer'), num('process'), num('vendor'))


def _name(process):
    return process.name_ja if process.name_ja and get_language().startswith('ja') else process.name


def step_facts(f):
    """One dict per finished step of every box: process, planned/actual days, processing time and wait before it."""
    items = OrderItem.objects.select_related('order__customer')
    if f.customer:
        items = items.filter(order__customer_id=f.customer)
    out, items = [], list(items)
    statuses = item_statuses(items)
    for item in items:
        for box in statuses[item.pk].boxes:
            for row, plan, t in zip(box.rows, step_plan(box.rows), timeline(box.rows)):
                step = row.step
                if row.state != DONE or not row.finished_at or (f.process and step.process_id != f.process):
                    continue
                vendor = step.vendor_id or step.process.default_vendor_id
                if f.vendor and vendor != f.vendor:
                    continue
                day = timezone.localdate(row.finished_at)
                if (f.start and day < f.start) or (f.end and day > f.end):
                    continue
                out.append({'process': step.process, **plan, 'processing': t['duration'], 'wait': t['wait_before'],
                            'day': day, 'customer': item.order.customer_id, 'vendor': vendor})
    return out


def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values, timedelta()) / len(values) if values else None


def plan_rows(facts, sort='process'):
    """Per process: finished steps with a plan, average planned / actual / difference in days and the overrun %."""
    groups = {}
    for x in facts:
        if x['diff'] is not None:
            groups.setdefault(x['process'].pk, []).append(x)
    rows = []
    for g in groups.values():
        planned, actual = sum(x['planned'] for x in g) / len(g), sum(x['actual'] for x in g) / len(g)
        pct = (actual - planned) * 100 / planned if planned > 0 else None
        rows.append({'process': g[0]['process'], 'n': len(g), 'planned': planned, 'actual': actual, 'diff': actual - planned,
                     'pct': pct, 'severity': severity(pct)})
    if sort == 'delay':
        rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0)))
    else:
        rows.sort(key=lambda r: r['process'].name)
    return rows


def slowest(rows, n=3):
    """The n steps with the biggest overrun (only those that overrun); the first one is the worst."""
    late = sorted((r for r in rows if r['pct'] and r['pct'] > 0), key=lambda r: -r['pct'])
    return late[:n]


def result_rows(f, facts):
    """Per process: recorded quantity and defects (voided results left out), average processing time and wait, and the longest of each."""
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    events = list(Event.objects.filter(event_type=Event.Type.RESULT).exclude(pk__in=corrected).select_related('process'))
    if f.process:
        events = [e for e in events if e.process_id == f.process]
    if f.start or f.end:
        events = [e for e in events if (not f.start or timezone.localdate(e.occurred_at) >= f.start) and (not f.end or timezone.localdate(e.occurred_at) <= f.end)]
    if f.customer or f.vendor:
        own = owners(events)
        events = [e for e in events if (not f.customer or own[e.pk][0] == f.customer) and (not f.vendor or own[e.pk][1] == f.vendor)]
    groups = {}
    for e in events:
        g = groups.setdefault(e.process_id, {'process': e.process, 'records': 0, 'quantity': 0, 'defects': 0})
        g['records'] += 1
        g['quantity'] += e.quantity
        g['defects'] += e.defects or 0
    for p in {x['process'].pk: x['process'] for x in facts}.values():
        groups.setdefault(p.pk, {'process': p, 'records': 0, 'quantity': 0, 'defects': 0})
    rows = []
    for g in groups.values():
        mine = [x for x in facts if x['process'].pk == g['process'].pk]
        rows.append({**g, 'process': _name(g['process']), 'rate': g['defects'] * 100 / g['quantity'] if g['quantity'] else 0,
                     'processing': _avg(x['processing'] for x in mine), 'wait': _avg(x['wait'] for x in mine), 'hot': set()})
    rows.sort(key=lambda r: r['process'])
    for key, flag in (('processing', 'processing'), ('wait', 'wait')):
        known = [r for r in rows if r[key]]
        if known:
            max(known, key=lambda r: r[key])['hot'].add(flag)
    total = {k: sum(r[k] for r in rows) for k in ('records', 'quantity', 'defects')}
    total['rate'] = total['defects'] * 100 / total['quantity'] if total['quantity'] else 0
    return rows, total


def owners(events):
    """{event pk: (customer id, vendor id)} from the tag assignment at that moment and the route step of the process."""
    by_uid = {}
    for a in TagAssignment.objects.filter(tag__uid__in={e.tag_uid for e in events}).select_related('item__order', 'tag'):
        by_uid.setdefault(a.tag.uid, []).append(a)
    steps = {(s.item_id, s.process_id): s.vendor_id or s.process.default_vendor_id
             for s in RouteStep.objects.filter(item__in={a.item_id for v in by_uid.values() for a in v}).select_related('process')}
    out = {}
    for e in events:
        a = next((a for a in by_uid.get(e.tag_uid, []) if a.start_at <= e.occurred_at and (not a.end_at or a.end_at > e.occurred_at)), None)
        out[e.pk] = (a.item.order.customer_id, steps.get((a.item_id, e.process_id))) if a else (None, None)
    return out
