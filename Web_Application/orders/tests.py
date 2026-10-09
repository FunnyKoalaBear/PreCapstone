from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Customer, Order


class OrderCreateTests(TestCase):
    def test_staff_only(self):
        self.assertEqual(self.client.get('/orders/new/').status_code, 302)
        self.client.force_login(User.objects.create_user('u', password='x'))
        self.assertEqual(self.client.get('/orders/new/').status_code, 302)
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        self.assertEqual(self.client.get('/orders/new/').status_code, 200)

    def test_valid_post_creates_order_with_job_number(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        c = Customer.objects.create(name='C')
        r = self.client.post('/orders/new/', {'customer': c.pk, 'due_date': date(2030, 1, 1)})
        self.assertRedirects(r, '/orders/new/')
        self.assertRegex(Order.objects.get().job_number, r'^\d{4}-\d{3}$')
