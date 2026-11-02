import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from tracking.models import Event, ScannerDevice, Tag, TagAssignment
from tracking.services import cancel_item, correct_event, record_event, ship_item

K = Process.Kind
# (name, kind): every route starts with Material, then mixes the rest
PROCESSES = [
    ('SCALE Material', K.MATERIAL), ('SCALE Milling', K.MACHINING), ('SCALE Lathe', K.MACHINING), ('SCALE Grinding', K.MACHINING),
    ('SCALE Inspection', K.MACHINING), ('SCALE Plating', K.OUTSOURCED), ('SCALE Heat treatment', K.OUTSOURCED), ('SCALE Coating', K.OUTSOURCED),
]
# State weights: untouched, in progress, complete, shipped, cancelled
STATES = ['new', 'progress', 'complete', 'shipped', 'cancelled']
WEIGHTS = [15, 50, 20, 8, 7]
PAIRS = {K.MATERIAL: (Event.Type.ARRIVED,), K.MACHINING: (Event.Type.STARTED, Event.Type.FINISHED), K.OUTSOURCED: (Event.Type.SENT_OUT, Event.Type.RETURNED)}


def is_scratch(name):
    return name.startswith(('scratch', 'test_'))


class Command(BaseCommand):
    help = 'Fill a SCRATCH database with many items and events for performance work (dev only; not demo data). Running it again adds more.'

    def add_arguments(self, parser):
        parser.add_argument('--items', type=int, default=5000)
        parser.add_argument('--events', type=int, default=100000, help='Scan events to reach (repeat scans fill the gap).')
        parser.add_argument('--seed', type=int, default=1)
        parser.add_argument('--verify', action='store_true', help='Seed nothing; compare every item against the reference implementation.')
        parser.add_argument('--slow', action='store_true', help='Write every scan through record_event (about 25 ms each) instead of bulk inserts.')

    def handle(self, *args, items, events, seed, slow, verify, **options):
        name = settings.DATABASES['default']['NAME']
        if not is_scratch(name):
            raise CommandError(f'Refusing to run on database "{name}": its name must start with "scratch" or "test_".')
        if verify:
            return self.verify()
        self.rng, self.tag_no, self.slow = random.Random(seed), Tag.objects.count(), slow
        self.now = timezone.now()
        self.build(items)
        self.stdout.write(f'Created {OrderItem.objects.count()} items. Writing events...')
        self.write_events(events)
        self.stdout.write(self.style.SUCCESS(f'{OrderItem.objects.count()} items, {Event.objects.count()} events in {name}.'))

    def verify(self):
        from tracking.status import item_statuses
        from tracking.tests_reference import first_difference
        items = list(OrderItem.objects.all())
        bad = first_difference(items, item_statuses(items))
        if bad:
            raise CommandError(f'Item {bad[0].pk} differs from the reference in {bad[1]}')
        self.stdout.write(self.style.SUCCESS(f'All {len(items)} items match the reference implementation.'))

    def uid(self):
        self.tag_no += 1
        return f'{self.tag_no:08X}'

    def build(self, n_items):
        rng = self.rng
        self.device = ScannerDevice.objects.get_or_create(name='SCALE-1')[0]
        self.user = User.objects.filter(username='scale_admin').first() or User.objects.create_superuser('scale_admin', password='x')
        procs = [Process.objects.get_or_create(name=n, kind=k, defaults={'vendor_lead_days': 5 if k == K.OUTSOURCED else None})[0] for n, k in PROCESSES]
        vendors = [Vendor.objects.get_or_create(code=f'{200 + i:05d}', defaults={'name': f'SCALE vendor {i}', 'kind': Vendor.Kind.VENDOR, 'lead_days': 4 + i})[0] for i in range(3)]
        customers = [Customer.objects.get_or_create(name=f'SCALE customer {i}')[0] for i in range(20)]
        today = timezone.localdate()
        n_orders, base = max(n_items // 5, 1), Order.objects.count()
        orders = Order.objects.bulk_create([
            Order(job_number=f'S{base + i:06d}', customer=customers[i % len(customers)], due_date=today + timedelta(days=rng.randint(-20, 60)))
            for i in range(n_orders)])
        items = OrderItem.objects.bulk_create([
            OrderItem(order=orders[i % n_orders], item_no=i // n_orders + 1, part_name=f'Part {i}', part_number=f'P{i % 900:04d}',
                      quantity=rng.randint(1, 200), box_count=rng.choices([1, 2, 3], [85, 10, 5])[0])
            for i in range(n_items)])
        steps, self.routes = [], {}
        for item in items:
            route = [procs[0]] + rng.sample(procs[1:], rng.randint(3, 6))
            self.routes[item.pk] = route
            for seq, p in enumerate(route, 1):
                steps.append(RouteStep(item=item, sequence=seq, process=p, vendor=rng.choice(vendors) if p.kind == K.OUTSOURCED else None))
        RouteStep.objects.bulk_create(steps)
        self.items = items
        self.plan = []  # (item, state) in creation order

    def box_events(self, route, upto, half):
        """(process, type, when) for the first `upto` steps; step `upto` is left half done when `half`."""
        t = self.now - timedelta(days=self.rng.randint(5, 40))
        out = []
        for i, p in enumerate(route[:upto + 1]):
            types = PAIRS[p.kind]
            for ty in (types if i < upto else types[:1] if half else ()):
                t += timedelta(hours=self.rng.uniform(0.5, 30))
                out.append((p, ty, t))
        return out

    def write_events(self, target):
        rng, scans = self.rng, []  # scans: (uid, process, type, when)
        for item in self.items:
            state = rng.choices(STATES, WEIGHTS)[0]
            route, k = self.routes[item.pk], item.box_count
            linked = k if state != 'new' else rng.choice([0, k, k])
            if state == 'progress' and k > 1:
                linked = rng.randint(1, k)
            for b in range(linked):
                full = state in ('complete', 'shipped')
                evs = [] if state == 'new' else self.box_events(route, len(route) if full else rng.randint(1, len(route) - 1), rng.random() < 0.5)
                start = (evs[0][2] if evs else self.now - timedelta(days=1)) - timedelta(hours=1)
                cut = rng.randint(1, len(evs) - 1) if len(evs) > 1 and rng.random() < 0.03 else None  # a replaced tag
                tag = first = Tag.objects.create(uid=self.uid())
                if cut:
                    split = evs[cut][2] - timedelta(minutes=1)
                    old = TagAssignment.objects.create(tag=tag, item=item, start_at=start, end_at=split)
                    Tag.objects.filter(pk=tag.pk).update(active=False)
                    tag = Tag.objects.create(uid=self.uid())
                    TagAssignment.objects.create(tag=tag, item=item, start_at=split, replaces=old)
                else:
                    TagAssignment.objects.create(tag=tag, item=item, start_at=start)
                for i, (p, ty, when) in enumerate(evs):
                    scans.append((first.uid if cut and i < cut else tag.uid, p, ty, when))
            self.plan.append((item, state))
        scans.sort(key=lambda s: s[3])
        # Repeat scans (a card read twice) fill the gap to the target
        repeats = []
        while len(scans) + len(repeats) < target and scans:
            uid, p, ty, when = rng.choice(scans)
            repeats.append((uid, p, ty, when + timedelta(minutes=1)))
        rows = [Event(device=self.device, tag_uid=uid, process=p, event_type=ty, occurred_at=when) for uid, p, ty, when in scans + repeats]
        if self.slow:
            saved = [record_event(self.device, e.tag_uid, e.process, e.event_type, e.occurred_at)[0] for e in rows]
        else:
            saved = Event.objects.bulk_create(rows, batch_size=5000)
        voids = saved[37::100]
        for ev in voids:
            correct_event(ev, self.user, 'scale data')
        for item, state in self.plan:
            if state == 'shipped':
                ship_item_safe(self.user, item)
            elif state == 'cancelled':
                cancel_item(self.user, item, 'scale data')


def ship_item_safe(user, item):
    """Ship the item if it ended up complete (a void may have changed that)."""
    try:
        ship_item(user, item)
    except ValidationError:
        pass
