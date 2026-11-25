from core.testing import TestCase, make_user
from pathlib import Path

from django.conf import settings

from orders.models import Customer, Order, OrderChange, OrderItem, Process, RouteStep

from .models import ScannerDevice, Tag, TagAssignment
from .services import record_event

LIVE_PAGES = ['/', '/boxes/', '/completed/', '/shipping/', '/dashboard/']


class LastChangeTests(TestCase):
    def setUp(self):
        self.user = make_user('pl', 'Planner')
        self.client.force_login(self.user)
        self.mill = Process.objects.create(name='Mill', kind=Process.Kind.MACHINING)
        self.device = ScannerDevice.objects.create(name='D')

    def sig(self):
        r = self.client.get('/api/last-change/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Cache-Control'], 'no-store')
        return r.json()['sig']

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        r = self.client.get('/api/last-change/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])

    def test_empty_database_gives_zeros(self):
        self.assertEqual(self.sig(), '0-0-0-0')

    def test_sig_changes_after_event_and_order_item(self):
        before = self.sig()
        record_event(self.device, 'AB1', self.mill, 'STARTED')
        after_event = self.sig()
        self.assertNotEqual(before, after_event)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date='2026-12-01')
        OrderItem.objects.create(order=order, part_name='P', quantity=1)
        self.assertNotEqual(after_event, self.sig())

    def test_sig_changes_after_tag_assignment(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date='2026-12-01')
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        before = self.sig()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T1'), item=item, start_at='2026-01-01T00:00:00+09:00')
        self.assertNotEqual(before, self.sig())

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post('/api/last-change/').status_code, 405)

    def make_order(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date='2026-12-01')
        OrderItem.objects.create(order=order, part_name='P', quantity=1)
        return order

    def test_sig_changes_after_order_change(self):
        order = self.make_order()
        before = self.sig()
        OrderChange.objects.create(order=order, field='due_date', old_value='a', new_value='b')
        self.assertNotEqual(before, self.sig())

    def test_live_attribute_and_script_on_live_pages(self):
        self.client.force_login(make_user('ad', 'Admin'))
        order = self.make_order()
        for url in LIVE_PAGES + ['/orders/', f'/orders/{order.job_number}/', '/priorities/', '/tags/history/', '/tags/', '/simulate/']:
            r = self.client.get(url)
            self.assertContains(r, 'data-live="/api/last-change/"', msg_prefix=url)
            self.assertContains(r, 'js/live.js', msg_prefix=url)

    def test_not_live_pages(self):
        self.client.force_login(make_user('ad', 'Admin'))
        for url in ['/analysis/', '/plan-vs-actual/', '/orders/?tab=new', '/orders/?tab=import', '/orders/?tab=codes',
                    '/orders/?tab=processes', '/settings/']:
            r = self.client.get(url, follow=True)
            self.assertNotContains(r, 'data-live', msg_prefix=url)
            self.assertNotContains(r, 'js/live.js', msg_prefix=url)

    def test_live_js_replaces_instead_of_reloading(self):
        js = (Path(settings.BASE_DIR) / 'static/js/live.js').read_text(encoding='utf-8')
        self.assertIn('location.replace', js)
        self.assertNotIn('location.reload', js)
