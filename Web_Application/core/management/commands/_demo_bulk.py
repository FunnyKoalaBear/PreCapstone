import random
from datetime import timedelta
from math import sin

from django.db.models import F, Max
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Event, Machine, Tag, TagAssignment, Worker
from tracking.services import record_event

SEED = 2610
N_SHIPPED, N_CANCELLED = 75, 3
# open orders by target state; the due-date offsets (days from today) cycle through DUE_OFFSETS
OPEN_MIX = {'not_started': 5, 'waiting': 8, 'in_progress': 9, 'at_vendor': 7, 'paused': 3, 'complete': 6}
DUE_OFFSETS = (-6, -2, 1, 2, 3, 5, 8, 12, 18, 25, 40)

CUSTOMERS = (('青葉精機', 'AO'), ('大和工業', 'YT'), ('富士エンジニアリング', 'FJ'), ('日の出産業', 'HD'))
PLAN_DAYS = {'Material': 2, 'Milling': 2, 'Lathe': 2, 'Machining centre': 3, 'Grinding': 2, 'Deburring': 1, 'Inspection': 1, 'Assembly': 2}
SLOW = {'Plating': 1.7, 'Grinding': 1.8, 'Inspection': 1.6, 'Heat treatment': 1.35, 'Coating': 1.2}  # actual / planned time: the bottlenecks
DEFECT_RATE = {'Milling': 0.015, 'Lathe': 0.02, 'Machining centre': 0.02, 'Grinding': 0.05, 'Deburring': 0.03, 'Inspection': 0.01, 'Assembly': 0.03}
MACHINES = {
    'Milling': ('Milling machine 1',), 'Lathe': ('Lathe 1', 'Lathe 2'), 'Machining centre': ('Machining centre 1', 'Machining centre 2'),
    'Grinding': ('Surface grinder 1',), 'Deburring': ('Deburring bench',), 'Inspection': ('Inspection bench',),
}
ALT_VENDOR = {'Plating': '00135', 'Material': '00102'}  # now and then a step uses another vendor or supplier
ROUTES = (
    ['Material', 'Milling', 'Lathe', 'Inspection'],
    ['Material', 'Machining centre', 'Deburring', 'Plating', 'Inspection'],
    ['Material', 'Lathe', 'Heat treatment', 'Grinding', 'Inspection'],
    ['Material', 'Laser cutting', 'Welding', 'Coating', 'Assembly', 'Inspection'],
    ['Milling', 'Lathe', 'Assembly'],
    ['Material', 'Laser cutting', 'Welding', 'Milling', 'Deburring', 'Coating', 'Assembly', 'Inspection'],
)
PARTS = (  # name, part number, drawing number
    ('ブッシュ', 'BSH-410', 'DWG-4101'), ('スペーサ', 'SPC-220', 'DWG-2201'), ('ホルダ', 'HLD-330', 'DWG-3301'), ('アーム', 'ARM-510', 'DWG-5101'),
    ('ローラ', 'RLR-120', 'DWG-1201'), ('ナックル', 'KNK-640', 'DWG-6401'), ('ケース', 'CSE-710', 'DWG-7101'), ('ステー', 'STY-150', 'DWG-1501'),
    ('カラー', 'CLR-260', 'DWG-2601'), ('ブロック', 'BLK-380', 'DWG-3801'), ('ガイド', 'GID-470', 'DWG-4701'), ('ハブ', 'HUB-590', 'DWG-5901'),
    ('クランプ', 'CLP-610', 'DWG-6101'), ('ジョイント', 'JNT-720', 'DWG-7201'), ('ディスク', 'DSK-830', 'DWG-8301'), ('マウント', 'MNT-940', 'DWG-9401'),
)
MATERIALS = ('SS400', 'A5052', 'S45C', 'SUS304')
DAY = timedelta(days=1)


