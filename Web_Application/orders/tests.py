import importlib
import tempfile
from datetime import timedelta

from django.apps import apps

from django.contrib.admin import site
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils.datastructures import MultiValueDict
from django.utils import timezone, translation

from core.testing import TestCase, make_user
from tracking.models import Box, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event

from .forms import OrderItemForm
from .models import Customer, Order, OrderChange, OrderItem, Process, RouteStep, Vendor

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
        self.client.force_login(make_user('s', 'Admin'))

    def test_staff_only(self):
        self.assertEqual(self.client.get('/orders/new/').status_code, 302)
        self.client.force_login(make_user('u', 'Operator'))
        self.assertEqual(self.client.get('/orders/new/').status_code, 403)
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
        self.client.force_login(make_user('s', 'Admin'))
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
        self.client.force_login(make_user('s', 'Admin'))
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

    def test_shows_step_table_and_chart(self):
        mach = Process.objects.create(name='Mach', kind=K.MACHINING)
        RouteStep.objects.create(item=self.item, sequence=2, process=mach)
        dev = ScannerDevice.objects.create(name='D')
        record_event(dev, 'AB', self.proc, 'ARRIVED')
        record_event(dev, 'AB', mach, 'STARTED')
        r = self.client.get(self.url)
        for text in ('Time spent', 'Waited before', 'Timeline', 'gantt-bar gantt-instant', 'gantt-bar gantt-in_progress', 'gantt-now'):
            self.assertContains(r, text)

    def test_chart_empty_without_events(self):
        self.assertNotContains(self.client.get(self.url), 'gantt-bar')


class BoxViewTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('s', 'Admin'))
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
        self.user = make_user('s', 'Admin')
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

    def test_edit_with_a_typed_customer_name_changes_the_customer(self):
        self.post(new_customer=' New  Client ')
        self.order.refresh_from_db()
        self.assertEqual(self.order.customer.name, 'New Client')
        self.assertEqual(self.changes()['', 'customer'], ('Acme', 'New Client'))

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
        self.client.force_login(make_user('u', 'Operator'))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)

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


class VendorTests(TestCase):
    def check(self, code, kind):
        Vendor(code=code, name='V', kind=kind).full_clean()

    def test_code_ranges_per_kind(self):
        V = Vendor.Kind
        for code, kind in (('00000', V.INTERNAL), ('00099', V.INTERNAL), ('00100', V.SUPPLIER), ('00429', V.SUPPLIER), ('00100', V.VENDOR), ('00429', V.VENDOR)):
            self.check(code, kind)
        for code, kind in (('00100', V.INTERNAL), ('00099', V.SUPPLIER), ('00430', V.SUPPLIER), ('00099', V.VENDOR), ('00430', V.VENDOR)):
            with self.assertRaises(ValidationError, msg=(code, kind)):
                self.check(code, kind)

    def test_code_must_be_five_digits_and_unique(self):
        for bad in ('123', '001234', 'ABCDE', '12 45'):
            with self.assertRaises(ValidationError, msg=bad):
                self.check(bad, Vendor.Kind.VENDOR)
        Vendor.objects.create(code='00100', name='A', kind=Vendor.Kind.VENDOR)
        with self.assertRaises(ValidationError):
            self.check('00100', Vendor.Kind.SUPPLIER)

    def test_str_shows_code_and_name_in_the_active_language(self):
        v = Vendor(code='00101', name='Acme', name_ja='アクメ', kind=Vendor.Kind.VENDOR)
        self.assertEqual(str(v), '00101 Acme')
        with translation.override('ja'):
            self.assertEqual(str(v), '00101 アクメ')


