from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from core.rules import min_rate_quantity
from core.templatetags.duration import duration
from orders.models import OrderItem, Process, RouteStep, Vendor

from .models import Event, TagAssignment
from .planning import step_plan
from .status import DONE, item_statuses, timeline

CHECK_FACTOR, CHECK_MIN_STEPS = 3, 5  # a step is flagged above 3x its process median, once the process has 5 finished steps
RECORD_LIMIT, RECORD_CAP = 200, 2000
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
                parts = [v for v in (t['duration'], t['wait_before']) if v is not None]
                out.append({'process': step.process, **plan, 'processing': t['duration'], 'wait': t['wait_before'],
                            'total': sum(parts, timedelta()) if parts else None, 'day': day, 'customer': item.order.customer_id,
                            'vendor': vendor, 'item': item, 'seq': step.sequence, 'box': box.caption})
    flag_scans(out)
    return out


def _median(values):
    """Median of numbers or timedeltas (None values skipped); None when there are none."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def process_stats(facts):
    """{process id: finished steps, median processing time, median wait, median total}."""
    groups = {}
    for x in facts:
        groups.setdefault(x['process'].pk, []).append(x)
    return {pk: {'n': len(g), **{k: _median(x[k] for x in g) for k in ('processing', 'wait', 'total')}} for pk, g in groups.items()}


def flag_scans(facts):
    """Add x['flags']: processing time or wait above 3x the process median (only for processes with 5+ finished steps)."""
    stats = process_stats(facts)
    for x in facts:
        st, x['flags'] = stats[x['process'].pk], []
        if st['n'] < CHECK_MIN_STEPS:
            continue
        for key in ('processing', 'wait'):
            if st[key] and x[key] is not None and x[key] > CHECK_FACTOR * st[key]:
                x['flags'].append({'key': key, 'value': x[key], 'median': st[key]})


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
        rows.append({'process': g[0]['process'], 'pk': g[0]['process'].pk, 'n': len(g), 'planned': planned, 'actual': actual,
                     'diff': actual - planned, 'median_actual': _median(x['actual'] for x in g), 'pct': pct, 'severity': severity(pct)})
    if sort == 'delay':
        rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0)))
    else:
        rows.sort(key=lambda r: r['process'].name)
    return rows


def vendor_rows(facts):
    """Per vendor: finished steps with a plan, average planned, average and median actual days, overrun %; biggest overrun first."""
    groups = {}
    for x in facts:
        if x['diff'] is not None and x['vendor']:
            groups.setdefault(x['vendor'], []).append(x)
    names = Vendor.objects.in_bulk(groups)
    rows = []
    for pk, g in groups.items():
        planned, actual = sum(x['planned'] for x in g) / len(g), sum(x['actual'] for x in g) / len(g)
        pct = (actual - planned) * 100 / planned if planned > 0 else None
        rows.append({'vendor': names[pk], 'n': len(g), 'planned': planned, 'actual': actual,
                     'median_actual': _median(x['actual'] for x in g), 'pct': pct, 'severity': severity(pct)})
    rows.sort(key=lambda r: (r['pct'] is None, -(r['pct'] or 0), str(r['vendor'])))
    return rows


def slowest(rows, n=3):
    """The n steps with the biggest overrun (only those that overrun); the first one is the worst."""
    late = sorted((r for r in rows if r['pct'] and r['pct'] > 0), key=lambda r: -r['pct'])
    return late[:n]


def result_events(f):
    """RESULT events (voided ones left out) that match the page filters."""
    corrected = Event.objects.filter(event_type=Event.Type.CORRECTION).values('corrects_id')
    events = list(Event.objects.filter(event_type=Event.Type.RESULT).exclude(pk__in=corrected).select_related('process', 'worker', 'machine', 'defect_cause'))
    if f.process:
        events = [e for e in events if e.process_id == f.process]
    if f.start or f.end:
        events = [e for e in events if (not f.start or timezone.localdate(e.occurred_at) >= f.start) and (not f.end or timezone.localdate(e.occurred_at) <= f.end)]
    if f.customer or f.vendor:
        own = owners(events)
        events = [e for e in events if (not f.customer or own[e.pk][0] == f.customer) and (not f.vendor or own[e.pk][1] == f.vendor)]
    return events


def result_rows(f, facts, events=None):
    """Per process: recorded quantity and defects (voided results left out), average processing time and wait, and the longest of each."""
    events = result_events(f) if events is None else events
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
        rows.append({**g, 'process': _name(g['process']), 'pk': g['process'].pk, 'rate': g['defects'] * 100 / g['quantity'] if g['quantity'] else 0,
                     'n': len(mine), 'processing': _avg(x['processing'] for x in mine), 'wait': _avg(x['wait'] for x in mine),
                     'processing_median': _median(x['processing'] for x in mine), 'wait_median': _median(x['wait'] for x in mine), 'hot': set()})
    rows.sort(key=lambda r: r['process'])
    for key, flag in (('processing', 'processing'), ('wait', 'wait')):
        known = [r for r in rows if r[key]]
        if known:
            max(known, key=lambda r: r[key])['hot'].add(flag)
    total = {k: sum(r[k] for r in rows) for k in ('records', 'quantity', 'defects')}
    total['rate'] = total['defects'] * 100 / total['quantity'] if total['quantity'] else 0
    return rows, total


def assigned(events):
    """{event pk: the TagAssignment (with item, order, customer) active for the event's tag at that moment, or None}; one query."""
    by_uid = {}
    for a in TagAssignment.objects.filter(tag__uid__in={e.tag_uid for e in events}).select_related('item__order__customer', 'tag'):
        by_uid.setdefault(a.tag.uid, []).append(a)
    return {e.pk: next((a for a in by_uid.get(e.tag_uid, []) if a.start_at <= e.occurred_at and (not a.end_at or a.end_at > e.occurred_at)), None) for e in events}


def owners(events):
    """{event pk: (customer id, vendor id)} from the tag assignment at that moment and the route step of the process."""
    found = assigned(events)
    steps = {(s.item_id, s.process_id): s.vendor_id or s.process.default_vendor_id
             for s in RouteStep.objects.filter(item__in={a.item_id for a in found.values() if a}).select_related('process')}
    return {e.pk: (a.item.order.customer_id, steps.get((a.item_id, e.process_id))) if a else (None, None) for e in events for a in [found[e.pk]]}


RESULT_ORDERS = ('total', 'wait', 'processing', 'ratio', 'item', 'process')
PLAN_ORDERS = ('overrun', 'planned', 'actual', 'diff', 'item', 'process')


def _cause(x):
    if x['process'].kind == Process.Kind.OUTSOURCED:
        return _('At vendor')
    return _('Waited before the step') if (x['wait'] or timedelta()) > (x['processing'] or timedelta()) else _('Processing')


def _flag_title(flag):
    what = _('Processing time') if flag['key'] == 'processing' else _('Wait before')
    return _('%(what)s %(value)s is more than %(n)s times the process median %(median)s') % {
        'what': what, 'value': duration(flag['value']), 'n': CHECK_FACTOR, 'median': duration(flag['median'])}


def record_rows(facts, tab, sel, order):
    """One row per finished step of the selected processes, built from the same facts; sorted by `order` (whitelisted)."""
    stats = process_stats(facts)
    rows = []
    for x in facts:
        if x['process'].pk not in sel or (tab == 'plan' and x['diff'] is None):
            continue
        item = x['item']
        row = {'item': item, 'job': item.order.job_number, 'box': x['box'], 'seq': x['seq'], 'part': item.part_name,
               'customer': str(item.order.customer), 'process': _name(x['process']),
               'flags': [{'key': f['key'], 'title': _flag_title(f)} for f in x['flags']]}
        if tab == 'plan':
            pct = x['diff'] * 100 / x['planned'] if x['planned'] > 0 else None
            rows.append({**row, 'planned': x['planned'], 'actual': x['actual'], 'diff': x['diff'], 'pct': pct,
                         'severity': severity(pct), 'value': x['actual']})
        else:
            med = stats[x['process'].pk]['total']
            ratio = x['total'] / med if x['total'] is not None and med else None
            rows.append({**row, 'wait': x['wait'], 'processing': x['processing'], 'total': x['total'], 'ratio': ratio,
                         'cause': _cause(x), 'value': int(x['total'].total_seconds()) if x['total'] is not None else ''})
    secs = lambda k: lambda r: (r[k] is None, -(r[k].total_seconds() if r[k] else 0))
    keys = {
        'total': secs('total'), 'wait': secs('wait'), 'processing': secs('processing'),
        'ratio': lambda r: (r['ratio'] is None, -(r['ratio'] or 0)), 'overrun': lambda r: (r['pct'] is None, -(r['pct'] or 0)),
        'planned': lambda r: -r['planned'], 'actual': lambda r: -r['actual'], 'diff': lambda r: -r['diff'],
        'item': lambda r: (r['item'].item_code, r['seq']), 'process': lambda r: (r['process'], r['item'].item_code),
    }
    rows.sort(key=keys[order])
    return rows


def defect_rate_rows(events, key, none_label):
    """Per worker or machine (key = 'worker' / 'machine'): records, quantity, defects and defect rate; highest rate first."""
    groups = {}
    for e in events:
        g = groups.setdefault(getattr(e, f'{key}_id'), {'name': str(getattr(e, key)) if getattr(e, f'{key}_id') else none_label, 'known': bool(getattr(e, f'{key}_id')),
                                                          'id': getattr(e, f'{key}_id') or 'none', 'records': 0, 'quantity': 0, 'defects': 0})
        g['records'] += 1
        g['quantity'] += e.quantity
        g['defects'] += e.defects or 0
    few = min_rate_quantity()
    rows = [{**g, 'rate': g['defects'] * 100 / g['quantity'], 'few': g['quantity'] < few} for g in groups.values()]
    rows.sort(key=lambda r: (-r['rate'], r['name']))
    return rows


def cause_rows(events, none_label):
    """Defects per main cause with share % and cumulative % in Pareto order; 'no cause recorded' comes last."""
    groups = {}
    for e in events:
        if e.defects:
            g = groups.setdefault(e.defect_cause_id, {'name': str(e.defect_cause) if e.defect_cause_id else none_label, 'known': bool(e.defect_cause_id),
                                                              'id': e.defect_cause_id or 'none', 'defects': 0})
            g['defects'] += e.defects
    rows = sorted(groups.values(), key=lambda r: (not r['known'], -r['defects'], r['name']))
    total, running = sum(r['defects'] for r in rows), 0
    for r in rows:
        running += r['defects']
        r['share'], r['cumulative'] = r['defects'] * 100 / total, running * 100 / total
    return rows


DEFECT_ORDERS = ('defects', 'date', 'item', 'part', 'process', 'quantity', 'rate')


def defect_detail(events, field, value, only_defects=True, order='defects', planner=False):
    """Results of one worker / machine / cause (field = 'worker' / 'machine' / 'defect_cause'; value = id, None for 'none recorded').

    Returns the 'by part' rows (with the rate of everyone else on the same part and process, except for causes) and the sorted records.
    Everything is built from the given events with one assignment query, so the tables and the records always agree.
    """
    hit = lambda e: getattr(e, f'{field}_id') == value and (field != 'defect_cause' or bool(e.defects))
    chosen = [e for e in events if hit(e)]
    pool = chosen if field == 'defect_cause' else [e for e in events if e.process_id in {c.process_id for c in chosen}]
    found = assigned(pool)
    part = lambda e: (found[e.pk].item.part_number or found[e.pk].item.part_name) if found[e.pk] else ''
    groups = {}
    for e in chosen:
        g = groups.setdefault((part(e), e.process_id), {'part': part(e), 'process': _name(e.process), 'records': 0, 'quantity': 0, 'defects': 0, 'base_quantity': 0, 'base_defects': 0})
        g['records'] += 1
        g['quantity'] += e.quantity
        g['defects'] += e.defects or 0
    if field != 'defect_cause':
        for e in pool:
            g = None if hit(e) else groups.get((part(e), e.process_id))
            if g:
                g['base_quantity'] += e.quantity
                g['base_defects'] += e.defects or 0
    few = min_rate_quantity()
    parts = [{**g, 'rate': g['defects'] * 100 / g['quantity'], 'few': g['quantity'] < few,
              'base_rate': g['base_defects'] * 100 / g['base_quantity'] if g['base_quantity'] else None} for g in groups.values()]
    parts.sort(key=lambda r: (-r['defects'], -r['rate'], r['part'], r['process']))
    records = []
    for e in chosen:
        if only_defects and not e.defects:
            continue
        a = found[e.pk]
        row = {'event': e, 'item': a.item if a else None, 'job': a.item.order.job_number if a else '', 'part': part(e),
               'customer': str(a.item.order.customer) if a else '', 'process': _name(e.process), 'machine': str(e.machine) if e.machine_id else '',
               'cause': str(e.defect_cause) if e.defect_cause_id else '', 'rate': (e.defects or 0) * 100 / e.quantity}
        if planner:
            row['worker'] = str(e.worker) if e.worker_id else ''
        records.append(row)
    newest = lambda r: -r['event'].occurred_at.timestamp()
    keys = {
        'defects': lambda r: (-(r['event'].defects or 0), newest(r)), 'date': newest, 'rate': lambda r: (-r['rate'], newest(r)),
        'quantity': lambda r: (-r['event'].quantity, newest(r)), 'item': lambda r: (r['item'].item_code if r['item'] else '', newest(r)),
        'part': lambda r: (r['part'], newest(r)), 'process': lambda r: (r['process'], newest(r)),
    }
    records.sort(key=keys[order])
    return parts, records
