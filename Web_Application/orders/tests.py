import csv
import importlib
import io
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta

from django.apps import apps
from django.conf import settings

from django.contrib.admin import site
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from unittest import mock
from pypdf import PdfReader
from PIL import Image
from reportlab.pdfgen import canvas
from django.utils.datastructures import MultiValueDict
from django.utils import timezone, translation

from core.management.commands.seed_demo import read_csv
from core.testing import TestCase, make_user
from tracking.models import ScannerDevice, Tag, TagAssignment
from tracking.services import cancel_item, correct_event, record_event
from tracking.status import item_status, step_due_back, timeline

from . import export, importer, printing
from .pdf import item_pdf, order_pdf
from .forms import OrderItemForm, OrderItemFormSet
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
        return self.client.post('/orders/?tab=new', data)

    def login_staff(self):
        self.client.force_login(make_user('s', 'Admin'))

    def test_staff_only(self):
        self.assertEqual(self.client.get('/orders/?tab=new').status_code, 302)
        self.client.force_login(make_user('u', 'Operator'))
        self.assertEqual(self.client.get('/orders/?tab=new').status_code, 403)
        self.login_staff()
        self.assertEqual(self.client.get('/orders/?tab=new').status_code, 200)

    def test_valid_post_creates_order_and_two_items(self):
        self.login_staff()
        route = [self.material.pk]
        self.assertRedirects(self.post([('Shaft', 5, route), ('Gear', 2, route)]), f'/orders/?q={Order.objects.latest("pk").job_number}')
        order = Order.objects.get()
        codes = [i.item_code for i in OrderItem.objects.order_by('item_no')]
        self.assertEqual(codes, [f'{order.job_number}-01', f'{order.job_number}-02'])

    def test_box_count_is_saved_and_defaults_to_one(self):
        self.login_staff()
        route = [self.material.pk]
        self.assertRedirects(self.post([('Shaft', 5, route, 3), ('Gear', 2, route)]), f'/orders/?q={Order.objects.latest("pk").job_number}')
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

    def test_search_finds_material(self):
        self.c.items.update(material='SS400 flat bar')
        self.assertEqual(self.jobs(q='ss400'), [self.c.job_number])

    def test_overdue_filter(self):
        self.assertEqual(self.jobs(late='1'), [self.b.job_number])

    def test_status_filter(self):
        proc = Process.objects.create(name='Mat', kind=Process.Kind.MATERIAL)
        item = self.a.items.get()
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB'), item=item)
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
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB'), item=self.item)
        self.url = f'/orders/{self.order.job_number}/'

    def test_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_shows_job_number_and_part_name(self):
        r = self.client.get(self.url)
        self.assertContains(r, self.order.job_number)
        self.assertContains(r, 'Shaft')
        self.assertContains(r, 'Tag AB')

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
                tag=Tag.objects.create(uid=f'U{item.pk}{i}'), item=item)
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
        self.assertContains(r, f'Tag U{item.pk}0')
        self.assertContains(r, f'Tag U{item.pk}1')
        self.assertContains(r, 'Box 1 of 3')
        self.assertContains(r, 'Box 2 of 3')
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

    def link(self, item, uid, end=False):
        return TagAssignment.objects.create(
            tag=Tag.objects.create(uid=uid), item=item,
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
        self.link(self.item, 'T1')
        tier = lambda: self.client.get('/priorities/').context['rows'][0]['tier']
        self.assertEqual(tier(), 'ON_TRACK')
        self.post(due_date=timezone.localdate())
        self.assertEqual(tier(), 'CRITICAL')
        self.post(due_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(tier(), 'OVERDUE')

    def test_box_count_cannot_drop_below_linked_boxes(self):
        item = self.new_item('Frame', [self.mill], boxes=3)
        self.link(item, 'A', end=True)
        self.link(item, 'B')
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
        self.link(self.item, 'T1')
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
        self.link(self.item, 'T1')
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

    def post(self, route, vendors, url='/orders/?tab=new'):
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
        r = self.client.get('/orders/?tab=new')
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


class StepDueDateTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.plate = Process.objects.create(name='Plate', kind=K.OUTSOURCED)

    def post(self, route, dues, slips):
        data = {
            'customer': Customer.objects.create(name=f'C{Customer.objects.count()}').pk, 'due_date': timezone.localdate() + timedelta(days=30),
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
            'items-0-part_name': 'Shaft', 'items-0-quantity': 1, 'items-0-route': route,
            'items-0-route_due': dues, 'items-0-route_slip': slips,
        }
        return self.client.post('/orders/?tab=new', data)

    def test_due_date_and_slip_saved_per_step_with_blank_rows_skipped(self):
        r = self.post([self.mat.pk, '', self.plate.pk], ['', '2031-02-03', '2031-02-04'], ['', 'x', ' S-1 '])
        self.assertEqual(r.status_code, 302)
        steps = list(RouteStep.objects.values_list('process_id', 'due_date', 'slip_number'))
        self.assertEqual(steps, [(self.mat.pk, None, ''), (self.plate.pk, date(2031, 2, 4), 'S-1')])

    def test_invalid_date_is_rejected(self):
        self.assertEqual(self.post([self.plate.pk], ['not-a-date'], [''] ).status_code, 200)
        self.assertFalse(Order.objects.exists())

    def test_form_has_inputs_and_detail_shows_values(self):
        self.assertContains(self.client.get('/orders/?tab=new'), 'name="items-0-route_due"')
        self.post([self.plate.pk], ['2031-02-04'], ['S-1'])
        page = self.client.get(f'/orders/{Order.objects.get().job_number}/')
        self.assertContains(page, '2031-02-04')
        self.assertContains(page, 'slip S-1')

    def test_step_due_back_prefers_step_date_then_lead_days(self):
        step = RouteStep(process=Process(vendor_lead_days=5))
        out = timezone.make_aware(datetime(2030, 1, 2, 12))
        self.assertEqual(step_due_back(step, out), date(2030, 1, 7))
        step.due_date = date(2030, 1, 3)
        self.assertEqual(step_due_back(step, out), date(2030, 1, 3))
        self.assertIsNone(step_due_back(RouteStep(process=Process()), out))


class MaterialTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)

    def data(self, item=None, material='SS400'):
        order = item.order if item else None
        d = {
            'customer': (order.customer if order else Customer.objects.create(name=f'C{Customer.objects.count()}')).pk,
            'due_date': timezone.localdate() + timedelta(days=30),
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1 if item else 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
            'items-0-part_name': 'Shaft', 'items-0-quantity': 1, 'items-0-box_count': 1, 'items-0-route': [self.proc.pk],
            'items-0-material': material,
        }
        if item:
            d.update({'items-0-id': item.pk, 'items-0-order': order.pk})
        return d

    def test_material_saved_shown_and_optional(self):
        self.assertEqual(self.client.post('/orders/?tab=new', self.data()).status_code, 302)
        item = OrderItem.objects.get()
        self.assertEqual(item.material, 'SS400')
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), 'SS400')
        self.assertContains(self.client.get('/orders/?tab=new'), 'name="items-0-material"')
        self.assertEqual(self.client.post('/orders/?tab=new', self.data(material='')).status_code, 302)

    def test_edit_logs_material_change(self):
        self.client.post('/orders/?tab=new', self.data())
        item = OrderItem.objects.get()
        self.client.post(f'/orders/{item.order.job_number}/edit/', self.data(item, 'A5052'))
        change = OrderChange.objects.get(field='material')
        self.assertEqual((change.old_value, change.new_value, str(change.field_label)), ('SS400', 'A5052', 'material'))


class PartNumberTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)

    def data(self, item=None, **ids):
        order = item.order if item else None
        d = {
            'customer': (order.customer if order else Customer.objects.create(name=f'C{Customer.objects.count()}')).pk,
            'due_date': timezone.localdate() + timedelta(days=30),
            'items-TOTAL_FORMS': 1, 'items-INITIAL_FORMS': 1 if item else 0, 'items-MIN_NUM_FORMS': 1, 'items-MAX_NUM_FORMS': 1000,
            'items-0-part_name': 'Shaft', 'items-0-quantity': 1, 'items-0-box_count': 1, 'items-0-route': [self.proc.pk],
        }
        d.update({f'items-0-{k}': v for k, v in ids.items()})
        if item:
            d.update({'items-0-id': item.pk, 'items-0-order': order.pk})
        return d

    def test_fields_saved_shown_and_optional(self):
        self.client.post('/orders/?tab=new', self.data(part_number=' ab-12 ', drawing_number='D-9', revision='B'))
        item = OrderItem.objects.get()
        self.assertEqual((item.part_number, item.drawing_number, item.revision), ('AB-12', 'D-9', 'B'))
        page = self.client.get(f'/orders/{item.order.job_number}/')
        for text in ('AB-12', 'D-9', 'Revision'):
            self.assertContains(page, text)
        self.assertEqual(self.client.post('/orders/?tab=new', self.data()).status_code, 302)
        self.assertEqual(OrderItem.objects.exclude(pk=item.pk).get().part_number, '')

    def test_search_finds_by_part_and_drawing_number(self):
        self.client.post('/orders/?tab=new', self.data(part_number='ab-12', drawing_number='DWG-77'))
        self.client.post('/orders/?tab=new', self.data(part_number='ZZ-1'))
        job = OrderItem.objects.get(part_number='AB-12').order.job_number
        for q in ('ab-1', 'dwg-77'):
            rows = self.client.get('/orders/', {'q': q}).context['rows']
            self.assertEqual([r['order'].job_number for r in rows], [job])

    def test_edit_logs_revision_change(self):
        self.client.post('/orders/?tab=new', self.data(revision='A'))
        item = OrderItem.objects.get()
        self.client.post(f'/orders/{item.order.job_number}/edit/', self.data(item, revision='B'))
        change = OrderChange.objects.get(field='revision')
        self.assertEqual((change.old_value, change.new_value, str(change.field_label)), ('A', 'B', 'revision'))

    def test_sheet_shows_the_numbers(self):
        self.client.post('/orders/?tab=new', self.data(part_number='AB-12', drawing_number='D-9', revision='C'))
        job = OrderItem.objects.get().order.job_number
        text = PdfReader(io.BytesIO(self.client.get(f'/orders/{job}/sheet/').content)).pages[0].extract_text()
        for expected in ('AB-12', 'D-9 / C'):
            self.assertIn(expected, text)


class RouteCopyTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.mill = Process.objects.create(name='Milling', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plating', kind=K.OUTSOURCED)
        self.vendor = Vendor.objects.create(code='00131', name='Demo Plating', kind=Vendor.Kind.VENDOR)
        self.customer = Customer.objects.create(name='C')

    def make(self, part_number, processes, vendor=None, due=True):
        order = Order.objects.create(customer=self.customer, due_date=date(2031, 1, 1))
        item = OrderItem.objects.create(order=order, part_name='P', quantity=3, part_number=part_number)
        for i, p in enumerate(processes, 1):
            RouteStep.objects.create(item=item, sequence=i, process=p, vendor=vendor if p == self.plate else None,
                                     due_date=date(2031, 1, 5) if due else None, slip_number='S-1')
        return item

    def copy(self, part_number, **extra):
        r = self.client.get('/orders/route-copy/', {'part_number': part_number, **extra})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_copy_gives_the_same_steps_in_the_same_order_without_dates_or_slips(self):
        self.make('AB-1', [self.plate, self.mill], self.vendor)
        data = self.copy('ab-1')
        self.assertEqual(data['steps'], [{'process': self.plate.pk, 'vendor': self.vendor.pk}, {'process': self.mill.pk, 'vendor': None}])
        self.assertNotIn('due_date', data['steps'][0])
        self.assertNotIn('slip_number', data['steps'][0])

    def test_latest_matching_item_wins_and_cancelled_ones_are_skipped(self):
        old = self.make('AB-1', [self.mill])
        newest = self.make('AB-1', [self.plate, self.mill])
        self.make('OTHER', [self.plate])
        self.assertEqual(self.copy('AB-1')['job'], newest.item_code)
        cancel_item(make_user('pl', 'Planner'), newest, 'withdrawn')
        data = self.copy('AB-1')
        self.assertEqual((data['job'], len(data['steps'])), (old.item_code, 1))

    def test_no_match_and_own_item_give_found_false(self):
        item = self.make('AB-1', [self.mill])
        self.assertEqual(self.copy('ZZ-9'), {'found': False, 'steps': []})
        self.assertEqual(self.copy(''), {'found': False, 'steps': []})
        self.assertFalse(self.copy('AB-1', item=item.pk)['found'])  # only earlier items count

    def test_inactive_processes_are_skipped_and_counted(self):
        self.make('AB-1', [self.mill, self.plate], self.vendor)
        self.plate.active = False
        self.plate.save()
        data = self.copy('AB-1')
        self.assertEqual((len(data['steps']), data['skipped']), (1, 1))

    def test_planner_only_and_button_in_the_form(self):
        self.assertContains(self.client.get('/orders/?tab=new'), 'class="copy-route')
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/orders/route-copy/', {'part_number': 'x'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/orders/route-copy/').status_code, 302)



class CsvExportTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.vendor = Vendor.objects.create(code='00131', name='Demo Plating', kind=Vendor.Kind.VENDOR)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plating', kind=K.OUTSOURCED, default_vendor=self.vendor)
        self.device = ScannerDevice.objects.create(name='Bench 1')

    def make(self, customer='Acme', part='Shaft', notes='', due=None, number=''):
        order = Order.objects.create(customer=Customer.objects.get_or_create(name=customer)[0], due_date=due or timezone.localdate() + timedelta(days=10),
                                     notes=notes, customer_order_number=number)
        item = OrderItem.objects.create(order=order, part_name=part, quantity=4, material='S45C', part_number='ab-1', drawing_number='D-1', revision='B')
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        RouteStep.objects.create(item=item, sequence=2, process=self.plate, due_date=date(2031, 4, 1))
        return item

    def csv_rows(self, response):
        self.assertEqual(response.status_code, 200)
        raw = b''.join(response.streaming_content)
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        return list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))

    def test_order_export_has_the_columns_and_one_row_per_item(self):
        item = self.make(notes='Rush')
        OrderItem.objects.create(order=item.order, part_name='Gear', quantity=1)
        r = self.client.get('/orders/export/')
        self.assertRegex(r['Content-Disposition'], r'^attachment; filename="orders_\d{8}\.csv"$')
        rows = self.csv_rows(r)
        self.assertEqual(rows[0], ['customer', 'customer_order_number', 'due_date', 'part_name', 'quantity', 'material', 'notes', 'route',
                                   'part_number', 'drawing_number', 'revision', 'job_number', 'item_code', 'status'])
        self.assertEqual(len(rows), 3)
        shaft = dict(zip(rows[0], rows[1]))
        self.assertEqual((shaft['customer'], shaft['part_name'], shaft['quantity'], shaft['notes']), ('Acme', 'Shaft', '4', 'Rush'))
        self.assertEqual((shaft['part_number'], shaft['drawing_number'], shaft['revision']), ('AB-1', 'D-1', 'B'))
        self.assertEqual((shaft['job_number'], shaft['item_code'], shaft['status']), (item.order.job_number, item.item_code, 'Not started'))
        self.assertEqual(shaft['route'], 'Mill|Plating:00131:2031-04-01')

    def test_filters_change_the_export(self):
        self.make(customer='Acme', part='Shaft')
        self.make(customer='Beta', part='Gear')
        late = self.make(customer='Late Co', part='Plate', due=timezone.localdate() - timedelta(days=3))
        parts = lambda **params: sorted(r[3] for r in self.csv_rows(self.client.get('/orders/export/', params))[1:])
        self.assertEqual(parts(), ['Gear', 'Plate', 'Shaft'])
        self.assertEqual(parts(q='gear'), ['Gear'])
        self.assertEqual(parts(late='1'), ['Plate'])
        self.assertEqual(parts(status='COMPLETE'), [])
        self.assertContains(self.client.get('/orders/', {'q': 'gear'}), 'orders/export/?q=gear')
        self.assertEqual(late.part_name, 'Plate')

    def test_japanese_text_survives(self):
        self.make(customer='東洋機械', part='ブラケット', notes='急ぎ / Rush')
        row = self.csv_rows(self.client.get('/orders/export/'))[1]
        self.assertEqual((row[0], row[3], row[6]), ('東洋機械', 'ブラケット', '急ぎ / Rush'))

    def test_cells_that_look_like_formulas_are_escaped(self):
        self.make(part='=HYPERLINK("http://x")', notes='-1+1')
        row = self.csv_rows(self.client.get('/orders/export/'))[1]
        self.assertEqual((row[3], row[6]), ('\'=HYPERLINK("http://x")', "'-1+1"))
        self.assertEqual(export.safe('@SUM'), "'@SUM")
        self.assertEqual(export.safe('plain'), 'plain')
        self.assertEqual(export.safe(5), 5)

    def test_event_export_lists_scans_cancellations_and_voids(self):
        item = self.make()
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid='ex1'), item=item,
                                         start_at=timezone.now() - timedelta(days=3))
        wrong = record_event(self.device, 'EX1', self.mill, 'STARTED', occurred_at=timezone.now() - timedelta(days=2))[0]
        record_event(self.device, 'EX1', self.mill, 'FINISHED', occurred_at=timezone.now() - timedelta(days=1))
        correct_event(wrong, self.planner, 'wrong scan')
        cancel_item(self.planner, item, 'withdrawn')
        r = self.client.get(f'/orders/{item.order.job_number}/events.csv')
        self.assertEqual(r['Content-Disposition'], f'attachment; filename="events_{item.order.job_number}.csv"')
        rows = self.csv_rows(r)
        self.assertEqual(rows[0], ['time_jst', 'event_type', 'process', 'device', 'tag_uid', 'voided', 'item_code'])
        body = [dict(zip(rows[0], x)) for x in rows[1:]]
        self.assertEqual([(x['event_type'], x['voided']) for x in body], [('Started', 'yes'), ('Finished', 'no'), ('Cancelled', 'no')])
        self.assertEqual((body[0]['process'], body[0]['device'], body[0]['tag_uid'], body[0]['item_code']), ('Mill', 'Bench 1', 'EX1', item.item_code))
        self.assertRegex(body[0]['time_jst'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
        self.assertEqual(body[2]['device'], 'pl')
        self.assertEqual(a.tag.uid, 'EX1')

    def test_event_export_shows_the_process_code(self):
        item = self.make()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='EX2'), item=item,
                                     start_at=timezone.now() - timedelta(days=3))
        record_event(self.device, 'EX2', self.plate, 'SENT_OUT', occurred_at=timezone.now() - timedelta(days=1))
        rows = self.csv_rows(self.client.get(f'/orders/{item.order.job_number}/events.csv'))
        self.assertEqual(rows[1][2], '00131 Plating')

    def test_planner_only_and_buttons(self):
        item = self.make()
        urls = ['/orders/export/', f'/orders/{item.order.job_number}/events.csv']
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), urls[1])
        self.assertContains(self.client.get('/orders/'), urls[0])
        self.client.force_login(make_user('op', 'Operator'))
        self.assertNotContains(self.client.get(f'/orders/{item.order.job_number}/'), urls[1])
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)



class PreviousRunsTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.device = ScannerDevice.objects.create(name='D')
        self.vendor = Vendor.objects.create(code='00131', name='Demo Plating', kind=Vendor.Kind.VENDOR)
        self.mill = Process.objects.create(name='Milling', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plating', kind=K.OUTSOURCED, default_vendor=self.vendor)
        self.customer = Customer.objects.create(name='C')
        self.n = 0

    def make(self, part_number='ab-1', finished=True, base=None):
        """An item of the part; finished=True runs milling (2h) then plating (3 days out), starting at base."""
        self.n += 1
        order = Order.objects.create(customer=self.customer, due_date=date(2031, 1, 1))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=3, part_number=part_number)
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        RouteStep.objects.create(item=item, sequence=2, process=self.plate)
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid=f'PR{self.n}'), item=item,
                                         start_at=timezone.now() - timedelta(days=400))
        if finished:
            t = base or timezone.now() - timedelta(days=20)
            at = lambda process, kind, delta: record_event(self.device, a.tag.uid, process, kind, occurred_at=t + delta)
            at(self.mill, 'STARTED', timedelta(hours=1))
            at(self.mill, 'FINISHED', timedelta(hours=3))
            at(self.plate, 'SENT_OUT', timedelta(days=1))
            at(self.plate, 'RETURNED', timedelta(days=4))
        return item

    def runs(self, item):
        r = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertEqual(r.status_code, 200)
        return r, r.context['items'][0]['runs']

    def test_runs_are_newest_first_case_insensitive_and_limited_to_five(self):
        old = [self.make('AB-1' if i % 2 else 'ab-1') for i in range(7)]
        self.make('OTHER')
        current = self.make('Ab-1', finished=False)
        _r, runs = self.runs(current)
        self.assertEqual([x['item'] for x in runs], list(reversed(old))[:5])

    def test_current_item_later_items_and_cancelled_runs_are_left_out(self):
        first = self.make()
        gone = self.make(finished=False)
        cancel_item(self.planner, gone, 'withdrawn')
        current = self.make(finished=False)
        self.make()  # created after the current item
        self.assertEqual([x['item'] for x in self.runs(current)[1]], [first])

    def test_durations_match_the_order_page_timeline_with_vendor_and_dates(self):
        run = self.make(base=timezone.make_aware(datetime(2030, 12, 1, 12, 0)))
        current = self.make(finished=False)
        r, runs = self.runs(current)
        expected = [t['duration'] for t in timeline(item_status(run).rows)]
        self.assertEqual([s['duration'] for s in runs[0]['steps']], expected)
        self.assertEqual(expected, [timedelta(hours=2), timedelta(days=3)])
        self.assertEqual(runs[0]['steps'][1]['vendor'], '00131 Demo Plating')
        self.assertEqual(runs[0]['total_days'], 4)  # first start to last return
        finish = timezone.localdate(item_status(run).rows[1].finished_at)
        self.assertEqual(runs[0]['finished'], finish)
        for text in (run.item_code, f'{finish:%Y-%m-%d}', 'Milling', '2h 0m', 'Plating', '00131 Demo Plating', '3d 0h'):
            self.assertContains(r, text)

    def test_unfinished_run_has_no_finish_date(self):
        run = self.make(finished=False)
        record_event(self.device, TagAssignment.objects.get(item=run).tag.uid, self.mill, 'STARTED')
        _r, runs = self.runs(self.make(finished=False))
        self.assertEqual((runs[0]['item'], runs[0]['finished'], runs[0]['total_days']), (run, None, 0))

    def test_box_is_hidden_without_runs_or_part_number(self):
        alone = self.make(finished=False)
        r, runs = self.runs(alone)
        self.assertEqual(runs, [])
        self.assertNotContains(r, 'Previous runs of this part')
        nothing = self.make(part_number='', finished=False)
        self.assertNotContains(self.runs(nothing)[0], 'Previous runs of this part')
        r, runs = self.runs(self.make(finished=False))
        self.assertContains(r, 'Previous runs of this part')


DEMO_DIR = settings.BASE_DIR / 'docs' / 'demo-data'


