from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Box, Event, ScannerDevice, Tag, TagAssignment
from tracking.priority import priority
from tracking.services import record_event
from tracking.status import days_late, due_back, is_stagnant, item_status

K = Process.Kind


class HomeDashboardTests(TestCase):
    def setUp(self):
        self.material = Process.objects.create(name='Material', kind=K.MATERIAL)
        self.milling = Process.objects.create(name='Milling', kind=K.MACHINING)
        self.plating = Process.objects.create(name='Plating', kind=K.OUTSOURCED, vendor_name='Acme Plating')
        self.device = ScannerDevice.objects.create(name='D')
        self.customer = Customer.objects.create(name='Cust')
        self.n = 0

    def make_item(self, route, due_days=10, order=None, since=None):
        """An item with its route and a tag assigned to it; returns (item, uid)."""
        self.n += 1
        order = order or Order.objects.create(customer=self.customer, due_date=timezone.localdate() + timedelta(days=due_days))
        item = OrderItem.objects.create(order=order, part_name=f'Part{self.n}', quantity=1)
        for i, proc in enumerate(route, 1):
            RouteStep.objects.create(item=item, sequence=i, process=proc)
        uid = f'T{self.n}'
        TagAssignment.objects.create(
            tag=Tag.objects.create(uid=uid), box=Box.objects.create(label=f'B{self.n}'), item=item,
            start_at=since or timezone.now(),
        )
        return item, uid

    def send_out(self, days_ago, lead_days, name='Vendor process'):
        """Send an item out to a new outsourced process `days_ago` days ago."""
        proc = Process.objects.create(name=name, kind=K.OUTSOURCED, vendor_name='V', vendor_lead_days=lead_days)
        now = timezone.now()
        item, uid = self.make_item([proc], since=now - timedelta(days=days_ago + 5))
        record_event(self.device, uid, proc, 'SENT_OUT', occurred_at=now - timedelta(days=days_ago))
        return item

    def home(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        return self.client.get('/')

    def test_staff_only_content(self):
        r = self.client.get('/')
        self.assertContains(r, 'Dashboard coming soon.')
        self.assertNotContains(r, 'In-house now')
        self.assertContains(self.home(), 'In-house now')

    def test_arrived_with_pending_next_step_is_waiting_in_house(self):
        _, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.material, 'ARRIVED')
        r = self.home()
        self.assertEqual([e['code'] for e in r.context['in_house']], ['WAITING'])
        self.assertEqual(r.context['counters']['waiting'], 1)
        self.assertEqual(r.context['at_vendor'], [])

    def test_sent_out_shows_at_vendor_with_vendor_name(self):
        _, uid = self.make_item([self.plating])
        record_event(self.device, uid, self.plating, 'SENT_OUT')
        r = self.home()
        self.assertEqual(r.context['in_house'], [])
        self.assertEqual(r.context['at_vendor'][0]['vendor'], 'Acme Plating')
        self.assertContains(r, 'Acme Plating')

    def test_completed_orders_do_not_appear(self):
        _, uid = self.make_item([self.material])
        record_event(self.device, uid, self.material, 'ARRIVED')
        r = self.home()
        self.assertEqual((r.context['in_house'], r.context['at_vendor']), ([], []))
        self.assertContains(r, 'Nothing in the factory right now.')

    def test_overdue_counts_an_order_once(self):
        order = Order.objects.create(customer=self.customer, due_date=timezone.localdate() - timedelta(days=2))
        self.make_item([self.material], order=order)
        self.make_item([self.material], order=order)
        self.assertEqual(self.home().context['counters']['overdue'], 1)

    def test_scans_on_assigned_tags_show_in_counters_and_tables(self):
        item, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.material, 'ARRIVED')
        record_event(self.device, uid, self.milling, 'STARTED')
        r = self.home()
        self.assertEqual(r.context['counters']['in_progress'], 1)
        self.assertEqual(r.context['counters']['not_started'], 0)
        self.assertEqual([(e['item'], e['code'], e['process']) for e in r.context['in_house']],
                         [(item, 'IN_PROGRESS', self.milling)])
        self.assertContains(r, item.item_code)

    def test_active_step_with_earlier_step_unscanned_is_in_progress(self):
        item, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.milling, 'STARTED')
        r = self.home()
        self.assertEqual(r.context['counters']['in_progress'], 1)
        self.assertEqual(r.context['in_house'][0]['code'], 'IN_PROGRESS')

    def test_sent_out_past_lead_time_is_late_and_counted(self):
        item = self.send_out(10, 7)
        r = self.home()
        e = r.context['at_vendor'][0]
        self.assertEqual((e['late_days'], r.context['counters']['vendor_overdue']), (3, 1))
        self.assertEqual(e['due_back'], timezone.localdate() - timedelta(days=3))
        self.assertContains(r, '3 days')
        detail = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(detail, 'Late by 3 days')
        self.assertContains(detail, 'due back')

    def test_sent_out_today_is_not_late(self):
        self.send_out(0, 7)
        r = self.home()
        e = r.context['at_vendor'][0]
        self.assertEqual((e['late_days'], r.context['counters']['vendor_overdue']), (0, 0))
        self.assertEqual(e['due_back'], timezone.localdate() + timedelta(days=7))

    def test_no_lead_days_is_never_late(self):
        self.send_out(60, None)
        r = self.home()
        e = r.context['at_vendor'][0]
        self.assertEqual((e['due_back'], e['late_days'], r.context['counters']['vendor_overdue']), (None, 0, 0))
        self.assertContains(r, 'No due date')

    def test_late_items_sort_first(self):
        oldest = self.send_out(20, None, 'P1')   # oldest but no due date
        late3 = self.send_out(10, 7, 'P2')
        late5 = self.send_out(12, 7, 'P3')
        r = self.home()
        self.assertEqual([e['item'] for e in r.context['at_vendor']], [late5, late3, oldest])
        self.assertEqual(r.context['counters']['vendor_overdue'], 2)

    def test_stagnant_counter_and_sort_order(self):
        now = timezone.now()
        fresh, fresh_uid = self.make_item([self.material, self.milling], due_days=1, since=now - timedelta(days=9))
        old, old_uid = self.make_item([self.material, self.milling], due_days=20, since=now - timedelta(days=9))
        older, older_uid = self.make_item([self.material, self.milling], due_days=30, since=now - timedelta(days=9))
        for uid, ago in ((fresh_uid, timedelta(hours=1)), (old_uid, timedelta(days=3)), (older_uid, timedelta(days=5))):
            record_event(self.device, uid, self.material, 'ARRIVED', occurred_at=now - ago)
        r = self.home()
        self.assertEqual(r.context['counters']['stagnant'], 2)
        self.assertEqual([e['item'] for e in r.context['in_house']], [older, old, fresh])
        self.assertContains(r, 'Idle for')


