from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from tracking.models import Box, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event

from .models import Customer, Order, OrderItem, Process, RouteStep

K = Process.Kind


class OrderCreateTests(TestCase):
    def setUp(self):
        self.material = Process.objects.create(name='Material', kind=K.MATERIAL)
        self.milling = Process.objects.create(name='Milling', kind=K.MACHINING)
        self.outsourced = Process.objects.create(name='Outsourced', kind=K.OUTSOURCED)

    def post(self, items, deleted=()):
        """items: list of (part_name, quantity, [process ids])."""
        data = {
            'customer': Customer.objects.create(name='C').pk, 'due_date': timezone.localdate() + timedelta(days=30),
            'items-TOTAL_FORMS': len(items), 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
        }
        for i, (name, qty, route) in enumerate(items):
            data.update({f'items-{i}-part_name': name, f'items-{i}-quantity': qty, f'items-{i}-route': route})
        data.update({f'items-{i}-DELETE': 'on' for i in deleted})
        return self.client.post('/orders/new/', data)

    def login_staff(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))

    def test_staff_only(self):
        self.assertEqual(self.client.get('/orders/new/').status_code, 302)
        self.client.force_login(User.objects.create_user('u', password='x'))
        self.assertEqual(self.client.get('/orders/new/').status_code, 302)
        self.login_staff()
        self.assertEqual(self.client.get('/orders/new/').status_code, 200)

    def test_valid_post_creates_order_and_two_items(self):
        self.login_staff()
        route = [self.material.pk]
        self.assertRedirects(self.post([('Shaft', 5, route), ('Gear', 2, route)]), '/orders/')
        order = Order.objects.get()
        codes = [i.item_code for i in OrderItem.objects.order_by('item_no')]
        self.assertEqual(codes, [f'{order.job_number}-01', f'{order.job_number}-02'])

    def test_route_is_saved_in_submitted_order(self):
        self.login_staff()
        route = [self.material.pk, self.milling.pk, self.outsourced.pk]
        self.post([('Shaft', 5, route)])
        steps = RouteStep.objects.order_by('sequence')
        self.assertEqual([s.sequence for s in steps], [1, 2, 3])
        self.assertEqual([s.process_id for s in steps], route)

    def test_empty_route_creates_nothing(self):
        self.login_staff()
        self.assertEqual(self.post([('Shaft', 5, [])]).status_code, 200)
        self.assertEqual((Order.objects.count(), OrderItem.objects.count(), RouteStep.objects.count()), (0, 0, 0))

    def test_post_without_item_creates_nothing(self):
        self.login_staff()
        self.assertEqual(self.post([]).status_code, 200)
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (0, 0))

    def test_removed_item_needs_no_route(self):
        self.login_staff()
        self.post([('Shaft', 5, [self.material.pk]), ('Gear', 2, [])], deleted=[1])
        self.assertEqual(list(OrderItem.objects.values_list('part_name', flat=True)), ['Shaft'])


class OrderListTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        self.today = timezone.localdate()
        self.acme, self.beta = Customer.objects.create(name='Acme'), Customer.objects.create(name='Beta')
        self.a = self.make(self.beta, 5, 'Shaft')   # due later, customer Beta
        self.b = self.make(self.acme, -3, 'Gear')   # overdue, customer Acme
        self.c = self.make(self.acme, 2, 'Plate')

    def make(self, customer, days, part, number=''):
        order = Order.objects.create(customer=customer, due_date=self.today + timedelta(days=days), customer_order_number=number)
        OrderItem.objects.create(order=order, part_name=part, quantity=1)
        return order

    def jobs(self, **params):
        r = self.client.get('/orders/', params)
        self.assertEqual(r.status_code, 200)
        return [row['order'].job_number for row in r.context['rows']]

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get('/orders/').status_code, 302)

    def test_sorting(self):
        self.assertEqual(self.jobs(), [o.job_number for o in (self.b, self.c, self.a)])
        self.assertEqual(self.jobs(sort='customer'), [o.job_number for o in (self.b, self.c, self.a)])
        self.assertEqual(self.jobs(sort='newest'), [o.job_number for o in (self.c, self.b, self.a)])
        self.assertEqual(self.jobs(sort='bogus'), self.jobs())

    def test_search(self):
        self.assertEqual(self.jobs(q=self.a.job_number), [self.a.job_number])
        self.assertEqual(self.jobs(q='beta'), [self.a.job_number])
        self.assertEqual(self.jobs(q='plate'), [self.c.job_number])

    def test_overdue_filter(self):
        self.assertEqual(self.jobs(late='1'), [self.b.job_number])

    def test_status_filter(self):
        proc = Process.objects.create(name='Mat', kind=Process.Kind.MATERIAL)
        item = self.a.items.get()
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB'), box=Box.objects.create(label='B'), item=item)
        record_event(ScannerDevice.objects.create(name='D'), 'AB', proc, 'ARRIVED')
        self.assertEqual(self.jobs(status='COMPLETE'), [self.a.job_number])
        self.assertEqual(len(self.jobs(status='NOT_STARTED')), 2)
        # A completed order is never overdue
        self.a.due_date = self.today - timedelta(days=1)
        self.a.save()
        self.assertNotIn(self.a.job_number, self.jobs(late='1'))
