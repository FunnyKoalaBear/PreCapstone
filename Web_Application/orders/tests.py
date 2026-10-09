from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Customer, Order, OrderItem


class OrderCreateTests(TestCase):
    def post(self, items, deleted=()):
        c = Customer.objects.create(name='C')
        data = {
            'customer': c.pk, 'due_date': date(2030, 1, 1),
            'items-TOTAL_FORMS': len(items), 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
        }
        for i, (name, qty) in enumerate(items):
            data.update({f'items-{i}-part_name': name, f'items-{i}-quantity': qty})
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
        self.assertRedirects(self.post([('Shaft', 5), ('Gear', 2)]), '/orders/new/')
        order = Order.objects.get()
        codes = [i.item_code for i in OrderItem.objects.order_by('item_no')]
        self.assertEqual(codes, [f'{order.job_number}-01', f'{order.job_number}-02'])

    def test_post_without_item_creates_nothing(self):
        self.login_staff()
        self.assertEqual(self.post([]).status_code, 200)
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (0, 0))

    def test_removed_item_is_not_created(self):
        self.login_staff()
        self.post([('Shaft', 5), ('Gear', 2)], deleted=[1])
        self.assertEqual(list(OrderItem.objects.values_list('part_name', flat=True)), ['Shaft'])