class SeedDemoTests(TestCase):
    def seed(self, *args):
        call_command('seed_demo', *args, stdout=StringIO())

    def status(self, part):
        item = OrderItem.objects.get(part_name=part)
        return item, item_status(item)

    def test_refuses_without_debug(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(Customer.objects.exists())

    @override_settings(DEBUG=True)
    def test_runs_twice_without_duplicating_and_reset_recreates(self):
        self.seed()
        n = (Order.objects.count(), Event.objects.count())
        self.seed()
        self.assertEqual((Order.objects.count(), Event.objects.count()), n)
        self.seed('--reset')
        self.assertEqual((Order.objects.count(), Event.objects.count()), n)

    @override_settings(DEBUG=True)
    def test_scenarios_match_statuses(self):
        self.seed()
        item, st = self.status('Flange')
        self.assertEqual((st.code, priority(item.order, st)['tier']), ('IN_PROGRESS', 'OVERDUE'))
        item, st = self.status('Cover')
        self.assertEqual(st.code, 'AT_VENDOR')
        self.assertGreater(days_late(due_back(st.rows[1].started_at, 5)), 0)
        self.assertTrue(is_stagnant(self.status('Shaft')[1]))
        self.assertEqual(self.status('Plate')[1].code, 'COMPLETE')
        self.assertEqual(self.status('Pin')[1].code, 'NOT_STARTED')
        item, st = self.status('Housing')
        self.assertEqual(priority(item.order, st)['tier'], 'CRITICAL')
        self.assertTrue(self.status('Gear')[1].warnings)
        self.assertEqual(self.status('New part')[1].code, 'IN_PROGRESS')

    @override_settings(DEBUG=True)
    def test_every_event_uses_the_right_process_kind(self):
        self.seed()
        events = Event.objects.exclude(event_type='CORRECTION').select_related('process')
        self.assertTrue(events)
        for e in events:
            self.assertEqual(e.process.kind, Event.REQUIRED_KIND[e.event_type], e)
        self.assertTrue(Event.objects.filter(event_type='CORRECTION').exists())
        self.assertFalse(Process.objects.exclude(name__startswith='DEMO ').exists())

    @override_settings(DEBUG=True)
    def test_reset_removes_demo_data_and_keeps_real_data(self):
        real_proc = Process.objects.create(name='Cutting', kind=K.MATERIAL)
        real_cust = Customer.objects.create(name='Real Co')
        real_order = Order.objects.create(customer=real_cust, due_date=timezone.localdate())
        real_dev = ScannerDevice.objects.create(name='Real scanner')
        Tag.objects.create(uid='REAL1')
        real_event = Event.objects.create(device=real_dev, tag_uid='REAL1', event_type='ARRIVED', process=real_proc)
        self.seed()
        n = (Order.objects.count(), Event.objects.count(), Process.objects.count(), TagAssignment.objects.count())
        self.seed('--reset')
        self.assertEqual((Order.objects.count(), Event.objects.count(), Process.objects.count(), TagAssignment.objects.count()), n)
        self.assertTrue(Event.objects.filter(pk=real_event.pk).exists())
        self.assertTrue(Order.objects.filter(pk=real_order.pk).exists())
        self.assertTrue(Tag.objects.filter(uid='REAL1').exists())
        self.assertTrue(Process.objects.filter(pk=real_proc.pk, kind=K.MATERIAL).exists())

    @override_settings(DEBUG=True)
    def test_failed_seed_leaves_nothing_behind(self):
        Process.objects.create(name='DEMO Cutting', kind=K.MATERIAL)  # wrong kind makes STARTED fail
        with self.assertRaises(Exception):
            self.seed()
        self.assertEqual((Customer.objects.count(), Order.objects.count(), Event.objects.count()), (0, 0, 0))
