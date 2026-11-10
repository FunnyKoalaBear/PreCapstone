from datetime import date, timedelta

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep

from .models import Event, ScannerDevice
from .services import assign_tag, cancel_item, record_event, release_assignment, ship_item

E = Event.Type


class SetterListFilterTests(TestCase):
    """GET /api/assignable-items/ lists only items still being worked on, sorted and paged on the server."""

    def setUp(self):
        self.device = ScannerDevice.objects.create(name='Setter')
        self.key = {'HTTP_X_API_KEY': self.device.api_key}
        self.planner = make_user('pl', 'Planner')
        self.mill = Process.objects.create(name='Mill', kind=Process.Kind.MACHINING)
        self.customer = Customer.objects.create(name='C')
        self.n = 0

    def make(self, part, due=date(2026, 12, 1), boxes=1):
        order = Order.objects.create(customer=self.customer, due_date=due)
        item = OrderItem.objects.create(order=order, part_name=part, quantity=1, box_count=boxes)
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        return item

    def scan(self, item, *types):
        """Tag the item, scan the given events on it, then free the tag (as when a tag is reused)."""
        self.n += 1
        uid = f'T{self.n}'
        a = assign_tag(item, uid)
        for n, t in enumerate(types, 1):
            record_event(self.device, uid, self.mill, t, a.start_at + timedelta(microseconds=n))
        release_assignment(a)

    def items(self, query=''):
        return self.client.get('/api/assignable-items/' + query, **self.key)

    def ids(self, query=''):
        return [i['id'] for i in self.items(query).json()['items']]

    def test_only_not_started_and_started_items_are_listed(self):
        new = self.make('New')
        started = self.make('Started')
        self.scan(started, E.STARTED)
        complete = self.make('Complete')
        self.scan(complete, E.STARTED, E.FINISHED)
        shipped = self.make('Shipped')
        self.scan(shipped, E.STARTED, E.FINISHED)
        ship_item(self.planner, shipped)
        cancelled = self.make('Cancelled')
        cancel_item(self.planner, cancelled, 'customer withdrew')
        j = self.items().json()
        self.assertEqual(j['total'], 2)
        self.assertEqual([i['id'] for i in j['items']], [new.pk, started.pk])
        self.assertNotIn(complete.pk, self.ids())

    def test_a_box_still_free_on_a_started_item_is_listed(self):
        item = self.make('Two boxes', boxes=2)
        assign_tag(item, 'B1')
        self.scan(item, E.STARTED)
        self.assertEqual(self.ids(), [item.pk])

    def test_pages_count_only_open_items(self):
        shown = []
        for n in range(5):
            item = self.make(f'P{n}')
            if n % 2:
                self.scan(item, E.STARTED, E.FINISHED)
            else:
                shown.append(item.pk)
        j = self.items('?offset=0&limit=2').json()
        self.assertEqual((j['total'], [i['id'] for i in j['items']]), (3, shown[:2]))
        self.assertEqual(self.ids('?offset=2&limit=2'), shown[2:])
        self.assertEqual(self.ids('?offset=3&limit=2'), [])

    def test_default_page_is_ten(self):
        for n in range(12):
            self.make(f'P{n}')
        j = self.items().json()
        self.assertEqual((j['total'], len(j['items'])), (12, 10))

    def test_sort_orders(self):
        a = self.make('Bolt', due=date(2026, 12, 20))
        b = self.make('Axle', due=date(2026, 12, 5))
        c = self.make('Cam', due=date(2026, 12, 10))
        self.assertEqual(self.ids(), [a.pk, b.pk, c.pk])
        self.assertEqual(self.ids('?sort=number'), [a.pk, b.pk, c.pk])
        self.assertEqual(self.ids('?sort=start'), [a.pk, b.pk, c.pk])
        self.assertEqual(self.ids('?sort=due'), [b.pk, c.pk, a.pk])
        self.assertEqual(self.ids('?sort=part'), [b.pk, a.pk, c.pk])

    def test_sorted_pages_do_not_overlap(self):
        made = [self.make('Same', due=date(2026, 12, 1)).pk for _ in range(5)]
        seen = self.ids('?sort=due&limit=2') + self.ids('?sort=due&offset=2&limit=2') + self.ids('?sort=due&offset=4&limit=2')
        self.assertEqual(seen, made)

    def test_bad_sort_is_refused(self):
        r = self.items('?sort=price')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'))