class CsvImportTests(TestCase):
    CUSTOMERS = ('Toyo Machinery', 'Sakura Robotics', 'Nippon Gear', 'Kita Valve')

    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        for r in read_csv('codes.csv'):
            Vendor.objects.create(code=r['code'], name=r['name'], name_ja=r['name_ja'], kind=r['kind'])
        for r in read_csv('processes.csv'):
            Process.objects.create(name=r['name'], name_ja=r['name_ja'], kind=r['kind'])

    def customers(self):
        for name in self.CUSTOMERS:
            Customer.objects.create(name=name)

    def upload(self, name='orders_demo.csv', create=False, raw=None):
        data = {'file': SimpleUploadedFile(name, raw if raw is not None else (DEMO_DIR / name).read_bytes())}
        if create:
            data['create_customers'] = 'on'
        return self.client.post('/orders/?tab=import', data)

    def confirm(self):
        return self.client.post('/orders/?tab=import', {'confirm': '1'})

    def rows(self, response):
        return {r.number: r for r in response.context['preview'].rows}

    def test_planner_only(self):
        self.client.logout()
        self.assertEqual(self.client.get('/orders/?tab=import').status_code, 302)
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/orders/?tab=import').status_code, 403)
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertContains(self.client.get('/orders/?tab=import'), 'name="file"')

    def test_clean_file_imports_seven_orders_and_ten_items(self):
        self.customers()
        r = self.upload()
        self.assertTrue(r.context['preview'].ok)
        self.assertNotContains(r, 'badge-overdue')
        self.assertRedirects(self.confirm(), '/orders/')
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (7, 10))
        toyo = Order.objects.get(customer_order_number='PO-7101')
        self.assertEqual(toyo.items.count(), 2)
        shaft = toyo.items.get(part_name='Drive shaft')
        self.assertEqual((shaft.material, shaft.quantity, shaft.steps.count()), ('S45C', 5, 5))
        plating = shaft.steps.get(process__name='Plating')
        self.assertEqual((plating.vendor.code, plating.due_date), ('00131', date(2026, 11, 2)))
        self.assertEqual(Order.objects.get(customer_order_number='SR-2210').notes, '急ぎ / Rush job')
        self.assertEqual(Order.objects.filter(customer_order_number='').count(), 1)
        self.assertEqual(len({o.job_number for o in Order.objects.all()}), 7)

    def test_create_customers_stays_ticked_after_a_check_and_unticked_otherwise(self):
        ticked = self.upload(create=True)
        self.assertRegex(ticked.content.decode(), r'<input type="checkbox" name="create_customers"[^>]* checked')
        self.assertNotRegex(self.upload().content.decode(), r'name="create_customers"[^>]* checked')
        self.assertContains(self.upload(), 'enctype="multipart/form-data"')

    def test_shift_jis_and_plain_utf8_give_the_same_result(self):
        self.customers()
        utf8 = self.upload()
        for raw in ((DEMO_DIR / 'orders_demo_sjis.csv').read_bytes(), (DEMO_DIR / 'orders_demo.csv').read_text(encoding='utf-8-sig').encode()):
            r = self.upload(raw=raw)
            self.assertTrue(r.context['preview'].ok)
            self.assertEqual([x.data for x in r.context['preview'].rows], [x.data for x in utf8.context['preview'].rows])
        self.assertEqual(self.rows(self.upload('orders_demo_sjis.csv'))[4].data['notes'], '急ぎ / Rush job')
        self.confirm()
        self.assertEqual((Order.objects.count(), OrderItem.objects.count()), (7, 10))

    def test_unknown_customers_need_the_option(self):
        r = self.upload()
        self.assertFalse(r.context['preview'].ok)
        self.assertEqual(self.rows(r)[2].errors, ['Unknown customer'])
        r = self.upload(create=True)
        self.assertEqual(r.context['preview'].new_customers, list(self.CUSTOMERS))
        self.confirm()
        self.assertEqual(sorted(Customer.objects.values_list('name', flat=True)), sorted(self.CUSTOMERS))
        self.assertEqual(Order.objects.count(), 7)

    def test_error_rows_give_their_expected_messages(self):
        self.upload(create=True)
        self.confirm()
        before = Order.objects.count()
        r = self.upload('orders_demo_errors.csv')
        rows = self.rows(r)
        expected = {
            2: 'Unknown customer', 3: 'Invalid due date', 4: 'Quantity must be at least 1', 5: 'Unknown code 00999',
            6: 'Code 00011 does not fit Plating: it needs a Vendor code', 7: 'Part name missing', 8: 'Route required',
            9: 'Malformed route step "Material-00101"', 10: 'An order with this customer order number already exists',
            11: 'Unknown process "Unknown process"',
        }
        for number, message in expected.items():
            self.assertEqual(rows[number].errors, [message], number)
        self.assertTrue(rows[12].ok)
        self.assertFalse(r.context['preview'].ok)
        self.assertNotContains(r, 'name="confirm"')
        self.confirm()  # all-or-nothing: the one valid row is not imported either
        self.assertEqual(Order.objects.count(), before)
        self.assertTrue(self.rows(self.upload('orders_demo_errors.csv', create=True))[2].ok)

    def test_new_columns_fill_the_fields_and_old_files_still_import(self):
        self.customers()
        head = 'customer,customer_order_number,due_date,part_name,part_number,drawing_number,revision,quantity,route'
        text = '\n'.join([head, 'Nippon Gear,A1,2030-01-05,P1, ab-1 ,D-1,C,1,Lathe:00012', ''])
        self.upload(raw=text.encode())
        self.confirm()
        item = OrderItem.objects.get()
        self.assertEqual((item.part_number, item.drawing_number, item.revision), ('AB-1', 'D-1', 'C'))
        self.upload()  # the demo file has none of the three columns
        self.confirm()
        self.assertEqual(OrderItem.objects.filter(part_number='').count(), 10)

    def test_new_columns_have_length_limits(self):
        self.customers()
        head = 'customer,due_date,part_name,part_number,revision,quantity,route'
        text = '\n'.join([head, f'Nippon Gear,2030-01-05,P1,{"X" * 51},12345678901,1,Lathe:00012', ''])
        errors = self.rows(self.upload(raw=text.encode()))[2].errors
        self.assertEqual(errors, ['Part number is too long (at most 50 characters)', 'Revision is too long (at most 10 characters)'])

    def test_japanese_process_name_and_group_due_dates(self):
        self.customers()
        head = 'customer,customer_order_number,due_date,part_name,quantity,material,notes,route'
        text = '\n'.join([head, 'Nippon Gear,A1,2030/01/05,P1,1,,,フライス:00011', 'Nippon Gear,A1,2030-01-06,P2,1,,,Lathe:00012', ''])
        rows = self.rows(self.upload(raw=text.encode()))
        self.assertEqual(rows[2].steps[0][0].name, 'Milling')
        self.assertEqual(rows[2].due, date(2030, 1, 5))
        self.assertEqual(rows[3].errors, ['Due date differs from the first row of this order'])

    def test_bad_files(self):
        self.assertContains(self.upload(raw=b'\x81\x20'), 'Could not read the file')
        self.assertContains(self.upload(raw=b'\n'), 'Missing column: customer')
        self.assertEqual(importer.parse('').file_errors, ['The file is empty.'])
        self.assertContains(self.upload(raw=b'customer,route\nA,B\n'), 'Missing column: due_date')
        self.assertContains(self.upload(raw=b'customer,customer_order_number,due_date,part_name,quantity,route\n'), 'The file has no data rows.')
        self.assertContains(self.client.post('/orders/?tab=import', {}), 'Choose a CSV file.')
        self.assertEqual(Order.objects.count(), 0)

    def test_confirm_without_upload_does_nothing(self):
        self.assertRedirects(self.confirm(), '/orders/?tab=import')