class StepVendorTests(TestCase):
    def setUp(self):
        self.default = Vendor.objects.create(code='00100', name='Default', kind=Vendor.Kind.VENDOR, lead_days=4)
        self.other = Vendor.objects.create(code='00101', name='Other', kind=Vendor.Kind.VENDOR)
        self.proc = Process.objects.create(name='Plate', kind=K.OUTSOURCED, default_vendor=self.default, vendor_name='Old', vendor_lead_days=9)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)

    def step(self, vendor=None):
        return RouteStep.objects.create(item=self.item, sequence=RouteStep.objects.count() + 1, process=self.proc, vendor=vendor)

    def test_step_vendor_falls_back_to_process_default(self):
        self.assertEqual(self.step().vendor_label, '00100 Default')
        self.assertEqual(self.step(self.other).vendor_label, '00101 Other')

    def test_lead_days_vendor_then_process(self):
        self.assertEqual(self.step().lead_days, 4)  # default vendor
        self.assertEqual(self.step(self.other).lead_days, 9)  # vendor without lead days: process value
        self.proc.default_vendor = None
        self.assertEqual(RouteStep(item=self.item, sequence=9, process=self.proc).lead_days, 9)
        self.assertEqual(RouteStep(item=self.item, sequence=9, process=self.proc).vendor_label, 'Old')

    def test_data_migration_turns_names_into_vendors(self):
        Process.objects.all().update(default_vendor=None)
        a = Process.objects.create(name='A', kind=K.OUTSOURCED, vendor_name='Same', vendor_lead_days=3)
        b = Process.objects.create(name='B', kind=K.OUTSOURCED, vendor_name='Same', vendor_lead_days=3)
        c = Process.objects.create(name='C', kind=K.OUTSOURCED, vendor_name='Mixed1', vendor_lead_days=2)
        d = Process.objects.create(name='D', kind=K.OUTSOURCED, vendor_name='Mixed1', vendor_lead_days=6)
        Vendor.objects.all().delete()
        importlib.import_module('orders.migrations.0010_vendors_from_names').names_to_vendors(apps, None)
        for p in (a, b, c, d, self.proc):
            p.refresh_from_db()
        self.assertEqual(a.default_vendor, b.default_vendor)
        self.assertEqual((a.default_vendor.kind, a.default_vendor.lead_days), ('VENDOR', 3))
        self.assertIsNone(c.default_vendor.lead_days)
        self.assertEqual(c.default_vendor, d.default_vendor)
        self.assertEqual(sorted(Vendor.objects.values_list('code', flat=True)), ['00100', '00101', '00102'])
        for v in Vendor.objects.all():
            v.full_clean()


class OrderFormVendorTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.vendor = Vendor.objects.create(code='00100', name='Acme', kind=Vendor.Kind.VENDOR)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.plate = Process.objects.create(name='Plate', kind=K.OUTSOURCED, default_vendor=self.vendor)

    def post(self, route, vendors, url='/orders/new/'):
        data = {
            'customer': Customer.objects.create(name=f'C{Customer.objects.count()}').pk, 'due_date': timezone.localdate() + timedelta(days=30),
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
            'items-0-part_name': 'Shaft', 'items-0-quantity': 1, 'items-0-route': route, 'items-0-route_vendor': vendors,
        }
        return self.client.post(url, data)

    def test_vendor_is_saved_per_step_and_blank_steps_stay_aligned(self):
        r = self.post([self.mat.pk, '', self.plate.pk], ['', '', self.vendor.pk])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(RouteStep.objects.values_list('process_id', 'vendor_id')), [(self.mat.pk, None), (self.plate.pk, self.vendor.pk)])

    def test_unknown_vendor_is_rejected(self):
        self.assertEqual(self.post([self.plate.pk], ['9999']).status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_form_offers_vendors_and_process_default(self):
        r = self.client.get('/orders/new/')
        self.assertContains(r, 'name="items-0-route_vendor"')
        self.assertContains(r, f'data-vendor="{self.vendor.pk}"')
        self.assertContains(r, '00100 Acme')

    def test_edit_changes_vendor_and_logs_it(self):
        self.post([self.plate.pk], [self.vendor.pk])
        item = OrderItem.objects.get()
        url = f'/orders/{item.order.job_number}/edit/'
        data = {
            'customer': item.order.customer_id, 'due_date': item.order.due_date, 'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1,
            'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000, 'items-0-id': item.pk, 'items-0-order': item.order_id,
            'items-0-part_name': 'Shaft', 'items-0-quantity': 1, 'items-0-box_count': 1, 'items-0-route': [self.plate.pk], 'items-0-route_vendor': [''],
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertIsNone(RouteStep.objects.get().vendor_id)
        change = OrderChange.objects.get(field='route')
        self.assertEqual((change.old_value, change.new_value), ('Plate [00100]', 'Plate'))

    def test_detail_page_shows_code_and_name(self):
        self.post([self.plate.pk], [self.vendor.pk])
        item = OrderItem.objects.get()
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), '00100 Acme')


