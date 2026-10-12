import tempfile
from datetime import timedelta

from django.contrib.admin import site
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from core.testing import TestCase
from tracking.models import Box, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event

from .models import Customer, Order, OrderChange, OrderItem, Process, RouteStep

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
        for i, (name, qty, route, *boxes) in enumerate(items):
            data.update({f'items-{i}-part_name': name, f'items-{i}-quantity': qty, f'items-{i}-route': route})
            if boxes:
                data[f'items-{i}-box_count'] = boxes[0]
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

    def test_box_count_is_saved_and_defaults_to_one(self):
        self.login_staff()
        route = [self.material.pk]
        self.assertRedirects(self.post([('Shaft', 5, route, 3), ('Gear', 2, route)]), '/orders/')
        self.assertEqual(dict(OrderItem.objects.values_list('part_name', 'box_count')), {'Shaft': 3, 'Gear': 1})

    def test_box_count_below_one_is_rejected(self):
        self.login_staff()
        self.assertEqual(self.post([('Shaft', 5, [self.material.pk], 0)]).status_code, 200)
        self.assertFalse(OrderItem.objects.exists())

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


class OrderDetailTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        self.order = Order.objects.create(customer=Customer.objects.create(name='Acme'), due_date=timezone.localdate())
        self.item = OrderItem.objects.create(order=self.order, part_name='Shaft', quantity=3)
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)
        RouteStep.objects.create(item=self.item, sequence=1, process=self.proc)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB'), box=Box.objects.create(label='B1'), item=self.item)
        self.url = f'/orders/{self.order.job_number}/'

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_shows_job_number_and_part_name(self):
        r = self.client.get(self.url)
        self.assertContains(r, self.order.job_number)
        self.assertContains(r, 'Shaft')
        self.assertContains(r, 'Box B1, tag AB')

    def test_unknown_job_number_is_404(self):
        self.assertEqual(self.client.get('/orders/0000-999/').status_code, 404)

    def test_scan_marks_step_done_and_shows_event(self):
        record_event(ScannerDevice.objects.create(name='D'), 'AB', self.proc, 'ARRIVED')
        r = self.client.get(self.url)
        entry = r.context['items'][0]
        self.assertEqual(entry['boxes'][0]['steps'][0]['row'].state, 'done')
        self.assertEqual(len(entry['boxes'][0]['events']), 1)
        self.assertContains(r, 'Arrived')
        self.assertContains(r, 'Done')


class BoxViewTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        self.order = Order.objects.create(customer=Customer.objects.create(name='Acme'), due_date=timezone.localdate())
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)

    def item(self, boxes, linked):
        item = OrderItem.objects.create(order=self.order, part_name=f'P{OrderItem.objects.count()}', quantity=1, box_count=boxes)
        RouteStep.objects.create(item=item, sequence=1, process=self.proc)
        for i in range(linked):
            TagAssignment.objects.create(
                tag=Tag.objects.create(uid=f'U{item.pk}{i}'), box=Box.objects.create(label=f'L{item.pk}{i}'), item=item)
        return item

    def test_list_shows_box_total_only_for_split_items(self):
        self.item(1, 1)
        self.assertIsNone(self.client.get('/orders/').context['rows'][0]['boxes'])
        self.item(3, 1)
        self.assertEqual(self.client.get('/orders/').context['rows'][0]['boxes'], 4)

    def test_detail_single_box_looks_as_before(self):
        self.item(1, 1)
        r = self.client.get(f'/orders/{self.order.job_number}/')
        self.assertContains(r, 'Event history')
        self.assertNotContains(r, 'Boxes:')

    def test_detail_split_item_shows_sections_and_unlinked_box(self):
        item = self.item(3, 2)
        r = self.client.get(f'/orders/{self.order.job_number}/')
        self.assertContains(r, 'Boxes: 2 of 3 linked')
        self.assertContains(r, f'Box L{item.pk}0')
        self.assertContains(r, f'Box L{item.pk}1')
        self.assertContains(r, 'Box not linked yet')
        self.assertContains(r, 'href="/tags/"')
        self.assertEqual(len(r.context['items'][0]['boxes']), 3)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OrderEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('s', password='x', is_staff=True)
        self.client.force_login(self.user)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.cust = Customer.objects.create(name='Acme')
        self.order = Order.objects.create(customer=self.cust, due_date=timezone.localdate() + timedelta(days=30), notes='old')
        self.item = self.new_item('Shaft', [self.mat, self.mill])
        self.device = ScannerDevice.objects.create(name='D')

    def new_item(self, part, route, boxes=1):
        item = OrderItem.objects.create(order=self.order, part_name=part, quantity=2, box_count=boxes)
        for i, p in enumerate(route, 1):
            RouteStep.objects.create(item=item, sequence=i, process=p)
        return item

    def link(self, item, uid, box, end=False):
        return TagAssignment.objects.create(
            tag=Tag.objects.create(uid=uid), box=Box.objects.create(label=box), item=item,
            start_at=timezone.now() - timedelta(days=2), end_at=timezone.now() - timedelta(days=1) if end else None)

    def post(self, items=None, **order_fields):
        """Edit POST: current order values, one entry per item (dict of overrides), then the overrides."""
        o = self.order
        items = items if items is not None else [{}]
        data = {'customer': o.customer_id, 'customer_order_number': o.customer_order_number, 'due_date': o.due_date, 'notes': o.notes,
                'items-TOTAL_FORMS': len(items), 'items-INITIAL_FORMS': o.items.count(), 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000}
        existing = list(o.items.all())
        for i, ov in enumerate(items):
            base = {}
            if i < len(existing):
                it = existing[i]
                base = {'id': it.pk, 'order': o.pk, 'part_name': it.part_name, 'quantity': it.quantity, 'box_count': it.box_count,
                        'route': list(it.steps.values_list('process_id', flat=True))}
            base.update(ov)
            data.update({f'items-{i}-{k}': v for k, v in base.items()})
        data.update(order_fields)
        return self.client.post(f'/orders/{o.job_number}/edit/', data)

    def changes(self):
        return {(c.item_code, c.field): (c.old_value, c.new_value) for c in OrderChange.objects.filter(order=self.order)}

    def test_edit_fields_logs_history_and_keeps_codes(self):
        job, code = self.order.job_number, self.item.item_code
        due = timezone.localdate() + timedelta(days=5)
        r = self.post([{'part_name': 'Long shaft', 'quantity': 9}], due_date=due, notes='rush', customer_order_number='PO-1')
        self.assertRedirects(r, f'/orders/{job}/')
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual((self.order.job_number, self.item.item_code, self.order.due_date, self.item.part_name), (job, code, due, 'Long shaft'))
        ch = self.changes()
        self.assertEqual(ch['', 'notes'], ('old', 'rush'))
        self.assertEqual(ch['', 'customer_order_number'], ('', 'PO-1'))
        self.assertEqual(ch['', 'due_date'][1], str(due))
        self.assertEqual(ch[code, 'part_name'], ('Shaft', 'Long shaft'))
        self.assertEqual(ch[code, 'quantity'], ('2', '9'))
        self.assertTrue(all(c.changed_by == self.user for c in OrderChange.objects.all()))
        page = self.client.get(f'/orders/{job}/')
        self.assertContains(page, 'Change history')
        self.assertContains(page, 'rush')

    def test_unchanged_save_logs_nothing(self):
        self.post()
        self.assertFalse(OrderChange.objects.exists())

    def test_due_date_change_moves_priority_tier(self):
        self.link(self.item, 'T1', 'B1')
        tier = lambda: self.client.get('/priorities/').context['rows'][0]['tier']
        self.assertEqual(tier(), 'ON_TRACK')
        self.post(due_date=timezone.localdate())
        self.assertEqual(tier(), 'CRITICAL')
        self.post(due_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(tier(), 'OVERDUE')

    def test_box_count_cannot_drop_below_linked_boxes(self):
        item = self.new_item('Frame', [self.mill], boxes=3)
        self.link(item, 'A', 'BA', end=True)
        self.link(item, 'B', 'BB')
        r = self.post([{}, {'box_count': 1}])
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Box count cannot be lower than the 2 boxes already linked.')
        item.refresh_from_db()
        self.assertEqual(item.box_count, 3)
        self.assertFalse(OrderChange.objects.exists())
        self.assertEqual(self.post([{}, {'box_count': 2}]).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.box_count, 2)

    def test_route_editable_before_a_scan_and_logged(self):
        self.assertEqual(self.post([{'route': [self.mill.pk]}]).status_code, 302)
        self.assertEqual(list(self.item.steps.values_list('process_id', flat=True)), [self.mill.pk])
        self.assertEqual(self.changes()[self.item.item_code, 'route'], ('Mat > Mill', 'Mill'))
        self.assertNotContains(self.client.get(f'/orders/{self.order.job_number}/edit/'), 'Route is locked')

    def test_route_locked_after_a_scan_and_posted_changes_ignored(self):
        self.link(self.item, 'T1', 'B1')
        record_event(self.device, 'T1', self.mat, 'ARRIVED')
        r = self.post([{'route': [self.mill.pk], 'part_name': 'Renamed'}])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(self.item.steps.values_list('process_id', flat=True)), [self.mat.pk, self.mill.pk])
        self.item.refresh_from_db()
        self.assertEqual(self.item.part_name, 'Renamed')
        self.assertNotIn((self.item.item_code, 'route'), self.changes())
        self.assertContains(self.client.get(f'/orders/{self.order.job_number}/edit/'), 'Route is locked because this item has been scanned.')

    def test_add_item(self):
        r = self.post([{}, {'part_name': 'Pin', 'quantity': 4, 'box_count': 2, 'route': [self.mill.pk]}])
        self.assertEqual(r.status_code, 302)
        new = self.order.items.get(part_name='Pin')
        self.assertEqual((new.item_code, new.box_count, new.steps.count()), (f'{self.order.job_number}-02', 2, 1))
        self.assertIn((new.item_code, 'item_added'), self.changes())

    def test_delete_untouched_item_but_not_a_linked_or_scanned_one(self):
        spare = self.new_item('Spare', [self.mill])
        r = self.post([{}, {'DELETE': 'on'}])
        self.assertEqual(r.status_code, 302)
        self.assertFalse(OrderItem.objects.filter(pk=spare.pk).exists())
        self.assertEqual(self.changes()[f'{self.order.job_number}-02', 'item_deleted'][0], f'{self.order.job_number}-02 Spare')
        self.link(self.item, 'T1', 'B1')
        r = self.post([{'DELETE': 'on'}, {}])
        self.assertContains(r, 'cannot be deleted because it has tags or scans')
        self.assertTrue(OrderItem.objects.filter(pk=self.item.pk).exists())
        self.assertContains(self.client.get(f'/orders/{self.order.job_number}/edit/'), 'Cannot be removed because it has tags or scans.')

    def test_replaced_drawing_is_logged(self):
        self.post([{'drawing_file': SimpleUploadedFile('a.pdf', b'%PDF-1.4 x')}])
        self.item.refresh_from_db()
        self.assertTrue(self.item.drawing_file.name.endswith('a.pdf'))
        self.assertEqual(self.changes()[self.item.item_code, 'drawing_file'][0], '')

    def test_staff_only(self):
        url = f'/orders/{self.order.job_number}/edit/'
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user('u', password='x'))
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, {}).status_code, 302)

    def test_change_history_is_append_only(self):
        self.post(notes='x')
        c = OrderChange.objects.get()
        c.new_value = 'y'
        with self.assertRaises(ValidationError):
            c.save()
        with self.assertRaises(ValidationError):
            c.delete()
        model_admin = site._registry[OrderChange]
        self.assertFalse(model_admin.has_change_permission(None) or model_admin.has_delete_permission(None) or model_admin.has_add_permission(None))

    def test_pages_render_in_both_languages(self):
        self.post(notes='x')
        for lang, words in (('en', ('Edit', 'Change history', 'Save changes')), ('ja', ('編集', '変更履歴', '変更を保存'))):
            self.client.cookies['django_language'] = lang
            detail = self.client.get(f'/orders/{self.order.job_number}/')
            edit = self.client.get(f'/orders/{self.order.job_number}/edit/')
            self.assertEqual((detail.status_code, edit.status_code), (200, 200))
            self.assertContains(detail, words[0])
            self.assertContains(detail, words[1])
            self.assertContains(edit, words[2])