class CodeListTests(TestCase):
    def setUp(self):
        Vendor.objects.create(code='00010', name='Lathe shop', kind=Vendor.Kind.INTERNAL)
        Vendor.objects.create(code='00150', name='Plating Co', name_ja='めっき工業', kind=Vendor.Kind.VENDOR, lead_days=5)

    def codes(self, query=''):
        return [v.code for v in self.client.get('/orders/?tab=codes' + query.replace('?', '&')).context['vendors']]

    def test_roles(self):
        self.assertEqual(self.client.get('/orders/?tab=codes').status_code, 302)
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/orders/?tab=codes').status_code, 403)
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertEqual(self.client.get('/orders/?tab=codes').status_code, 200)

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


def make_pdf(pages):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for n in range(pages):
        c.drawString(100, 700, f'DRAWING-PAGE-{n}')
        c.showPage()
    c.save()
    return buf.getvalue()


def make_png():
    buf = io.BytesIO()
    Image.new('RGB', (400, 200), 'white').save(buf, 'PNG')
    return buf.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OrderSheetAccessTests(TestCase):
    def test_operator_opens_sheet_and_anonymous_is_sent_to_login(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=date(2031, 5, 6))
        url = f'/orders/{order.job_number}/sheet/'
        self.assertRedirects(self.client.get(url), f'/login/?next={url}', fetch_redirect_response=False)
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(f'/orders/{order.job_number}/'), 'Preview all sheets')