def build_specs(rng, now, vendors):
    """Decide every order (route, dates, events, results) before anything is saved; all times come back from `now`."""
    specs, due_i = [], 0

    def spec(part_i, state, **kw):
        nonlocal due_i
        route = ROUTES[part_i % len(ROUTES)]
        steps = []
        for name in route:
            v = vendors[ALT_VENDOR[name]] if name in ALT_VENDOR and rng.random() < 0.25 else None
            steps.append({'name': name, 'alt': v})
        return {'part': PARTS[part_i], 'qty': rng.choice((10, 20, 30, 50, 80, 120, 200)), 'state': state, 'steps': steps,
                'events': [], 'results': [], **kw}

    for i in range(N_SHIPPED):
        months_ago = rng.uniform(0, 11.6)
        specs.append(spec(rng.randrange(len(PARTS)), 'shipped', ship_at=now - timedelta(days=2 + months_ago * 30.4), months_ago=months_ago))
    for i in range(N_CANCELLED):
        specs.append(spec(rng.randrange(len(PARTS)), 'cancelled', ago=rng.uniform(5, 60)))
    for state, n in OPEN_MIX.items():
        for _ in range(n):
            part_i = rng.randrange(len(PARTS))
            wanted = {'in_progress': 'MACHINING', 'paused': 'MACHINING', 'at_vendor': 'OUTSOURCED'}.get(state)
            while wanted and not any(KINDS[s] == wanted for s in ROUTES[part_i % len(ROUTES)]):
                part_i = rng.randrange(len(PARTS))
            specs.append(spec(part_i, state, due=DUE_OFFSETS[due_i % len(DUE_OFFSETS)]))
            due_i += 1
    return specs


KINDS = {n: Process.Kind.MATERIAL if n == 'Material' else Process.Kind.OUTSOURCED if n in ('Plating', 'Heat treatment', 'Welding', 'Coating', 'Laser cutting')
         else Process.Kind.MACHINING for n in sum(map(list, ROUTES), [])}


def step_times(prev_end, span, kind):
    """(first event, its time, last event, its time) of a step that takes `span` days after prev_end."""
    end = prev_end + span * DAY
    if kind == Process.Kind.MATERIAL:
        return None, None, 'ARRIVED', end
    if kind == Process.Kind.MACHINING:
        return 'STARTED', prev_end + span * 0.4 * DAY, 'FINISHED', end
    return 'SENT_OUT', prev_end + min(span * 0.15, 0.7) * DAY, 'RETURNED', end


