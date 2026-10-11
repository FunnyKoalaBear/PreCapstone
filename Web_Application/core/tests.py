from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Box, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event

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