class SheetBase(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.vendor = Vendor.objects.create(code='00131', name='Demo Plating', name_ja='デモメッキ', kind=Vendor.Kind.VENDOR)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.plate = Process.objects.create(name='Plating', name_ja='メッキ', kind=K.OUTSOURCED, default_vendor=self.vendor)
        order = Order.objects.create(customer=Customer.objects.create(name='Acme Corp'), customer_order_number='PO-77',
                                     due_date=date(2031, 5, 6), notes='Handle with care')
        self.order = order
        self.items = [self.item(order, 'Drive shaft', 5), self.item(order, 'Flange', 3)]
        self.url = f'/orders/{order.job_number}/sheet/'

    def item(self, order, part, qty):
        item = OrderItem.objects.create(order=order, part_name=part, quantity=qty, material='S45C')
        RouteStep.objects.create(item=item, sequence=1, process=self.mat)
        RouteStep.objects.create(item=item, sequence=2, process=self.plate, vendor=self.vendor, due_date=date(2031, 4, 1), slip_number='S-9')
        return item

    def attach(self, item, name, data):
        item.drawing_file.save(name, SimpleUploadedFile(name, data))

    def pdf(self):
        r = self.client.get(self.url)
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
        return PdfReader(io.BytesIO(r.content))


class OrderSheetTests(SheetBase):
    def test_operator_can_print_but_anonymous_cannot(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_button_on_detail_page_for_operators_and_up(self):
        for role in ('Planner', 'Operator'):
            self.client.force_login(make_user(role.lower(), role))
            self.assertContains(self.client.get(f'/orders/{self.order.job_number}/'), self.url)

    def test_one_page_per_item_without_drawings_with_details_and_embedded_font(self):
        reader = self.pdf()
        self.assertEqual(len(reader.pages), 2)
        text = reader.pages[0].extract_text()
        for expected in (self.items[0].item_code, 'Acme Corp', 'PO-77', 'Drive shaft', 'S45C', '2031-05-06', 'Handle with care',
                         'Work instruction sheet', 'Mat', 'Plating', '00131 Demo Plating', '2031-04-01'):
            self.assertIn(expected, text)
        self.assertIn(self.items[1].item_code, reader.pages[1].extract_text())
        self.assertIn(b'FontFile2', self.client.get(self.url).content)

    def test_drawings_follow_their_sheet_so_duplex_puts_them_on_the_back(self):
        self.attach(self.items[0], 'a.pdf', make_pdf(2))
        self.attach(self.items[1], 'b.png', make_png())
        reader = self.pdf()
        # sheet + 2 drawing pages + a blank to keep the next sheet on a front; then sheet + image
        self.assertEqual(len(reader.pages), 4 + 2)
        self.assertIn('DRAWING-PAGE-0', reader.pages[1].extract_text())
        self.assertIn('DRAWING-PAGE-1', reader.pages[2].extract_text())
        self.assertEqual(reader.pages[3].extract_text().strip(), '')
        self.assertIn(self.items[1].item_code, reader.pages[4].extract_text())

    def test_japanese_text_is_written_in_japanese(self):
        self.client.cookies['django_language'] = 'ja'
        text = self.pdf().pages[0].extract_text()
        self.assertIn('作業指示書', text)
        self.assertIn('メッキ', text)
        self.assertIn('00131 デモメッキ', text)

    def test_unreadable_drawing_gives_a_note_page(self):
        self.attach(self.items[0], 'bad.pdf', b'not a pdf')
        reader = self.pdf()
        self.assertEqual(len(reader.pages), 2 + 1)
        self.assertIn('The drawing could not be read.', reader.pages[1].extract_text())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ItemPdfTests(SheetBase):
    def reader(self, item):
        return PdfReader(io.BytesIO(item_pdf(item)))

    def test_item_without_drawing_is_its_sheet_only(self):
        r = self.reader(self.items[0])
        self.assertEqual(len(r.pages), 1)
        self.assertIn(self.items[0].item_code, r.pages[0].extract_text())

    def test_pdf_drawing_lands_on_the_back(self):
        self.attach(self.items[0], 'a.pdf', make_pdf(1))
        r = self.reader(self.items[0])
        self.assertEqual(len(r.pages), 2)
        self.assertIn('DRAWING-PAGE-0', r.pages[1].extract_text())

    def test_two_page_sheet_gets_a_blank_before_the_drawing(self):
        self.attach(self.items[0], 'a.pdf', make_pdf(1))
        with mock.patch('orders.pdf._sheet', return_value=make_pdf(2)):
            r = self.reader(self.items[0])
        self.assertEqual(len(r.pages), 4)
        self.assertEqual(r.pages[2].extract_text().strip(), '')
        self.assertIn('DRAWING-PAGE-0', r.pages[3].extract_text())

    def test_page_count_is_even_with_a_drawing(self):
        self.attach(self.items[0], 'a.pdf', make_pdf(2))
        self.assertEqual(len(self.reader(self.items[0]).pages), 4)

    def test_png_and_unreadable_drawings(self):
        self.attach(self.items[0], 'b.png', make_png())
        self.assertEqual(len(self.reader(self.items[0]).pages), 2)
        self.attach(self.items[1], 'bad.pdf', b'not a pdf')
        self.assertIn('The drawing could not be read.', self.reader(self.items[1]).pages[1].extract_text())

    def test_order_pdf_is_the_sum_of_its_items(self):
        self.attach(self.items[0], 'a.pdf', make_pdf(2))
        total = len(self.reader(self.items[0]).pages) + len(self.reader(self.items[1]).pages)
        self.assertEqual(len(PdfReader(io.BytesIO(order_pdf(self.order))).pages), total)


class PrintItemTests(SheetBase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp()) / 'print'  # does not exist yet
        self.item_ = self.items[0]

    def run_print(self, **kw):
        with override_settings(PRINT_DIR=self.dir), mock.patch('orders.printing.print_pdf', **kw) as m:
            return printing.print_item(self.item_), m

    def test_saves_the_sheet_prints_it_and_returns_the_job_id(self):
        job, m = self.run_print(return_value='job-7')
        path = self.dir / f'{self.item_.item_code}.pdf'
        self.assertEqual(job, 'job-7')
        m.assert_called_once_with(str(path))
        self.assertTrue(path.read_bytes().startswith(b'%PDF'))

    def test_second_call_overwrites_and_leaves_no_temp_file(self):
        self.run_print(return_value='a')
        path = self.dir / f'{self.item_.item_code}.pdf'
        path.write_bytes(b'old')
        self.run_print(return_value='b')
        self.assertTrue(path.read_bytes().startswith(b'%PDF'))
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_printer_error_propagates(self):
        with self.assertRaisesMessage(RuntimeError, 'paused'):
            self.run_print(side_effect=RuntimeError('paused'))

    def test_placeholder(self):
        f = self.dir.parent / 'x.pdf'
        with self.assertRaises(FileNotFoundError):
            printing.print_pdf(str(f))
        f.write_bytes(b'%PDF')
        with override_settings(PRINT_DRY_RUN=False), self.assertRaises(RuntimeError):
            printing.print_pdf(str(f))
        with override_settings(PRINT_DRY_RUN=True):
            self.assertEqual(printing.print_pdf(str(f)), 'dry-run')


class ItemPrintViewTests(SheetBase):
    def setUp(self):
        super().setUp()
        self.item_ = self.items[0]
        self.url = f'/items/{self.item_.pk}/print/'
        self.page = f'/orders/{self.order.job_number}/'
        self.client.force_login(make_user('op', 'Operator'))

    def post(self, **kw):
        with mock.patch('orders.views.print_item', **kw) as m:
            return self.client.post(self.url, follow=True), m

    def test_print_redirects_to_the_item_card_with_a_success_message(self):
        r, m = self.post(return_value='job-9')
        m.assert_called_once_with(self.item_)
        self.assertRedirects(r, f'{self.page}#item-{self.item_.pk}', fetch_redirect_response=False)
        self.assertContains(r, 'sent to the printer')
        self.assertContains(r, 'job-9')

    def test_get_is_405_and_prints_nothing(self):
        with mock.patch('orders.views.print_item') as m:
            self.assertEqual(self.client.get(self.url).status_code, 405)
        m.assert_not_called()

    def test_anonymous_and_non_operator(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.force_login(make_user('nobody'))
        with mock.patch('orders.views.print_item') as m:
            self.assertEqual(self.client.post(self.url).status_code, 403)
        m.assert_not_called()

    def test_printer_errors_become_messages_not_500s(self):
        for exc in (RuntimeError('Printer is paused'), FileNotFoundError('gone')):
            with self.assertLogs('orders.views', 'ERROR'):
                r, _m = self.post(side_effect=exc)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, str(exc))
            self.assertContains(r, self.item_.item_code)

    def test_unknown_item_is_404(self):
        self.assertEqual(self.client.post('/items/9999/print/').status_code, 404)
        self.assertEqual(self.client.get('/items/9999/sheet/').status_code, 404)

    def test_item_sheet_is_an_inline_pdf(self):
        r = self.client.get(f'/items/{self.item_.pk}/sheet/')
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.client.logout()
        self.assertEqual(self.client.get(f'/items/{self.item_.pk}/sheet/').status_code, 302)

    def test_order_page_has_one_print_form_per_item_and_the_preview_link(self):
        html = self.client.get(self.page).content.decode()
        self.assertEqual(html.count('action="/items/'), 2)
        self.assertIn('Preview all sheets', html)
        self.client.force_login(make_user('nobody'))  # no role: the page itself is closed
        self.assertEqual(self.client.get(self.page).status_code, 403)


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
        return self.client.post('/orders/?tab=new', data)

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
        self.assertRedirects(r, f'/orders/?q={Order.objects.latest("pk").job_number}')
        self.assertEqual(Order.objects.get().customer.name, 'Fresh Client')
        self.assertEqual(Customer.objects.count(), 1)

    def test_typed_name_wins_over_the_dropdown(self):
        old = Customer.objects.create(name='Old')
        self.order_post(customer=old.pk, new_customer='Brand New')
        self.assertEqual(Order.objects.get().customer.name, 'Brand New')
        self.assertEqual(Customer.objects.count(), 2)

    def test_typed_name_matching_an_existing_customer_reuses_it(self):
        c = Customer.objects.create(name='Acme Corp')
        self.assertRedirects(self.order_post(new_customer='acme  CORP'), f'/orders/?q={Order.objects.latest("pk").job_number}')
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
        r = self.client.get('/orders/?tab=new')
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
        self.assertIn('font-size: var(--text-sm); font-weight: 400', css)


class OrdersHubTests(TestCase):
    def login(self, role='Planner'):
        self.client.force_login(make_user(role.lower(), role))

    def test_old_urls_redirect_to_their_tab(self):
        self.login()
        for old, tab in (('/orders/new/', 'new'), ('/orders/import/', 'import'), ('/codes/', 'codes')):
            self.assertRedirects(self.client.get(old), f'/orders/?tab={tab}')
        self.assertRedirects(self.client.get('/codes/?q=lathe'), '/orders/?tab=codes&q=lathe')
        self.assertEqual(self.client.post('/orders/import/', {}).status_code, 308)

    def test_tab_bar_marks_the_active_tab_and_keeps_it_on_refresh(self):
        self.login()
        for tab in ('orders', 'new', 'import', 'codes'):
            r = self.client.get(f'/orders/?tab={tab}')
            href = '/orders/' if tab == 'orders' else f'/orders/?tab={tab}'
            self.assertEqual(r.context['tab'], tab)
            self.assertContains(r, f'class="tab is-active" href="{href}" aria-current="page"')
            self.assertContains(r, 'class="tab-icon"')
        self.assertEqual(self.client.get('/orders/?tab=nonsense').context['tab'], 'orders')

    def test_operator_sees_only_the_orders_tab(self):
        self.login('Operator')
        self.assertEqual([t['key'] for t in self.client.get('/orders/').context['tabs']], ['orders'])
        for tab in ('new', 'import', 'codes'):
            self.assertEqual(self.client.get(f'/orders/?tab={tab}').status_code, 403)

    def test_nav_has_one_orders_link(self):
        self.login()
        html = self.client.get('/orders/').content.decode()
        nav = html[html.index('id="site-nav"'):html.index('</nav>')]
        self.assertNotIn('tab=', nav)
        self.assertNotIn('/orders/new/', nav)
        self.assertNotIn('/codes/', nav)
        self.assertEqual(nav.count('href="/orders/"'), 1)

    def test_orders_tab_table_sits_in_a_scroll_panel(self):
        self.login()
        self.assertContains(self.client.get('/orders/'), 'class="scroll-panel scroll-panel-first"')

    def test_import_tab_text_is_translated_and_checkbox_is_in_its_label(self):
        self.login()
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ja'
        html = self.client.get('/orders/?tab=import').content.decode()
        self.assertIn('<code>customer, customer_order_number, due_date, part_name, quantity, material, notes, route</code>', html)
        self.assertIn('列:', html)
        self.assertIn('工程順の1行は', html)
        self.assertNotIn('route step is written', html)
        self.assertRegex(html, r'<label class="check" for="id_create_customers"><input type="checkbox" name="create_customers"[^>]*><span>新規の得意先を作成する</span></label>')
        self.assertIn('accept=".csv,text/csv"', html)

    def test_checkbox_rule_is_scoped_to_check_labels_in_cards(self):
        css = (settings.BASE_DIR / 'static' / 'css' / 'base.css').read_text(encoding='utf-8')
        self.assertIn('.card .check input { width: auto;', css)


class CodesTabTests(TestCase):
    URL = '/orders/?tab=codes'

    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def add(self, **kw):
        data = {'action': 'save', 'code': '00200', 'name': 'New Plating', 'name_ja': '', 'kind': '', 'lead_days': '', 'notes': 'n'}
        return self.client.post(self.URL, {**data, **kw})

    def test_add_suggests_kind_from_range_and_allows_override(self):
        self.add(code='00020', name='In shop')
        self.add(code='00200', name='Outside')
        self.add(code='00201', name='Metal supplier', kind='SUPPLIER')
        kinds = dict(Vendor.objects.values_list('code', 'kind'))
        self.assertEqual(kinds, {'00020': 'INTERNAL', '00200': 'VENDOR', '00201': 'SUPPLIER'})
        self.assertEqual(Vendor.objects.get(code='00200').notes, 'n')

    def test_validation_digits_unique_and_range(self):
        self.assertContains(self.add(code='12'), 'exactly 5 digits')
        self.add()
        self.assertEqual(Vendor.objects.count(), 1)
        self.assertContains(self.add(), 'already exists')
        self.assertContains(self.add(code='00500', name='Too high'), 'outside the range')
        self.assertContains(self.add(code='00050', name='Wrong kind', kind='VENDOR'), 'outside the range')

    def test_edit_and_deactivate_keep_the_record(self):
        self.add()
        v = Vendor.objects.get()
        self.assertContains(self.client.get(f'{self.URL}&edit={v.pk}'), 'Edit code 00200')
        self.client.post(self.URL, {'action': 'save', 'vendor': v.pk, 'code': '00200', 'name': 'Renamed', 'kind': 'VENDOR'})
        self.assertEqual(Vendor.objects.get().name, 'Renamed')
        self.client.post(self.URL, {'action': 'deactivate', 'vendor': v.pk})
        self.assertFalse(Vendor.objects.get().active)
        self.assertEqual(list(self.client.get(self.URL).context['vendors']), [])
        self.assertEqual(list(self.client.get(self.URL + '&inactive=1').context['vendors']), [v])
        self.client.post(self.URL, {'action': 'reactivate', 'vendor': v.pk})
        self.assertTrue(Vendor.objects.get().active)

    def test_type_filter_and_scroll_panel(self):
        Vendor.objects.create(code='00010', name='In', kind='INTERNAL')
        Vendor.objects.create(code='00150', name='Out', kind='VENDOR')
        Vendor.objects.create(code='00151', name='Sup', kind='SUPPLIER')
        codes = lambda q: [v.code for v in self.client.get(self.URL + q).context['vendors']]
        self.assertEqual(codes('&kind=INTERNAL'), ['00010'])
        self.assertEqual(codes('&kind=EXTERNAL'), ['00150', '00151'])
        self.assertContains(self.client.get(self.URL), 'class="scroll-panel scroll-panel-first"')

    def test_inactive_code_is_not_offered_for_new_routes(self):
        v = Vendor.objects.create(code='00150', name='Closed', kind='VENDOR', active=False)
        self.assertNotIn(v.pk, [pk for pk, _l in OrderItemFormSet().empty_form.vendor_options])

    def test_import_rejects_an_inactive_code(self):
        Vendor.objects.create(code='00150', name='Closed', kind='VENDOR', active=False)
        Process.objects.create(name='Plating', kind='OUTSOURCED')
        Customer.objects.create(name='Acme')
        preview = importer.parse('customer,due_date,part_name,quantity,route\nAcme,2030-01-01,P,1,Plating:00150\n')
        self.assertIn('no longer active', ' '.join(preview.rows[0].errors))

    def test_work_sheet_still_prints_with_an_inactive_code(self):
        v = Vendor.objects.create(code='00150', name='Closed', kind='VENDOR', active=False)
        proc = Process.objects.create(name='Plating', kind='OUTSOURCED')
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        RouteStep.objects.create(item=item, sequence=1, process=proc, vendor=v)
        r = self.client.get(f'/orders/{order.job_number}/sheet/')
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
