from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Box, Event, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event
from tracking.status import item_status

K = Process.Kind
# short name: (kind, Japanese name, vendor name, lead days); stored as "DEMO <name>", never shared with real processes
PROCESSES = {
    'Material': (K.MATERIAL, '材料入荷', '', None), 'Cutting': (K.MACHINING, '切断', '', None),
    'Welding': (K.MACHINING, '溶接', '', None), 'Assembly': (K.MACHINING, '組立', '', None),
    'Inspection': (K.MACHINING, '検査', '', None),
    'Painting': (K.OUTSOURCED, '塗装', 'DEMO 日本塗装', 5), 'Plating': (K.OUTSOURCED, 'メッキ', 'DEMO メッキ工業', 7),
}
CUSTOMERS = ('東洋機械', '北斗製作所')
# 4-byte tag UIDs of the demo scenarios (box labels stay DEMO-xx)
DEMO_UIDS = (
    '0781DD5E', 'F8DF3064', '74BA7571', '8EFBD21A', '57E36722', 'FC3F61E6', '092C4B14',
    '3E180A05', '03193FDF', '414D497D', 'C36F67C6', 'D795A680', 'B15D6B73',
)


class Command(BaseCommand):
    help = 'Create demo orders, tags and scans for reviewing the dashboard (DEBUG only).'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete demo data first, then re-create it.')

    def handle(self, *args, reset=False, **options):
        if not settings.DEBUG:
            raise CommandError('seed_demo only runs when DEBUG is True.')
        with transaction.atomic():
            if reset:
                self.wipe()
            elif Customer.objects.filter(name__startswith='DEMO ').exists():
                self.stdout.write('Demo data already exists; use --reset to re-create it.')
                return
            self.seed()

    def wipe(self):
        """Delete demo rows only; QuerySet.delete() bypasses the append-only Model.delete."""
        ev = Event.objects.filter(Q(device__name__startswith='DEMO ') | Q(tag_uid__in=DEMO_UIDS))
        ev.filter(corrects__isnull=False).delete()
        ev.delete()
        orders = Order.objects.filter(customer__name__startswith='DEMO ')
        TagAssignment.objects.filter(Q(tag__uid__in=DEMO_UIDS) | Q(item__order__in=orders)).delete()
        orders.delete()
        Tag.objects.filter(uid__in=DEMO_UIDS).delete()
        Box.objects.filter(label__startswith='DEMO').delete()
        ScannerDevice.objects.filter(name__startswith='DEMO ').delete()
        Customer.objects.filter(name__startswith='DEMO ').delete()
        Process.objects.filter(name__startswith='DEMO ').delete()

    def seed(self):
        now, today = timezone.now(), timezone.localdate()
        ago = lambda **kw: now - timedelta(**kw)
        procs = {n: Process.objects.get_or_create(
            name=f'DEMO {n}', defaults={'kind': k, 'name_ja': ja, 'vendor_name': v, 'vendor_lead_days': d})[0]
            for n, (k, ja, v, d) in PROCESSES.items()}
        d1, d2 = (ScannerDevice.objects.get_or_create(name=f'DEMO Scanner {i}')[0] for i in (1, 2))
        c1, c2 = (Customer.objects.get_or_create(name=f'DEMO {n}')[0] for n in CUSTOMERS)
        created = []

        def link(item, uid, box, start=None, end=None):
            TagAssignment.objects.create(
                tag=Tag.objects.get_or_create(uid=uid)[0], box=Box.objects.get_or_create(label=box)[0],
                item=item, start_at=start or ago(days=21), end_at=end,
            )

        def make(label, customer, part, route, due, uid, box, number, start=None, end=None, box_count=1):
            """One order with one item, its route, and a tag/box assignment; returns the item."""
            order = Order.objects.create(customer=customer, customer_order_number=number, due_date=today + timedelta(days=due))
            item = OrderItem.objects.create(order=order, part_name=part, quantity=10, box_count=box_count)
            for i, name in enumerate(route, 1):
                RouteStep.objects.create(item=item, sequence=i, process=procs[name])
            link(item, uid, box, start, end)
            created.append((label, item))
            return item

        def scan(uid, name, etype, when, device=d1):
            return record_event(device, uid, procs[name], etype, occurred_at=when)[0]

        full = ['Cutting', 'Welding', 'Assembly', 'Inspection']
        # 1 Healthy: material arrived, two steps done with a 4h wait, third running
        make('Healthy', c1, 'ブラケット', ['Material'] + full, 10, '0781DD5E', 'DEMO-01', 'TY-2610-014')
        scan('0781DD5E', 'Material', 'ARRIVED', ago(hours=34))
        for name, s, f in (('Cutting', 30, 26), ('Welding', 22, 18)):
            scan('0781DD5E', name, 'STARTED', ago(hours=s))
            scan('0781DD5E', name, 'FINISHED', ago(hours=f))
        scan('0781DD5E', 'Assembly', 'STARTED', ago(hours=3), d2)
        # 2 Overdue
        make('Overdue', c1, 'フランジ', ['Cutting', 'Welding', 'Inspection'], -3, 'F8DF3064', 'DEMO-02', 'TY-2610-009')
        scan('F8DF3064', 'Cutting', 'STARTED', ago(hours=80))
        scan('F8DF3064', 'Cutting', 'FINISHED', ago(hours=70))
        scan('F8DF3064', 'Welding', 'STARTED', ago(hours=2), d2)
        # 3 Critical: due soon, 4 steps left, nothing scanned yet
        make('Critical', c2, 'ハウジング', full, 2, '74BA7571', 'DEMO-03', 'HS-45021')
        # 4 Vendor late: Painting out 10 days, lead 5
        make('Vendor late', c2, 'カバー', ['Cutting', 'Painting'], 4, '8EFBD21A', 'DEMO-04', 'HS-45017')
        scan('8EFBD21A', 'Cutting', 'STARTED', ago(days=12))
        scan('8EFBD21A', 'Cutting', 'FINISHED', ago(days=11))
        scan('8EFBD21A', 'Painting', 'SENT_OUT', ago(days=10), d2)
        # 5 Stagnant: finished a step 40h ago, nothing since
        make('Stagnant', c1, 'シャフト', ['Cutting', 'Welding', 'Inspection'], 6, '57E36722', 'DEMO-05', 'TY-2610-021')
        scan('57E36722', 'Cutting', 'STARTED', ago(hours=46))
        scan('57E36722', 'Cutting', 'FINISHED', ago(hours=40))
        # 6 Complete, due yesterday
        make('Complete', c2, 'プレート', ['Cutting', 'Welding', 'Plating'], -1, 'FC3F61E6', 'DEMO-06', 'HS-44986')
        scan('FC3F61E6', 'Cutting', 'STARTED', ago(days=6))
        scan('FC3F61E6', 'Cutting', 'FINISHED', ago(days=5, hours=20))
        scan('FC3F61E6', 'Welding', 'STARTED', ago(days=5))
        scan('FC3F61E6', 'Welding', 'FINISHED', ago(days=4))
        scan('FC3F61E6', 'Plating', 'SENT_OUT', ago(days=3), d2)
        scan('FC3F61E6', 'Plating', 'RETURNED', ago(days=1), d2)
        # 7 Not started
        make('Not started', c1, 'ピン', ['Cutting', 'Welding', 'Assembly'], 20, '092C4B14', 'DEMO-07', 'TY-2610-027')
        # 8 Messy scans: skipped step, process not in route, unassigned tag, voided wrong scan
        make('Messy scans', c2, 'ギア', full, 5, '3E180A05', 'DEMO-08', 'HS-45030')
        scan('3E180A05', 'Cutting', 'STARTED', ago(hours=30))
        scan('3E180A05', 'Cutting', 'FINISHED', ago(hours=28))
        scan('3E180A05', 'Assembly', 'STARTED', ago(hours=20))
        scan('3E180A05', 'Plating', 'SENT_OUT', ago(hours=18), d2)
        wrong = scan('3E180A05', 'Inspection', 'FINISHED', ago(hours=15))
        fix = Event(device=d2, tag_uid='3E180A05', event_type=Event.Type.CORRECTION, corrects=wrong, occurred_at=ago(hours=14), reason='Wrong scan')
        fix.full_clean()
        fix.save()
        Tag.objects.get_or_create(uid='B15D6B73')
        scan('B15D6B73', 'Cutting', 'STARTED', ago(hours=6), d2)
        # 9 Reused box: DEMO-09 carried a finished order, now carries a new one
        make('Reused box (old)', c2, '旧部品', ['Cutting'], -10, '03193FDF', 'DEMO-09', 'HS-44871', ago(days=21), ago(days=9))
        scan('03193FDF', 'Cutting', 'STARTED', ago(days=19))
        scan('03193FDF', 'Cutting', 'FINISHED', ago(days=18))
        make('Reused box (now)', c1, '新部品', ['Cutting', 'Welding'], 7, '414D497D', 'DEMO-09', 'TY-2610-025', ago(days=2))
        scan('414D497D', 'Cutting', 'STARTED', ago(hours=30))
        scan('414D497D', 'Cutting', 'FINISHED', ago(hours=20))
        scan('414D497D', 'Welding', 'STARTED', ago(hours=2), d2)
        # 10 Split item: 3 boxes expected; one finished, one at step 2, one not linked yet
        route = ['Cutting', 'Welding', 'Assembly']
        split = make('Split item', c1, 'フレーム', route, 8, 'C36F67C6', 'DEMO-10', 'TY-2610-018', box_count=3)
        link(split, 'D795A680', 'DEMO-11')
        for name, s, f in (('Cutting', 40, 36), ('Welding', 34, 30), ('Assembly', 28, 24)):
            scan('C36F67C6', name, 'STARTED', ago(hours=s))
            scan('C36F67C6', name, 'FINISHED', ago(hours=f))
        scan('D795A680', 'Cutting', 'STARTED', ago(hours=26))
        scan('D795A680', 'Cutting', 'FINISHED', ago(hours=22))
        scan('D795A680', 'Welding', 'STARTED', ago(hours=1), d2)

        self.stdout.write(self.style.SUCCESS(f'Created {len(created)} demo orders:'))
        for label, item in created:
            self.stdout.write(f'  {item.order.job_number}  {label:<18} {item_status(item).code}')
