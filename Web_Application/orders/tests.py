from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

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
            'customer': Customer.objects.create(name='C').pk, 'due_date': date(2030, 1, 1),
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
        self.assertRedirects(self.post([('Shaft', 5, route), ('Gear', 2, route)]), '/orders/new/')
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
