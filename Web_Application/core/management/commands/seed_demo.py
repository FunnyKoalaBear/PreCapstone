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
# short name: (kind, vendor name, lead days); stored as "DEMO <name>", never shared with real processes
PROCESSES = {
    'Material': (K.MATERIAL, '', None), 'Cutting': (K.MACHINING, '', None), 'Welding': (K.MACHINING, '', None),
    'Assembly': (K.MACHINING, '', None), 'Inspection': (K.MACHINING, '', None),
    'Painting': (K.OUTSOURCED, 'DEMO Paint Co', 5), 'Plating': (K.OUTSOURCED, 'DEMO Plating Co', 7),
}


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
        ev = Event.objects.filter(Q(device__name__startswith='DEMO ') | Q(tag_uid__startswith='DEMO'))
        ev.filter(corrects__isnull=False).delete()
        ev.delete()
        orders = Order.objects.filter(customer__name__startswith='DEMO ')
        TagAssignment.objects.filter(Q(tag__uid__startswith='DEMO') | Q(item__order__in=orders)).delete()
        orders.delete()
        Tag.objects.filter(uid__startswith='DEMO').delete()
        Box.objects.filter(label__startswith='DEMO').delete()
        ScannerDevice.objects.filter(name__startswith='DEMO ').delete()
        Customer.objects.filter(name__startswith='DEMO ').delete()
        Process.objects.filter(name__startswith='DEMO ').delete()

    def seed(self):
        now, today = timezone.now(), timezone.localdate()
        ago = lambda **kw: now - timedelta(**kw)
        procs = {n: Process.objects.get_or_create(
            name=f'DEMO {n}', defaults={'kind': k, 'vendor_name': v, 'vendor_lead_days': d})[0] for n, (k, v, d) in PROCESSES.items()}
        d1, d2 = (ScannerDevice.objects.get_or_create(name=f'DEMO Scanner {i}')[0] for i in (1, 2))
        c1, c2 = (Customer.objects.get_or_create(name=f'DEMO {n}')[0] for n in ('Alpha Industries', 'Beta Works'))
        created = []

        def make(label, customer, part, route, due, uid, box, start=None, end=None):
            """One order with one item, its route, and a tag/box assignment; returns the item."""
            order = Order.objects.create(customer=customer, due_date=today + timedelta(days=due))
            item = OrderItem.objects.create(order=order, part_name=part, quantity=10)
            for i, name in enumerate(route, 1):
                RouteStep.objects.create(item=item, sequence=i, process=procs[name])
            TagAssignment.objects.create(
                tag=Tag.objects.get_or_create(uid=uid)[0], box=Box.objects.get_or_create(label=box)[0],
                item=item, start_at=start or ago(days=21), end_at=end,
            )
            created.append((label, item))
            return item

        def scan(uid, name, etype, when, device=d1):
            return record_event(device, uid, procs[name], etype, occurred_at=when)[0]

        full = ['Cutting', 'Welding', 'Assembly', 'Inspection']
        # 1 Healthy: material arrived, two steps done with a 4h wait, third running
        make('Healthy', c1, 'Bracket', ['Material'] + full, 10, 'DEMO-01', 'DEMO-01')
        scan('DEMO-01', 'Material', 'ARRIVED', ago(hours=34))
        for name, s, f in (('Cutting', 30, 26), ('Welding', 22, 18)):
            scan('DEMO-01', name, 'STARTED', ago(hours=s))
            scan('DEMO-01', name, 'FINISHED', ago(hours=f))
        scan('DEMO-01', 'Assembly', 'STARTED', ago(hours=3), d2)
        # 2 Overdue
        make('Overdue', c1, 'Flange', ['Cutting', 'Welding', 'Inspection'], -3, 'DEMO-02', 'DEMO-02')
        scan('DEMO-02', 'Cutting', 'STARTED', ago(hours=80))
        scan('DEMO-02', 'Cutting', 'FINISHED', ago(hours=70))
        scan('DEMO-02', 'Welding', 'STARTED', ago(hours=2), d2)
        # 3 Critical: due soon, 4 steps left, nothing scanned yet
        make('Critical', c2, 'Housing', full, 2, 'DEMO-03', 'DEMO-03')
        # 4 Vendor late: Painting out 10 days, lead 5
        make('Vendor late', c2, 'Cover', ['Cutting', 'Painting'], 4, 'DEMO-04', 'DEMO-04')
        scan('DEMO-04', 'Cutting', 'STARTED', ago(days=12))
        scan('DEMO-04', 'Cutting', 'FINISHED', ago(days=11))
        scan('DEMO-04', 'Painting', 'SENT_OUT', ago(days=10), d2)
        # 5 Stagnant: finished a step 40h ago, nothing since
        make('Stagnant', c1, 'Shaft', ['Cutting', 'Welding', 'Inspection'], 6, 'DEMO-05', 'DEMO-05')
        scan('DEMO-05', 'Cutting', 'STARTED', ago(hours=46))
        scan('DEMO-05', 'Cutting', 'FINISHED', ago(hours=40))
        # 6 Complete, due yesterday
        make('Complete', c2, 'Plate', ['Cutting', 'Welding', 'Plating'], -1, 'DEMO-06', 'DEMO-06')
        scan('DEMO-06', 'Cutting', 'STARTED', ago(days=6))
        scan('DEMO-06', 'Cutting', 'FINISHED', ago(days=5, hours=20))
        scan('DEMO-06', 'Welding', 'STARTED', ago(days=5))
        scan('DEMO-06', 'Welding', 'FINISHED', ago(days=4))
        scan('DEMO-06', 'Plating', 'SENT_OUT', ago(days=3), d2)
        scan('DEMO-06', 'Plating', 'RETURNED', ago(days=1), d2)
        # 7 Not started
        make('Not started', c1, 'Pin', ['Cutting', 'Welding', 'Assembly'], 20, 'DEMO-07', 'DEMO-07')
        # 8 Messy scans: skipped step, process not in route, unassigned tag, voided wrong scan
        make('Messy scans', c2, 'Gear', full, 5, 'DEMO-08', 'DEMO-08')
        scan('DEMO-08', 'Cutting', 'STARTED', ago(hours=30))
        scan('DEMO-08', 'Cutting', 'FINISHED', ago(hours=28))
        scan('DEMO-08', 'Assembly', 'STARTED', ago(hours=20))
        scan('DEMO-08', 'Plating', 'SENT_OUT', ago(hours=18), d2)
        wrong = scan('DEMO-08', 'Inspection', 'FINISHED', ago(hours=15))
        fix = Event(device=d2, tag_uid='DEMO-08', event_type=Event.Type.CORRECTION, corrects=wrong, occurred_at=ago(hours=14))
        fix.full_clean()
        fix.save()
        Tag.objects.get_or_create(uid='DEMO-X1')
        scan('DEMO-X1', 'Cutting', 'STARTED', ago(hours=6), d2)
        # 9 Reused box: DEMO-09 carried a finished order, now carries a new one
        make('Reused box (old)', c2, 'Old part', ['Cutting'], -10, 'DEMO-09A', 'DEMO-09', ago(days=21), ago(days=9))
        scan('DEMO-09A', 'Cutting', 'STARTED', ago(days=19))
        scan('DEMO-09A', 'Cutting', 'FINISHED', ago(days=18))
        make('Reused box (now)', c1, 'New part', ['Cutting', 'Welding'], 7, 'DEMO-09B', 'DEMO-09', ago(days=2))
        scan('DEMO-09B', 'Cutting', 'STARTED', ago(hours=30))
        scan('DEMO-09B', 'Cutting', 'FINISHED', ago(hours=20))
        scan('DEMO-09B', 'Welding', 'STARTED', ago(hours=2), d2)

        self.stdout.write(self.style.SUCCESS(f'Created {len(created)} demo orders:'))
        for label, item in created:
            self.stdout.write(f'  {item.order.job_number}  {label:<18} {item_status(item).code}')