def fill_events(rng, s, now, procs):
    """Fill the spec's events, results, step due dates, order due date and assignment window."""
    state, steps = s['state'], s['steps']
    for st in steps:
        st['kind'] = KINDS[st['name']]
        vendor = st['alt'] or procs[st['name']].default_vendor
        st['planned'] = PLAN_DAYS.get(st['name']) or (vendor.lead_days if vendor and vendor.lead_days else 3)
        st['span'] = max(st['planned'] * SLOW.get(st['name'], 1.0) * rng.gauss(1, 0.2), 0.4)
    n = len(steps)
    machining = [i for i, st in enumerate(steps) if st['kind'] == Process.Kind.MACHINING]
    outsourced = [i for i, st in enumerate(steps) if st['kind'] == Process.Kind.OUTSOURCED]
    k = n  # index of the step the order is at (n = all done)
    if state in ('cancelled', 'waiting'):
        k = rng.randrange(1, n)
    elif state == 'not_started':
        k = 0
    elif state == 'at_vendor':
        k = rng.choice(outsourced)
    elif state in ('in_progress', 'paused'):
        k = rng.choice(machining)
    # anchor: the time the last finished step ended
    if state == 'shipped':
        done_end = s['ship_at'] - rng.uniform(0.3, 4) * DAY
        late = rng.gauss(0.5 - 0.25 * (11 - s['months_ago']) + 1.5 * sin((11 - s['months_ago']) * 0.9), 2.5)  # days late: better over the year, with a dip
    elif state == 'complete':
        done_end = now - rng.uniform(0.3, 9) * DAY
    elif state == 'cancelled':
        done_end = now - s['ago'] * DAY
    elif state == 'waiting':
        done_end = now - timedelta(hours=rng.choice((2, 5, 9, 14, 20, 30, 45, 70)))
    elif state == 'at_vendor':
        out_days = steps[k]['planned'] + rng.choice((-3, -1, 0, 1, 2, 4, 6)) if rng.random() < 0.8 else 1
        out_days = max(out_days, 1)
        done_end = now - out_days * DAY - rng.uniform(0.2, 0.6) * DAY
    elif state == 'not_started':
        done_end = now - rng.uniform(0.1, 3) * DAY
    else:  # in_progress, paused
        run = timedelta(hours=rng.choice((1, 3, 6, 10, 18, 30, 40)))
        done_end = now - run - timedelta(hours=rng.uniform(2, 14))
    elapsed = sum(st['span'] for st in steps[:k])
    start = done_end - elapsed * DAY if state != 'not_started' else done_end
    s['start'] = start
    cursor = start
    for i, st in enumerate(steps):
        st['due'] = timezone.localdate(cursor) + timedelta(days=st['planned'])
        if i >= k:
            break
        first, t1, last, t2 = step_times(cursor, st['span'], st['kind'])
        if first:
            s['events'].append((t1, st['name'], first))
        s['events'].append((t2, st['name'], last))
        if st['kind'] == Process.Kind.MACHINING and st['name'] in DEFECT_RATE and rng.random() < 0.85:
            s['results'].append((t2, st['name']))
        cursor = t2
    # the step the order is at now, and the plan for the steps after it
    if state in ('in_progress', 'paused'):
        started = now - run
        s['events'].append((started, steps[k]['name'], 'STARTED'))
        steps[k]['due'] = timezone.localdate(started) + timedelta(days=steps[k]['planned'])
        if state == 'paused':
            s['pause'] = (started + (now - started) * rng.uniform(0.3, 0.9), steps[k]['name'], rng.choice(('Waiting for a tool', 'Material shortage', 'Machine maintenance')))
        elif rng.random() < 0.25:
            s['partial'] = (now - timedelta(minutes=30), steps[k]['name'])
        cursor = started
    elif state == 'at_vendor':
        sent = now - out_days * DAY
        s['events'].append((sent, steps[k]['name'], 'SENT_OUT'))
        steps[k]['due'] = timezone.localdate(sent) + timedelta(days=steps[k]['planned'])
        cursor = sent
    if k < n and state != 'not_started':
        nxt = timezone.localdate(cursor)
        for st in steps[k + (1 if state in ('in_progress', 'paused', 'at_vendor') else 0):]:
            nxt += timedelta(days=st['planned'])
            st['due'] = nxt
    elif state == 'not_started':
        nxt = timezone.localdate(start)
        for st in steps:
            nxt += timedelta(days=st['planned'])
            st['due'] = nxt
    # order due date and the end of the tag assignment
    today = timezone.localdate(now)
    if state == 'shipped':
        s['due'] = max(timezone.localdate(s['ship_at']) - timedelta(days=round(late)), timezone.localdate(start) + timedelta(days=3))
        s['end'] = s['ship_at']
    else:
        s['due'] = today + timedelta(days=s['due']) if state != 'cancelled' else today + timedelta(days=rng.choice((-3, 4, 15)))
        s['end'] = done_end if state == 'cancelled' else None
    if state == 'cancelled':
        s['cancel'] = (done_end + rng.uniform(0.1, 0.5) * DAY, rng.choice(('Withdrawn by the customer', 'Design changed', 'Duplicate order')))
        s['end'] = s['cancel'][0]
    return s


