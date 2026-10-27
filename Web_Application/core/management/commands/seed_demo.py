import csv
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Case, ProtectedError, Q, Value, When
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from tracking.models import Box, Event, Machine, ScannerDevice, Tag, TagAssignment, Worker
from tracking.services import record_event
from tracking.status import DONE, item_statuses

from ._demo_bulk import seed_bulk

DATA_DIR = settings.BASE_DIR / 'docs' / 'demo-data'
# default vendor code per process name, and lead days per vendor code (the CSV files carry neither)
DEFAULT_VENDOR = {
    'Material': '00101', 'Milling': '00011', 'Lathe': '00012', 'Machining centre': '00013', 'Grinding': '00014',
    'Deburring': '00015', 'Inspection': '00020', 'Assembly': '00021', 'Plating': '00131', 'Heat treatment': '00132',
    'Welding': '00133', 'Coating': '00134', 'Laser cutting': '00136',
}
LEAD_DAYS = {'00131': 7, '00132': 10, '00133': 5, '00134': 5, '00135': 3, '00136': 3}
MATERIALS = ('SS400', 'A5052', 'S45C', 'SUS304')
MAX_RESULTS = 15  # finished steps that get a result


def read_csv(name):
    with open(DATA_DIR / name, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


CUSTOMERS = ('東洋機械', '北斗製作所')
# 4-byte tag UIDs of the demo scenarios (box labels stay DEMO-xx)
DEMO_UIDS = (
    '0781DD5E', 'F8DF3064', '74BA7571', '8EFBD21A', '57E36722', 'FC3F61E6', '092C4B14',
    '3E180A05', '03193FDF', '414D497D', 'C36F67C6', 'D795A680', 'B15D6B73', '5A1C9E02', '9B27F4D6',
)


class Command(BaseCommand):
    help = 'Create demo orders, tags and scans for reviewing the dashboard (DEBUG only).'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete demo data first, then re-create it.')

    def handle(self, *args, reset=False, **options):
        if not settings.DEBUG:
            raise CommandError('seed_demo only runs when DEBUG is True.')
        with transaction.atomic():  # a failed reset leaves the database unchanged
            removed = self.wipe() if reset else {'orders': 0, 'events': 0, 'processes': 0}
            if not reset and Customer.objects.filter(name__startswith='DEMO ').exists():
                self.stdout.write('Demo data already exists; use --reset to re-create it.')
                return
            orders, events, procs = Order.objects.count(), Event.objects.count(), Process.objects.count()
            self.seed()
            self.stdout.write(
                f'Removed {removed["orders"]} orders, {removed["events"]} events, {removed["processes"]} processes. '
                f'Created {Order.objects.count() - orders} orders, {Event.objects.count() - events} events, {Process.objects.count() - procs} processes.')

    def wipe(self):
        """Delete demo rows in dependency order and return the counts; QuerySet.delete() bypasses the append-only Model.delete."""
        orders = Order.objects.filter(customer__name__startswith='DEMO ')
        uids = list(Tag.objects.filter(note='DEMO').values_list('uid', flat=True)) + list(DEMO_UIDS)
        ev = Event.objects.filter(Q(device__name__startswith='DEMO ') | Q(tag_uid__in=uids) | Q(item__order__in=orders))
        ev = Event.objects.filter(Q(pk__in=ev.values('pk')) | Q(corrects__in=ev))  # a correction goes with the event it voids
        removed = {'events': ev.count(), 'orders': orders.count(), 'processes': 0}
        ev.filter(corrects__isnull=False).delete()
        ev.delete()
        TagAssignment.objects.filter(Q(tag__uid__in=uids) | Q(item__order__in=orders)).delete()
        orders.delete()
        Tag.objects.filter(uid__in=uids).delete()
        User.objects.filter(username__startswith='demo_').delete()
        Box.objects.filter(label__startswith='DEMO').delete()
        Customer.objects.filter(name__startswith='DEMO ').delete()
        # Old demo processes and scanners stay (with a warning) if a real record still points at them
        for obj in [*ScannerDevice.objects.filter(name__startswith='DEMO '), *Process.objects.filter(name__startswith='DEMO ')]:
            try:
                with transaction.atomic():
                    obj.delete()
                removed['processes'] += isinstance(obj, Process)
            except ProtectedError as e:
                last = max(e.protected_objects, key=lambda o: getattr(o, 'occurred_at', timezone.now()))
                self.stdout.write(self.style.WARNING(f'Kept {type(obj).__name__} "{obj}": still used by {len(e.protected_objects)} record(s), e.g. {last}.'))
        return removed

    def demo_process(self, row, vendor):
        """The process of that name; a real one of another kind is left alone and a 'DEMO ' copy is used."""
        found = Process.objects.filter(name=row['name']).first()
        name = row['name']
        if found and found.kind != row['kind']:
            name = f'DEMO {name}'
            self.stdout.write(self.style.WARNING(f'Process "{row["name"]}" exists with another kind; using "{name}".'))
        return Process.objects.get_or_create(name=name, defaults={'name_ja': row['name_ja'], 'kind': row['kind'], 'default_vendor': vendor})[0]

    def seed(self):
        now, today = timezone.now(), timezone.localdate()
        ago = lambda **kw: now - timedelta(**kw)
        vendors = {r['code']: Vendor.objects.get_or_create(code=r['code'], defaults={
            'name': r['name'], 'name_ja': r['name_ja'], 'kind': r['kind'], 'lead_days': LEAD_DAYS.get(r['code']),
            'notes': r['notes'], 'active': r['active'] == 'yes'})[0]
            for r in read_csv('codes.csv')}
        Vendor.objects.filter(code__in=[r['code'] for r in read_csv('codes.csv') if r['notes']], notes='').update(
            notes=Case(*[When(code=r['code'], then=Value(r['notes'])) for r in read_csv('codes.csv') if r['notes']]))  # fill notes on codes made before the field existed
        procs = {r['name']: self.demo_process(r, vendors[DEFAULT_VENDOR[r['name']]]) for r in read_csv('processes.csv')}
        d1, d2, _silent = (ScannerDevice.objects.get_or_create(name=f'DEMO Scanner {i}')[0] for i in (1, 2, 3))  # scanner 3 never reports
        for role, name in (('Operator', 'demo_operator'), ('Planner', 'demo_planner'), ('Admin', 'demo_admin')):  # cannot log in
            user = User(username=name)
            user.set_unusable_password()
            user.save()
            user.groups.add(Group.objects.get(name=role))
        c1, c2 = (Customer.objects.get_or_create(name=f'DEMO {n}')[0] for n in CUSTOMERS)
        created = []

        def link(item, uid, box, start=None, end=None):
            TagAssignment.objects.create(
                tag=Tag.objects.get_or_create(uid=uid)[0], box=Box.objects.get_or_create(label=box)[0],
                item=item, start_at=start or ago(days=21), end_at=end,
            )

        def make(label, customer, part, route, due, uid, box, number, start=None, end=None, box_count=1, ids=('', '', '')):
            """One order with one item, its route, and a tag/box assignment; ids = part number, drawing number, revision."""
            order = Order.objects.create(customer=customer, customer_order_number=number, due_date=today + timedelta(days=due))
            item = OrderItem.objects.create(
                order=order, part_name=part, quantity=10, box_count=box_count, material=MATERIALS[len(created) % len(MATERIALS)],
                part_number=ids[0], drawing_number=ids[1], revision=ids[2])
            for i, name in enumerate(route, 1):  # step due dates spread back from the order due date, two days per step
                RouteStep.objects.create(item=item, sequence=i, process=procs[name], vendor=procs[name].default_vendor,
                                         due_date=today + timedelta(days=due - 2 * (len(route) - i)))
            link(item, uid, box, start, end)
            created.append((label, item))
            return item

        def scan(uid, name, etype, when, device=d1):
            return record_event(device, uid, procs[name], etype, occurred_at=when)[0]

        full = ['Milling', 'Lathe', 'Assembly', 'Inspection']
        # 1 Healthy: material arrived, two steps done with a 4h wait, third running
        make('Healthy', c1, 'ブラケット', ['Material'] + full, 10, '0781DD5E', 'DEMO-01', 'TY-2610-014', ids=('BRK-100', 'DWG-1001', 'A'))
        scan('0781DD5E', 'Material', 'ARRIVED', ago(hours=34))
        for name, s, f in (('Milling', 30, 26), ('Lathe', 22, 18)):
            scan('0781DD5E', name, 'STARTED', ago(hours=s))
            scan('0781DD5E', name, 'FINISHED', ago(hours=f))
        scan('0781DD5E', 'Assembly', 'STARTED', ago(hours=3), d2)
        # 2 Overdue
        make('Overdue', c1, 'フランジ', ['Milling', 'Lathe', 'Inspection'], -3, 'F8DF3064', 'DEMO-02', 'TY-2610-009', ids=('FLG-200', 'DWG-2001', 'B'))
        scan('F8DF3064', 'Milling', 'STARTED', ago(hours=80))
        scan('F8DF3064', 'Milling', 'FINISHED', ago(hours=70))
        scan('F8DF3064', 'Lathe', 'STARTED', ago(hours=2), d2)
        # 3 Critical: due soon, 4 steps left, nothing scanned yet
        make('Critical', c2, 'ハウジング', full, 2, '74BA7571', 'DEMO-03', 'HS-45021')
        # 4 Vendor late: Coating out 10 days, planned back 5 days ago (step due date and slip)
        late = make('Vendor late', c2, 'カバー', ['Milling', 'Coating'], 4, '8EFBD21A', 'DEMO-04', 'HS-45017')
        scan('8EFBD21A', 'Milling', 'STARTED', ago(days=12))
        scan('8EFBD21A', 'Milling', 'FINISHED', ago(days=11))
        scan('8EFBD21A', 'Coating', 'SENT_OUT', ago(days=10), d2)
        late.steps.filter(process=procs['Coating']).update(due_date=today - timedelta(days=5), slip_number='SL-0412')
        # 5 Stagnant: finished a step 40h ago, nothing since
        make('Stagnant', c1, 'シャフト', ['Milling', 'Lathe', 'Inspection'], 6, '57E36722', 'DEMO-05', 'TY-2610-021')
        scan('57E36722', 'Milling', 'STARTED', ago(hours=46))
        scan('57E36722', 'Milling', 'FINISHED', ago(hours=40))
        # 6 Complete, due yesterday
        make('Complete', c2, 'プレート', ['Milling', 'Lathe', 'Plating'], -1, 'FC3F61E6', 'DEMO-06', 'HS-44986')
        scan('FC3F61E6', 'Milling', 'STARTED', ago(days=6))
        scan('FC3F61E6', 'Milling', 'FINISHED', ago(days=5, hours=20))
        scan('FC3F61E6', 'Lathe', 'STARTED', ago(days=5))
        scan('FC3F61E6', 'Lathe', 'FINISHED', ago(days=4))
        scan('FC3F61E6', 'Plating', 'SENT_OUT', ago(days=3), d2)
        scan('FC3F61E6', 'Plating', 'RETURNED', ago(days=1), d2)
        # 7 Not started
        make('Not started', c1, 'ピン', ['Milling', 'Lathe', 'Assembly'], 20, '092C4B14', 'DEMO-07', 'TY-2610-027', ids=('PIN-300', '', ''))
        # 8 Messy scans: skipped step, process not in route, unassigned tag, voided wrong scan
        make('Messy scans', c2, 'ギア', full, 5, '3E180A05', 'DEMO-08', 'HS-45030')
        scan('3E180A05', 'Milling', 'STARTED', ago(hours=30))
        scan('3E180A05', 'Milling', 'FINISHED', ago(hours=28))
        scan('3E180A05', 'Assembly', 'STARTED', ago(hours=20))
        scan('3E180A05', 'Plating', 'SENT_OUT', ago(hours=18), d2)
        wrong = scan('3E180A05', 'Inspection', 'FINISHED', ago(hours=15))
        fix = Event(device=d2, tag_uid='3E180A05', event_type=Event.Type.CORRECTION, corrects=wrong, occurred_at=ago(hours=14), reason='Wrong scan')
        fix.full_clean()
        fix.save()
        Tag.objects.get_or_create(uid='B15D6B73')
        scan('B15D6B73', 'Milling', 'STARTED', ago(hours=6), d2)
        # 9 Reused box: DEMO-09 carried a finished order, now carries a new one
        old = make('Reused box (old)', c2, '旧部品', ['Milling'], -10, '03193FDF', 'DEMO-09', 'HS-44871', ago(days=21), ago(days=9), ids=('FLG-200', 'DWG-2001', 'A'))
        scan('03193FDF', 'Milling', 'STARTED', ago(days=19))
        scan('03193FDF', 'Milling', 'FINISHED', ago(days=18))
        self.manual(d2, '', None, Event.Type.SHIPPED, ago(days=8), '', item=old)  # shipped two days after its due date
        make('Reused box (now)', c1, '新部品', ['Milling', 'Lathe'], 7, '414D497D', 'DEMO-09', 'TY-2610-025', ago(days=2), ids=('flg-200', 'DWG-2001', 'B'))
        scan('414D497D', 'Milling', 'STARTED', ago(hours=30))
        scan('414D497D', 'Milling', 'FINISHED', ago(hours=20))
        scan('414D497D', 'Lathe', 'STARTED', ago(hours=2), d2)
        # 10 Split item: 3 boxes expected; one finished, one at step 2, one not linked yet
        route = ['Milling', 'Lathe', 'Assembly']
        split = make('Split item', c1, 'フレーム', route, 8, 'C36F67C6', 'DEMO-10', 'TY-2610-018', box_count=3)
        link(split, 'D795A680', 'DEMO-11')
        for name, s, f in (('Milling', 40, 36), ('Lathe', 34, 30), ('Assembly', 28, 24)):
            scan('C36F67C6', name, 'STARTED', ago(hours=s))
            scan('C36F67C6', name, 'FINISHED', ago(hours=f))
        scan('D795A680', 'Milling', 'STARTED', ago(hours=26))
        scan('D795A680', 'Milling', 'FINISHED', ago(hours=22))
        scan('D795A680', 'Lathe', 'STARTED', ago(hours=1), d2)

        # 11 Paused: milling started, then put on hold by the planner
        make('Paused', c1, 'ベース', ['Material', 'Milling', 'Inspection'], 12, '5A1C9E02', 'DEMO-12', 'TY-2610-030', ids=('BRK-100', 'DWG-1001', 'A'))
        scan('5A1C9E02', 'Material', 'ARRIVED', ago(hours=30))
        scan('5A1C9E02', 'Milling', 'STARTED', ago(hours=6))
        self.manual(d1, '5A1C9E02', procs['Milling'], Event.Type.PAUSED, ago(hours=2), 'Waiting for a tool')
        # 12 Cancelled: the customer withdrew the order after the first step
        gone = make('Cancelled', c2, 'リング', ['Milling', 'Lathe'], 5, '9B27F4D6', 'DEMO-13', 'HS-45040', ids=('BRK-100', 'DWG-1001', 'B'))
        scan('9B27F4D6', 'Milling', 'STARTED', ago(hours=8))
        self.manual(d2, '', None, Event.Type.CANCELLED, ago(hours=5), 'Withdrawn by the customer', item=gone)
        # The healthy order's assembly is only partly done
        self.manual(d1, '0781DD5E', procs['Assembly'], Event.Type.PARTIAL, ago(hours=1), 'Half of the pieces')
        self.add_results(created, d1)
        bulk = seed_bulk(procs, {v.code: v for v in vendors.values()}, (d1, d2))
        self.stdout.write(self.style.SUCCESS(f'Created {len(created)} scenario orders and {bulk} history orders:'))
        statuses = item_statuses([item for _label, item in created])
        for label, item in created:
            self.stdout.write(f'  {item.order.job_number}  {label:<18} {statuses[item.pk].code}')

    def add_results(self, created, device):
        """Quantity-done results for finished steps; about one in ten has 1-2 defects, work spread over the CSV workers and machines."""
        people = read_csv('workers_machines.csv')
        workers = [Worker.objects.get_or_create(name=r['name'], defaults={'name_ja': r['name_ja']})[0] for r in people if r['kind'] == 'WORKER']
        machines = [Machine.objects.get_or_create(name=r['name'], defaults={'name_ja': r['name_ja']})[0] for r in people if r['kind'] == 'MACHINE']
        n, statuses = 0, item_statuses([item for _label, item in created])
        for _label, item in created:
            for box in statuses[item.pk].boxes:
                for row in (r for r in box.rows if r.state == DONE and r.finished_at):
                    if n == MAX_RESULTS:
                        return
                    machining = row.step.process.kind == Process.Kind.MACHINING
                    event = Event(
                        device=device, tag_uid=box.assignments[-1].tag.uid, process=row.step.process, event_type=Event.Type.RESULT,
                        occurred_at=row.finished_at, quantity=item.quantity, defects=(1 + n % 2) if n % 7 == 3 else 0,
                        worker=workers[n % len(workers)] if machining else None, machine=machines[n % len(machines)] if machining else None,
                    )
                    event.full_clean()
                    event.save()
                    n += 1

    @staticmethod
    def manual(device, uid, process, event_type, when, reason, item=None):
        """A planner-style event (pause, partial, cancel or ship) recorded straight into the log."""
        event = Event(device=device, tag_uid=uid, process=process, event_type=event_type, occurred_at=when, reason=reason, item=item)
        event.full_clean()
        event.save()