class CodeListTests(TestCase):
    def setUp(self):
        Vendor.objects.create(code='00010', name='Lathe shop', kind=Vendor.Kind.INTERNAL)
        Vendor.objects.create(code='00150', name='Plating Co', name_ja='めっき工業', kind=Vendor.Kind.VENDOR, lead_days=5)

    def codes(self, query=''):
        return [v.code for v in self.client.get('/codes/' + query).context['vendors']]

    def test_roles(self):
        self.assertEqual(self.client.get('/codes/').status_code, 302)
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/codes/').status_code, 403)
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertEqual(self.client.get('/codes/').status_code, 200)

    def test_search_by_code_name_and_kind(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertEqual(self.codes(), ['00010', '00150'])
        self.assertEqual(self.codes('?q=0015'), ['00150'])
        self.assertEqual(self.codes('?q=lathe'), ['00010'])
        self.assertEqual(self.codes('?q=めっき'), ['00150'])
        self.assertEqual(self.codes('?kind=INTERNAL'), ['00010'])
        self.assertEqual(self.codes('?kind=bogus'), ['00010', '00150'])


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DrawingViewTests(TestCase):
    def setUp(self):
        order = Order.objects.create(customer=Customer.objects.create(name='Acme'), due_date=timezone.localdate())
        self.item = OrderItem.objects.create(order=order, part_name='Shaft', quantity=1)
        self.url = f'/items/{self.item.pk}/drawing/'
        self.detail = f'/orders/{order.job_number}/'
        self.client.force_login(make_user('op', 'Operator'))

    def attach(self, name, data):
        self.item.drawing_file.save(name, SimpleUploadedFile(name, data))

    def test_no_drawing_shows_text_and_view_is_404(self):
        self.assertContains(self.client.get(self.detail), 'No drawing')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_pdf_is_streamed_with_link_and_no_preview(self):
        self.attach('a.pdf', b'%PDF-1.4 x')
        r = self.client.get(self.url)
        self.assertEqual((r.status_code, r['Content-Type'], b''.join(r.streaming_content)), (200, 'application/pdf', b'%PDF-1.4 x'))
        html = self.client.get(self.detail).content.decode()
        self.assertIn('Open drawing', html)
        self.assertNotIn('drawing-preview', html)

    def test_image_gets_inline_preview(self):
        self.attach('a.png', b'PNG-data')
        self.assertContains(self.client.get(self.detail), 'drawing-preview')
        self.assertEqual(self.client.get(self.url)['Content-Type'], 'image/png')

    def test_login_required_and_unknown_item_404(self):
        self.attach('a.pdf', b'%PDF-1.4 x')
        self.assertEqual(self.client.get('/items/9999/drawing/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.get(self.url).status_code, 403)


class CustomerTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('s', 'Admin'))
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)

    def add(self, name, contact='', **extra):
        return self.client.post('/customers/', {'name': name, 'contact': contact, **extra})

    def order_post(self, **fields):
        data = {
            'due_date': timezone.localdate() + timedelta(days=30), 'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0,
            'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000, 'items-0-part_name': 'P', 'items-0-quantity': 1,
            'items-0-route': [self.mat.pk], **fields,
        }
        return self.client.post('/orders/new/', data)

    def test_page_is_staff_only_and_in_the_nav(self):
        self.assertContains(self.client.get('/orders/'), 'href="/customers/"')
        self.client.logout()
        self.assertEqual(self.client.get('/customers/').status_code, 302)
        self.assertEqual(self.add('X').status_code, 302)
        self.assertFalse(Customer.objects.exists())

    def test_add_creates_a_customer_and_stays_on_the_page(self):
        r = self.add('  Acme   Corp ', 'Mr. A', )
        self.assertRedirects(r, '/customers/', fetch_redirect_response=False)
        c = Customer.objects.get()
        self.assertEqual((c.name, c.contact), ('Acme Corp', 'Mr. A'))
        self.assertContains(self.client.get('/customers/'), 'Customer Acme Corp added.')

    def test_duplicates_are_rejected(self):
        Customer.objects.create(name='Acme Corp')
        for name in ('acme corp', 'ACME CORP', '  Acme Corp  ', 'Acme  Corp', 'Acme　Corp', 'Ａｃｍｅ Corp'):
            r = self.add(name)
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'A customer with this name already exists.')
        self.assertEqual(Customer.objects.count(), 1)
        self.assertContains(self.add('   '), 'This field is required.')

    def test_table_counts_orders_and_search_filters(self):
        a = Customer.objects.create(name='Acme')
        Customer.objects.create(name='Beta')
        for _ in range(2):
            Order.objects.create(customer=a, due_date=timezone.localdate())
        r = self.client.get('/customers/')
        self.assertEqual([(c.name, c.order_count) for c in r.context['customers']], [('Acme', 2), ('Beta', 0)])
        self.assertEqual([c.name for c in self.client.get('/customers/', {'q': 'bet'}).context['customers']], ['Beta'])
        self.assertContains(self.client.get('/customers/', {'q': 'zzz'}), 'No customers match.')

    def test_order_with_only_a_new_name_creates_customer_and_order(self):
        r = self.order_post(new_customer='  Fresh  Client ')
        self.assertRedirects(r, '/orders/')
        self.assertEqual(Order.objects.get().customer.name, 'Fresh Client')
        self.assertEqual(Customer.objects.count(), 1)

    def test_typed_name_wins_over_the_dropdown(self):
        old = Customer.objects.create(name='Old')
        self.order_post(customer=old.pk, new_customer='Brand New')
        self.assertEqual(Order.objects.get().customer.name, 'Brand New')
        self.assertEqual(Customer.objects.count(), 2)

    def test_typed_name_matching_an_existing_customer_reuses_it(self):
        c = Customer.objects.create(name='Acme Corp')
        self.assertRedirects(self.order_post(new_customer='acme  CORP'), '/orders/')
        self.assertEqual((Order.objects.get().customer, Customer.objects.count()), (c, 1))

    def test_neither_field_gives_the_required_error_and_keeps_input(self):
        r = self.order_post(customer_order_number='PO-9')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'This field is required.')
        self.assertContains(r, 'value="PO-9"')
        self.assertFalse(Order.objects.exists())

    def test_failed_order_creates_no_customer_and_keeps_the_typed_name(self):
        r = self.order_post(new_customer='Ghost', due_date='')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="Ghost"')
        self.assertFalse(Customer.objects.exists())

    def test_button_in_heading_is_scoped_in_css(self):
        from django.conf import settings
        css = (settings.BASE_DIR / 'static' / 'css' / 'base.css').read_text(encoding='utf-8')
        self.assertIn('h1 .button-secondary', css)


class InactiveProcessTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('s', 'Admin'))
        self.live = Process.objects.create(name='Live', kind=K.MACHINING)
        self.old = Process.objects.create(name='RetiredOne', kind=K.MACHINING, active=False)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=9))
        self.item = OrderItem.objects.create(order=order, part_name='Old part', quantity=1)
        RouteStep.objects.create(item=self.item, sequence=1, process=self.old)
        self.order = order

    def route_ids(self, form):
        return [pk for pk, _label in form.fields['route'].choices]

    def test_new_route_form_excludes_an_inactive_process(self):
        r = self.client.get('/orders/new/')
        self.assertNotContains(r, 'RetiredOne')
        self.assertEqual(self.route_ids(r.context['formset'].empty_form), [self.live.pk])
        data = MultiValueDict({'part_name': ['P'], 'quantity': ['1'], 'box_count': ['1'], 'route': [str(self.old.pk)]})
        self.assertFalse(OrderItemForm(data).is_valid())

    def test_editing_an_order_keeps_its_inactive_process_selectable(self):
        r = self.client.get(f'/orders/{self.order.job_number}/edit/')
        self.assertContains(r, 'RetiredOne')
        form = r.context['formset'].forms[0]
        self.assertEqual(set(self.route_ids(form)), {self.live.pk, self.old.pk})
        data = MultiValueDict({'part_name': ['Old part'], 'quantity': ['1'], 'box_count': ['1'], 'route': [str(self.old.pk)]})
        self.assertTrue(OrderItemForm(data, instance=self.item).is_valid())
        self.assertEqual(self.route_ids(r.context['formset'].empty_form), [self.live.pk])  # new items: active only

    def test_order_detail_still_names_the_inactive_process(self):
        self.assertContains(self.client.get(f'/orders/{self.order.job_number}/'), 'RetiredOne')

    def test_admin_actions_flip_active(self):
        User.objects.create_superuser('root', 'r@example.com', 'x')
        self.client.login(username='root', password='x')
        post = lambda action, pks: self.client.post('/admin/orders/process/', {'action': action, '_selected_action': pks})
        post('mark_inactive', [self.live.pk])
        self.live.refresh_from_db()
        self.assertFalse(self.live.active)
        post('mark_active', [self.live.pk, self.old.pk])
        self.live.refresh_from_db()
        self.old.refresh_from_db()
        self.assertTrue(self.live.active and self.old.active)
        self.assertEqual(Process.objects.filter(active=True).count(), 2)  # no delete involved


class CustomerDetailEditTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('s', 'Admin'))
        self.acme = Customer.objects.create(name='Acme', contact='Mr. A')
        self.beta = Customer.objects.create(name='Beta Works')
        mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.make = lambda c, days, po='': self._order(c, days, po, mat)

    def _order(self, customer, days, po, proc):
        o = Order.objects.create(customer=customer, customer_order_number=po, due_date=timezone.localdate() + timedelta(days=days))
        item = OrderItem.objects.create(order=o, part_name='P', quantity=1)
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        return o

    def edit(self, customer, name, contact=''):
        return self.client.post(f'/customers/{customer.pk}/edit/', {'name': name, 'contact': contact})

    def test_pages_need_staff_and_unknown_id_is_404(self):
        urls = [f'/customers/{self.acme.pk}/', f'/customers/{self.acme.pk}/edit/']
        self.assertEqual([self.client.get(u).status_code for u in urls], [200, 200])
        self.assertEqual(self.client.get('/customers/9999/').status_code, 404)
        self.assertEqual(self.client.get('/customers/9999/edit/').status_code, 404)
        self.client.logout()
        self.assertEqual([self.client.get(u).status_code for u in urls], [302, 302])
        self.assertEqual(self.edit(self.acme, 'Hacked').status_code, 302)
        self.acme.refresh_from_db()
        self.assertEqual(self.acme.name, 'Acme')

    def test_list_links_to_the_detail_page(self):
        self.assertContains(self.client.get('/customers/'), f'href="/customers/{self.acme.pk}/"')

    def test_detail_lists_only_this_customers_orders_newest_first(self):
        first = self.make(self.acme, 5, 'PO-1')
        second = self.make(self.acme, -3, 'PO-2')
        other = self.make(self.beta, 5, 'PO-X')
        r = self.client.get(f'/customers/{self.acme.pk}/')
        self.assertEqual([x['order'] for x in r.context['rows']], [second, first])
        self.assertContains(r, f'href="/orders/{first.job_number}/"')
        self.assertContains(r, 'PO-2')
        self.assertNotContains(r, other.job_number)
        self.assertContains(r, 'Mr. A')
        self.assertContains(r, 'badge-not_started')
        self.assertContains(r, 'Overdue')
        self.assertContains(r, f'href="/customers/{self.acme.pk}/edit/"')
        self.assertContains(r, 'href="/customers/"')

    def test_detail_without_orders_says_so(self):
        self.assertContains(self.client.get(f'/customers/{self.beta.pk}/'), 'This customer has no orders yet.')

    def test_edit_saves_name_and_contact_and_returns_to_detail(self):
        r = self.edit(self.acme, '  Acme   Holdings ', ' 03-1111 ')
        self.assertRedirects(r, f'/customers/{self.acme.pk}/', fetch_redirect_response=False)
        self.acme.refresh_from_db()
        self.assertEqual((self.acme.name, self.acme.contact), ('Acme Holdings', '03-1111'))
        self.assertContains(self.client.get(f'/customers/{self.acme.pk}/'), 'Customer Acme Holdings updated.')

    def test_edit_form_is_prefilled(self):
        r = self.client.get(f'/customers/{self.acme.pk}/edit/')
        self.assertContains(r, 'value="Acme"')
        self.assertContains(r, 'value="Mr. A"')

    def test_renaming_to_another_customers_name_is_rejected(self):
        for name in ('Beta Works', 'beta works', ' BETA   WORKS ', 'Beta\u3000Works', 'Ｂｅｔａ Works'):
            r = self.edit(self.acme, name)
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'A customer with this name already exists.')
        self.acme.refresh_from_db()
        self.assertEqual(self.acme.name, 'Acme')

    def test_keeping_the_same_name_works(self):
        for name in ('Acme', 'ACME', ' Acme '):
            self.assertEqual(self.edit(self.acme, name, 'new').status_code, 302, name)
        self.acme.refresh_from_db()
        self.assertEqual((self.acme.name, self.acme.contact), ('Acme', 'new'))

    def test_orders_show_the_new_name_after_a_rename(self):
        order = self.make(self.acme, 5)
        self.edit(self.acme, 'Acme Renamed')
        self.assertContains(self.client.get(f'/orders/{order.job_number}/'), 'Acme Renamed')
        self.assertContains(self.client.get('/orders/'), 'Acme Renamed')
        self.assertNotContains(self.client.get('/orders/'), '>Acme<')
        self.assertEqual(Order.objects.get(pk=order.pk).customer_id, self.acme.pk)

    def test_heading_button_style_is_compact(self):
        from django.conf import settings
        css = (settings.BASE_DIR / 'static' / 'css' / 'base.css').read_text(encoding='utf-8')
        self.assertIn('font-size: 0.85rem; font-weight: 400', css)