def seed_bulk(procs, vendors, devices, stdout=None):
    """Create 100+ orders: a year of shipped ones plus every open state, all events through record_event."""
    rng, now = random.Random(SEED), timezone.now()
    specs = [fill_events(rng, s, now, procs) for s in build_specs(rng, now, vendors)]
    specs.sort(key=lambda s: s['start'])
    customers = [Customer.objects.get_or_create(name=f'DEMO {n}')[0] for n, _p in CUSTOMERS]
    people = list(Worker.objects.all())
    machines = {m.name: m for m in Machine.objects.all()}
    voids = 0
    seq, tag_n = {}, 0
    for s in specs:
        c = rng.randrange(len(customers))
        prefix = timezone.localtime(s['start']).strftime('%y%m')
        last = Order.objects.filter(job_number__startswith=prefix + '-').aggregate(m=Max('job_number'))['m']
        seq[c] = seq.get(c, 0) + 1
        order = Order.objects.create(
            job_number=f'{prefix}-{int(last.split("-")[1]) + 1 if last else 1:03d}', customer=customers[c],
            customer_order_number=f'{CUSTOMERS[c][1]}-B{seq[c]:04d}', due_date=s['due'])
        Order.objects.filter(pk=order.pk).update(created_at=s['start'])
        name, number, drawing = s['part']
        item = OrderItem.objects.create(order=order, part_name=name, part_number=number, drawing_number=drawing, revision=rng.choice('ABC'),
                                        quantity=s['qty'], material=rng.choice(MATERIALS))
        steps = {}
        for i, st in enumerate(s['steps'], 1):
            proc = procs[st['name']]
            vendor = st['alt'] or (vendors[rng.choice(('00101', '00102', '00103'))] if st['name'] == 'Material' else proc.default_vendor)
            steps[st['name']] = RouteStep.objects.create(item=item, sequence=i, process=proc, vendor=vendor, due_date=st['due'])
        tag_n += 1
        uid = f'{0xD0000000 + tag_n * 7919:08X}'
        TagAssignment.objects.create(
            tag=Tag.objects.get_or_create(uid=uid, defaults={'note': 'DEMO'})[0],
            item=item, start_at=s['start'], end_at=s['end'])
        for when, pname, etype in s['events']:
            record_event(devices[0] if KINDS[pname] == Process.Kind.MACHINING else devices[1], uid, procs[pname], etype, occurred_at=when)
        for when, pname in s['results']:
            made = [rng.random() < DEFECT_RATE[pname] for _ in range(s['qty'])]
            machine = machines.get(rng.choice(MACHINES[pname])) if pname in MACHINES else None
            event = Event(device=devices[0], tag_uid=uid, process=procs[pname], event_type=Event.Type.RESULT, occurred_at=when,
                          quantity=s['qty'], defects=sum(made), worker=rng.choice(people), machine=machine)
            event.full_clean()
            event.save()
        extra = []
        if 'pause' in s:
            when, pname, why = s['pause']
            extra.append(Event(device=devices[0], tag_uid=uid, process=procs[pname], event_type=Event.Type.PAUSED, occurred_at=when, reason=why, is_manual=True))
        if 'partial' in s:
            when, pname = s['partial']
            extra.append(Event(device=devices[0], tag_uid=uid, process=procs[pname], event_type=Event.Type.PARTIAL, occurred_at=when, reason='Half of the pieces', is_manual=True))
        if s['state'] in ('waiting', 'in_progress') and voids < 4 and rng.random() < 0.5:  # a wrong scan, voided by a correction
            voids += 1
            name = next(st['name'] for st in s['steps'] if st['kind'] == Process.Kind.MACHINING)
            wrong, _a = record_event(devices[0], uid, procs[name], 'FINISHED', occurred_at=now - timedelta(hours=3))
            extra.append(Event(device=devices[0], tag_uid=uid, event_type=Event.Type.CORRECTION, corrects=wrong, occurred_at=now - timedelta(hours=2), reason='Wrong scan'))
        if s['state'] == 'shipped':
            extra.append(Event(device=devices[1], event_type=Event.Type.SHIPPED, occurred_at=s['ship_at'], item=item))
        if 'cancel' in s:
            extra.append(Event(device=devices[1], event_type=Event.Type.CANCELLED, occurred_at=s['cancel'][0], reason=s['cancel'][1], item=item))
        for event in extra:
            event.full_clean()
            event.save()
    # events were "received" when they happened, so the dashboard's events-today count is realistic
    Event.objects.filter(device__in=devices, received_at__gt=F('occurred_at')).update(received_at=F('occurred_at'))
    return len(specs)
