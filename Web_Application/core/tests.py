import gettext
import re
from collections import Counter
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from django.test import TestCase as RawTestCase
from django.test import override_settings
from django.utils import translation
from django.utils import timezone

from core import accounts
from core.models import Profile
from core.templatetags.duration import duration
from core.management.commands.seed_demo import DEMO_UIDS, read_csv
from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from tracking.models import Event, Machine, ScannerDevice, Tag, TagAssignment, Worker
from tracking.overview import board_groups
from tracking.planning import process_averages
from tracking.priority import priority
from tracking.shipping import shipping_overview
from tracking.services import cancel_item, correct_event, record_event, ship_item
from tracking.status import days_late, due_back, is_stagnant, item_status

K = Process.Kind


class RoleTests(TestCase):
    """Page group -> lowest role allowed (Operator < Planner < Admin; superusers always pass)."""
    GROUPS = {
        'Operator': ['/', '/orders/', '/priorities/', '/boxes/'],
        'Planner': ['/orders/?tab=new', '/customers/', '/tags/', '/tags/history/'],
        'Admin': ['/simulate/'],
    }

    def status(self, user, url):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(url).status_code

    def test_anonymous_goes_to_login_with_next(self):
        for urls in self.GROUPS.values():
            for url in urls:
                r = self.client.get(url)
                self.assertRedirects(r, f'/login/?next={url}', fetch_redirect_response=False)

    def test_each_role_sees_its_pages_and_a_translated_403_above(self):
        order = ['Operator', 'Planner', 'Admin']
        for i, role in enumerate(order):
            user = make_user(role.lower(), role)
            for need, urls in self.GROUPS.items():
                for url in urls:
                    expected = 200 if order.index(need) <= i else 403
                    self.assertEqual(self.status(user, url), expected, (role, url))

    def test_user_without_role_gets_403_page(self):
        self.client.force_login(make_user('nobody'))
        r = self.client.get('/')
        self.assertContains(r, 'Access denied', status_code=403)

    def test_superuser_passes_everywhere(self):
        root = User.objects.create_superuser('root', password='x')
        for urls in self.GROUPS.values():
            for url in urls:
                self.assertEqual(self.status(root, url), 200, url)

    def test_nav_shows_only_usable_links(self):
        self.client.force_login(make_user('op', 'Operator'))
        html = self.client.get('/').content.decode()
        self.assertIn('href="/orders/"', html)
        for hidden in ('/orders/?tab=new', '/customers/', '/tags/', '/simulate/'):
            self.assertNotIn(f'href="{hidden}"', html)
        self.client.force_login(make_user('pl', 'Planner'))
        html = self.client.get('/').content.decode()
        self.assertIn('href="/tags/"', html)
        self.assertNotIn('href="/simulate/"', html)

    def test_login_and_logout(self):
        make_user('op', 'Operator')
        r = self.client.post('/login/?next=/orders/', {'username': 'op', 'password': 'x', 'next': '/orders/'})
        self.assertRedirects(r, '/orders/')
        self.assertRedirects(self.client.post('/logout/'), '/login/')
        self.assertContains(self.client.get('/login/'), 'Log in')

    def test_planner_opens_dashboard_but_not_simulate(self):
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/simulate/').status_code, 403)

    def test_operator_gets_403_on_dashboard(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/dashboard/').status_code, 403)

    def test_demo_admin_cannot_open_django_admin(self):
        user = make_user('demo_admin', 'Admin')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/admin/').status_code, 302)  # not staff: sent to the admin login

    def test_login_page_is_translated(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ja'
        self.assertContains(self.client.get('/login/'), 'ログイン')


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
            tag=Tag.objects.create(uid=uid), item=item,
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
        self.client.force_login(make_user('s', 'Admin'))
        return self.client.get('/')

    def groups(self, group):
        in_house, outsourced = board_groups()
        return in_house if group == 'in_house' else outsourced

    def card(self, r, group, name):
        return next(c for c in self.groups(group) if c['process'].name == name)

    def test_home_needs_login(self):
        self.assertRedirects(self.client.get('/'), '/login/?next=/')
        self.assertContains(self.home(), 'Progress matrix')

    def test_home_shows_the_matrix_and_not_the_old_board(self):
        item, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.material, 'ARRIVED')
        r = self.home()
        self.assertContains(r, 'class="matrix"')
        self.assertContains(r, item.item_code)
        self.assertNotContains(r, 'href="/matrix/"')
        for gone in ('Process board', 'process-card', 'In-house now', 'At vendors'):
            self.assertNotContains(r, gone)
        self.assertEqual(self.client.get('/board/').status_code, 404)

    def test_arrived_with_pending_next_step_is_waiting_in_house(self):
        _, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.material, 'ARRIVED')
        r = self.home()
        milling = self.card(r, 'in_house', 'Milling')
        self.assertEqual(([e['code'] for e in milling['waiting']], milling['running']), (['WAITING'], []))
        self.assertEqual(r.context['counters']['waiting'], 1)
        self.assertEqual(self.card(r, 'outsourced', 'Plating')['running'], [])

    def test_sent_out_shows_at_vendor_with_vendor_name(self):
        _, uid = self.make_item([self.plating])
        record_event(self.device, uid, self.plating, 'SENT_OUT')
        r = self.home()
        self.assertEqual(self.card(r, 'in_house', 'Milling')['waiting'], [])
        self.assertEqual(self.card(r, 'outsourced', 'Plating')['running'][0]['vendor'], 'Acme Plating')
        self.assertContains(r, 'Acme Plating')

    def test_completed_orders_do_not_appear(self):
        _, uid = self.make_item([self.material])
        record_event(self.device, uid, self.material, 'ARRIVED')
        r = self.home()
        self.assertEqual([(len(c['running']), len(c['waiting'])) for c in self.groups('in_house') + self.groups('outsourced')], [(0, 0), (0, 0)])
        self.assertEqual(sum(r.context['counters'][k] for k in ('in_progress', 'waiting', 'at_vendor', 'not_started')), 0)

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
        self.assertEqual([(e['item'], e['code']) for e in self.card(r, 'in_house', 'Milling')['running']], [(item, 'IN_PROGRESS')])
        self.assertContains(r, item.item_code)

    def test_active_step_with_earlier_step_unscanned_is_in_progress(self):
        item, uid = self.make_item([self.material, self.milling])
        record_event(self.device, uid, self.milling, 'STARTED')
        r = self.home()
        self.assertEqual(r.context['counters']['in_progress'], 1)
        self.assertEqual(self.card(r, 'in_house', 'Milling')['running'][0]['code'], 'IN_PROGRESS')

    def test_sent_out_past_lead_time_is_late_and_counted(self):
        item = self.send_out(10, 7)
        r = self.home()
        e = self.groups('outsourced')[-1]['running'][0]
        self.assertEqual((e['late_days'], r.context['counters']['vendor_overdue']), (3, 1))
        self.assertEqual(e['due_back'], timezone.localdate() - timedelta(days=3))
        detail = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(detail, 'Late by 3 days')
        self.assertContains(detail, 'due back')

    def test_sent_out_today_is_not_late(self):
        self.send_out(0, 7)
        r = self.home()
        e = self.groups('outsourced')[-1]['running'][0]
        self.assertEqual((e['late_days'], r.context['counters']['vendor_overdue']), (0, 0))
        self.assertEqual(e['due_back'], timezone.localdate() + timedelta(days=7))

    def test_step_due_date_beats_lead_days(self):
        item = self.send_out(3, 7)
        due = timezone.localdate() - timedelta(days=2)
        item.steps.update(due_date=due)
        e = self.groups('outsourced')[-1]['running'][0]
        self.assertEqual((e['due_back'], e['late_days']), (due, 2))

    def test_no_lead_days_is_never_late(self):
        self.send_out(60, None)
        r = self.home()
        e = self.groups('outsourced')[-1]['running'][0]
        self.assertEqual((e['due_back'], e['late_days'], r.context['counters']['vendor_overdue']), (None, 0, 0))

    def test_stagnant_counter(self):
        now = timezone.now()
        fresh, fresh_uid = self.make_item([self.material, self.milling], due_days=1, since=now - timedelta(days=9))
        old, old_uid = self.make_item([self.material, self.milling], due_days=20, since=now - timedelta(days=9))
        older, older_uid = self.make_item([self.material, self.milling], due_days=30, since=now - timedelta(days=9))
        for uid, ago in ((fresh_uid, timedelta(hours=1)), (old_uid, timedelta(days=3)), (older_uid, timedelta(days=5))):
            record_event(self.device, uid, self.material, 'ARRIVED', occurred_at=now - ago)
        r = self.home()
        self.assertEqual(r.context['counters']['stagnant'], 2)
        self.assertEqual([e['item'] for e in self.card(r, 'in_house', 'Milling')['waiting']], [older, old, fresh])  # longest idle first

    def test_split_item_has_one_row_per_box_and_counters_count_boxes(self):
        item, uid1 = self.make_item([self.material, self.milling])
        item.box_count = 3
        item.save()
        now = timezone.now()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2X'), item=item,
                                     start_at=now - timedelta(days=3))
        record_event(self.device, uid1, self.material, 'ARRIVED')
        record_event(self.device, uid1, self.milling, 'STARTED')
        record_event(self.device, 'T2X', self.material, 'ARRIVED', occurred_at=now - timedelta(days=2))
        r = self.home()
        milling = self.card(r, 'in_house', 'Milling')
        rows = milling['waiting'] + milling['running']
        self.assertEqual([(e['tag_uid'], e['code'], e['stagnant'], e['caption']) for e in rows],
                         [('T2X', 'WAITING', True, 'Box 1 of 3'), (uid1, 'IN_PROGRESS', False, 'Box 2 of 3')])
        c = r.context['counters']
        self.assertEqual((c['in_progress'], c['waiting'], c['not_started'], c['stagnant'], c['overdue']), (1, 1, 1, 1, 0))

    def test_vendor_lateness_is_per_box(self):
        proc = Process.objects.create(name='Paint', kind=K.OUTSOURCED, vendor_name='V', vendor_lead_days=2)
        item, uid1 = self.make_item([proc])
        item.box_count = 2
        item.save()
        now = timezone.now()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2Y'), item=item,
                                     start_at=now - timedelta(days=9))
        record_event(self.device, uid1, proc, 'SENT_OUT')
        record_event(self.device, 'T2Y', proc, 'SENT_OUT', occurred_at=now - timedelta(days=6))
        rows = [c for c in self.groups('outsourced') if c['process'] == proc][0]['running']
        self.assertEqual([(e['tag_uid'], e['late_days'] > 0) for e in rows], [('T2Y', True), (uid1, False)])


class SeedDemoTests(TestCase):
    def seed(self, *args):
        call_command('seed_demo', *args, stdout=StringIO())

    def status(self, part):
        item = OrderItem.objects.get(part_name=part)
        return item, item_status(item)

    @override_settings(DEBUG=True)
    def test_demo_users_log_in_with_demo_password_and_rerun(self):
        with mock.patch.dict('os.environ', {'DEMO_PASSWORD': 'pw-1'}):
            self.seed()
            self.seed()
        admin = User.objects.get(username='demo_admin')
        self.assertTrue(admin.check_password('pw-1'))
        self.assertFalse(admin.is_staff or admin.is_superuser)
        self.assertTrue(User.objects.get(username='demo_op_sato').check_password('pw-1'))
        self.assertTrue(User.objects.get(username='demo_op_sato').profile)
        self.assertEqual(list(admin.groups.values_list('name', flat=True)), ['Admin'])
        with mock.patch.dict('os.environ', clear=False) as env:
            env.pop('DEMO_PASSWORD', None)
            self.seed()
        self.assertFalse(User.objects.get(username='demo_planner').has_usable_password())

    def test_refuses_without_debug(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(Customer.objects.exists())

    @override_settings(DEBUG=True)
    def test_runs_twice_without_duplicating_and_reset_recreates(self):
        self.seed()
        n = (Order.objects.count(), Event.objects.count())
        self.seed()
        self.assertEqual((Order.objects.count(), Event.objects.count()), n)
        self.seed('--reset')
        self.assertEqual((Order.objects.count(), Event.objects.count()), n)

    @override_settings(DEBUG=True)
    def test_scenarios_match_statuses(self):
        self.seed()
        item, st = self.status('フランジ')
        self.assertEqual((st.code, priority(item.order, st)['tier']), ('IN_PROGRESS', 'OVERDUE'))
        item, st = self.status('カバー')
        self.assertEqual(st.code, 'AT_VENDOR')
        self.assertGreater(days_late(due_back(st.rows[1].started_at, 5)), 0)
        self.assertTrue(is_stagnant(self.status('シャフト')[1]))
        self.assertEqual(self.status('プレート')[1].code, 'COMPLETE')
        self.assertEqual(self.status('ピン')[1].code, 'NOT_STARTED')
        item, st = self.status('ハウジング')
        self.assertEqual(priority(item.order, st)['tier'], 'CRITICAL')
        self.assertTrue(self.status('ギア')[1].warnings)
        self.assertEqual(self.status('新部品')[1].code, 'IN_PROGRESS')
        self.assertEqual(self.status('旧部品')[1].code, 'SHIPPED')

    @override_settings(DEBUG=True)
    def test_part_numbers_repeat_on_several_orders(self):
        self.seed()
        for part_number in ('BRK-100', 'FLG-200'):
            self.assertGreaterEqual(OrderItem.objects.filter(part_number=part_number).values('order').distinct().count(), 3)

    @override_settings(DEBUG=True)
    def test_split_item_rolls_up_three_boxes(self):
        self.seed()
        item, st = self.status('フレーム')
        self.assertEqual((item.box_count, st.code), (3, 'IN_PROGRESS'))
        self.assertEqual([b.code for b in st.boxes], ['COMPLETE', 'IN_PROGRESS', 'NOT_STARTED'])
        self.assertIsNone(st.boxes[2].assignment)
        self.assertEqual(st.warnings, [])
        self.seed('--reset')
        self.assertEqual(OrderItem.objects.filter(part_name='フレーム').count(), 1)
        self.assertEqual(TagAssignment.objects.filter(item__part_name='フレーム').count(), 2)

    @override_settings(DEBUG=True)
    def test_every_event_uses_the_right_process_kind(self):
        self.seed()
        events = Event.objects.filter(event_type__in=Event.REQUIRED_KIND).select_related('process')
        self.assertTrue(events)
        for e in events:
            self.assertEqual(e.process.kind, Event.REQUIRED_KIND[e.event_type], e)
        self.assertTrue(Event.objects.filter(event_type='CORRECTION').exists())
        names = {r['name'] for r in read_csv('processes.csv')}
        self.assertEqual(set(Process.objects.values_list('name', flat=True)), names)

    @override_settings(DEBUG=True)
    def test_seed_uses_csv_codes_and_new_fields(self):
        self.seed()
        codes = {r['code']: r['kind'] for r in read_csv('codes.csv')}
        self.assertEqual({v.code: v.kind for v in Vendor.objects.all()}, codes)
        for step in RouteStep.objects.select_related('vendor', 'process'):
            self.assertIn(step.vendor.code, codes)
            self.assertEqual(step.vendor.kind, {'MATERIAL': 'SUPPLIER', 'MACHINING': 'INTERNAL', 'OUTSOURCED': 'VENDOR'}[step.process.kind])
        for v in Vendor.objects.all():
            v.full_clean()
        self.assertFalse(OrderItem.objects.filter(material='').exists())
        self.assertEqual(RouteStep.objects.get(slip_number='SL-0412').process.name, 'Coating')

    @override_settings(DEBUG=True)
    def test_seed_records_results_with_workers_and_machines(self):
        self.seed()
        results = Event.objects.filter(event_type='RESULT', tag_uid__in=DEMO_UIDS)  # the scenario orders; history has more
        self.assertEqual(results.count(), 15)
        self.assertEqual(set(results.values_list('quantity', flat=True)), {10})
        self.assertEqual(results.filter(defects__gt=0).count(), 2)
        self.assertGreater(results.exclude(worker=None).values('worker').distinct().count(), 2)
        self.assertEqual(set(Worker.objects.values_list('name', flat=True)), {r['name'] for r in read_csv('workers_machines.csv') if r['kind'] == 'WORKER'})
        self.assertEqual(Machine.objects.count(), len([r for r in read_csv('workers_machines.csv') if r['kind'] == 'MACHINE']))

    @override_settings(DEBUG=True)
    def test_seed_has_paused_partial_and_cancelled_examples(self):
        self.seed()
        self.assertEqual(self.status('ベース')[1].code, 'PAUSED')
        self.assertEqual(self.status('リング')[1].code, 'CANCELLED')
        self.assertTrue(any(r.partial for r in self.status('ブラケット')[1].rows))
        self.seed('--reset')
        self.assertEqual(Event.objects.filter(event_type='CANCELLED', item__part_name='リング').count(), 1)

    @override_settings(DEBUG=True)
    def test_seed_gives_the_plan_vs_actual_page_some_rows(self):
        self.seed()
        self.assertTrue(process_averages())

    @override_settings(DEBUG=True)
    def test_history_covers_every_state_and_a_year_of_shipping(self):
        self.seed()
        codes = Counter(item_status(i).code for i in OrderItem.objects.all())
        for code in ('NOT_STARTED', 'WAITING', 'IN_PROGRESS', 'AT_VENDOR', 'PAUSED', 'COMPLETE', 'SHIPPED', 'CANCELLED'):
            self.assertGreaterEqual(codes[code], 3, code)
        self.assertGreaterEqual(Order.objects.count(), 60)
        ready, months = shipping_overview()
        rc = ready[0]['order'].customer
        self.assertGreaterEqual(len(months), 11)
        self.assertTrue(0 < min(m['rate'] for m in months) < max(m['rate'] for m in months) <= 100)
        self.assertTrue(Event.objects.filter(event_type='RESULT', defects__gt=0).exists())
        self.assertTrue(ScannerDevice.objects.filter(name='DEMO Scanner 3', last_seen__isnull=True).exists())

    @override_settings(DEBUG=True)
    def test_history_makes_slow_steps_visible_and_is_repeatable(self):
        self.seed()
        diff = {r['process'].name: r['diff'] for r in process_averages()}
        for slow in ('Plating', 'Grinding', 'Inspection'):
            self.assertGreater(diff[slow], 0.5, slow)
        self.assertLess(diff['Lathe'], diff['Grinding'])
        first = list(OrderItem.objects.order_by('order__job_number', 'item_no').values_list('part_name', 'quantity'))
        self.seed('--reset')
        self.assertEqual(first, list(OrderItem.objects.order_by('order__job_number', 'item_no').values_list('part_name', 'quantity')))

    @override_settings(DEBUG=True)
    def test_home_matrix_shows_every_cell_state_and_a_long_route(self):
        self.seed()
        self.client.force_login(make_user('op', 'Operator'))
        data = self.client.get('/', {'n': 500}).context['matrix']
        states = {c['state'] for r in data['rows'] for c in r['cells']}
        self.assertEqual(states, {'pending', 'in_progress', 'at_vendor', 'paused', 'done'})
        self.assertGreaterEqual(len(data['columns']), 6)

    @override_settings(DEBUG=True)
    def test_every_analysis_and_shipping_filter_and_highlight_has_data(self):
        self.seed()
        self.client.force_login(make_user('pl', 'Planner'))
        get = lambda url, **q: self.client.get(url, q).context
        plan = get('/analysis/', tab='plan', sort='delay')
        self.assertGreaterEqual({r['severity'] for r in plan['rows']} - {''}, {'minor', 'major', 'severe'})
        pcts = [r['pct'] for r in plan['rows'] if r['pct'] is not None]
        self.assertEqual(pcts, sorted(pcts, reverse=True))
        self.assertEqual(len(plan['top']), 3)
        res = get('/analysis/', tab='results')
        self.assertTrue(res['longest_wait'] and res['longest_processing'])
        self.assertTrue(any(r['records'] and r['processing'] and r['wait'] for r in res['rows']))
        everything = len(plan['rows'])
        customer, vendor = Customer.objects.filter(name__startswith='DEMO ').first(), Vendor.objects.get(code='00131')
        process = Process.objects.get(name__in=['Plating', 'DEMO Plating'])
        for key, value in (('customer', customer.pk), ('vendor', vendor.pk), ('process', process.pk), ('date_from', timezone.localdate() - timedelta(days=120))):
            rows = get('/analysis/', tab='plan', **{key: value})['rows']
            self.assertTrue(0 < len(rows) <= everything, key)
            self.assertTrue(get('/analysis/', tab='results', **{key: value})['rows'], key)
        ready, months = shipping_overview()
        rc = ready[0]['order'].customer
        self.assertGreaterEqual(len(ready), 3)
        self.assertTrue(shipping_overview(ready_customer=rc.pk)[0])
        self.assertLess(len(shipping_overview(ready_customer=rc.pk)[0]), len(ready))
        self.assertTrue(shipping_overview(ready_q=ready[0]['item'].part_name)[0])
        due = ready[-1]['order'].due_date
        self.assertTrue(shipping_overview(due_from=due, due_to=due)[0])
        self.assertTrue(any(shipping_overview(month_customer=c.pk)[1] for c in Customer.objects.filter(name__startswith='DEMO ')))
        self.assertEqual(len(shipping_overview(month_from=months[3]['month'], month_to=months[1]['month'])[1]), 3)

    @override_settings(DEBUG=True)
    def test_reset_removes_demo_data_and_keeps_real_data(self):
        real_proc = Process.objects.create(name='Cutting', kind=K.MATERIAL)
        real_cust = Customer.objects.create(name='Real Co')
        real_order = Order.objects.create(customer=real_cust, due_date=timezone.localdate())
        real_dev = ScannerDevice.objects.create(name='Real scanner')
        Tag.objects.create(uid='REAL1')
        real_event = Event.objects.create(device=real_dev, tag_uid='REAL1', event_type='ARRIVED', process=real_proc)
        self.seed()
        n = (Order.objects.count(), Event.objects.count(), Process.objects.count(), TagAssignment.objects.count())
        self.seed('--reset')
        self.assertEqual((Order.objects.count(), Event.objects.count(), Process.objects.count(), TagAssignment.objects.count()), n)
        self.assertTrue(Event.objects.filter(pk=real_event.pk).exists())
        self.assertTrue(Order.objects.filter(pk=real_order.pk).exists())
        self.assertTrue(Tag.objects.filter(uid='REAL1').exists())
        self.assertTrue(Process.objects.filter(pk=real_proc.pk, kind=K.MATERIAL).exists())

    @override_settings(DEBUG=True)
    def test_failed_seed_leaves_nothing_behind(self):
        with mock.patch('core.management.commands.seed_demo.seed_bulk', side_effect=RuntimeError), self.assertRaises(RuntimeError):
            self.seed()
        self.assertEqual((Customer.objects.count(), Order.objects.count(), Event.objects.count()), (0, 0, 0))

    @override_settings(DEBUG=True)
    def test_real_process_of_another_kind_is_left_alone(self):
        real = Process.objects.create(name='Milling', kind=K.MATERIAL)
        self.seed()
        self.assertEqual(Process.objects.get(pk=real.pk).kind, K.MATERIAL)
        self.assertTrue(Process.objects.filter(name='DEMO Milling', kind=K.MACHINING).exists())

    @override_settings(DEBUG=True)
    def test_reset_keeps_a_demo_process_that_a_real_scan_uses(self):
        old = Process.objects.create(name='DEMO Old', kind=K.MATERIAL)
        Event.objects.create(device=ScannerDevice.objects.create(name='Real scanner'), tag_uid='REAL1', event_type='ARRIVED', process=old)
        self.seed('--reset')
        self.assertTrue(Process.objects.filter(pk=old.pk).exists())
        self.assertTrue(Event.objects.filter(tag_uid='REAL1').exists())


class ResponsiveLayoutTests(TestCase):
    CSS = settings.BASE_DIR / 'static' / 'css'

    def css(self, name):
        return (self.CSS / name).read_text(encoding='utf-8')

    def test_pages_have_the_viewport_tag(self):
        self.assertContains(self.client.get('/login/'), '<meta name="viewport" content="width=device-width, initial-scale=1">')

    def test_breakpoints_are_defined_only_in_theme_css(self):
        for name in ('base.css', 'lang.css'):
            self.assertNotIn('@media', self.css(name), name)
        widths = re.findall(r'@media \(max-width: (\d+)px\)', self.css('theme.css'))
        self.assertEqual(sorted(set(widths)), ['480', '768'])

    def test_containers_have_no_fixed_pixel_widths(self):  # hairlines such as 2px are fine
        for name in ('base.css', 'theme.css', 'lang.css'):
            self.assertEqual(re.findall(r'(?<![-\w])(?:width|min-width)\s*:\s*\d{3,}px', self.css(name)), [], name)

    def test_listed_tables_turn_into_cards_and_the_two_wide_ones_stay_tables(self):
        self.client.force_login(make_user('pl', 'Planner'))
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='CARD1'), item=item)
        Vendor.objects.create(code='00001', name='V', kind=Vendor.Kind.INTERNAL)
        pages = {'/orders/': 1, '/orders/?tab=codes': 1, '/analysis/?tab=results': 4, '/shipping/': 2, '/tags/?q=CARD1': 1}
        for url, tables in pages.items():
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('class="table table-cards"'), tables, url)
            unlabelled = re.findall(r'<td(?![^>]*(?:data-label|colspan))[^>]*>', html)
            self.assertEqual(len(unlabelled), 1 if 'tags' in url else 0, url)  # only the tag actions cell has no label
        for url in ('/', f'/orders/{order.job_number}/'):
            self.assertNotIn('table-cards', self.client.get(url).content.decode(), url)
        theme = self.css('theme.css')
        self.assertIn('.table-cards [data-label]::before { content: attr(data-label)', theme)
        self.assertNotIn('table-cards', self.css('base.css'))

    def test_menu_button_is_labelled_controls_the_nav_and_keeps_role_limited_links(self):
        self.client.force_login(make_user('op', 'Operator'))
        html = self.client.get('/orders/').content.decode()
        self.assertIn('class="nav-toggle" aria-controls="site-nav" aria-expanded="false" aria-label="Menu"', html)
        self.assertIn('<nav class="site-nav" id="site-nav">', html)
        self.assertIn('js/nav.js', html)
        self.assertIn('href="/orders/"', html)
        self.assertNotIn('href="/customers/"', html)  # an Operator still only sees the links the role can use
        self.client.force_login(make_user('pl', 'Planner'))
        self.assertIn('href="/customers/"', self.client.get('/orders/').content.decode())

    def test_menu_collapses_only_below_768px_and_only_when_javascript_runs(self):
        theme, base = self.css('theme.css'), self.css('base.css')
        block = re.search(r'@media \(max-width: 768px\) \{.*?\n\}', theme, re.S).group(0)
        for rule in ('.site-header .nav-toggle { display: inline-block; }', '.js .site-nav { display: none; }', '.js .site-nav.is-open {'):
            self.assertIn(rule, block)
        self.assertNotIn('.site-nav { display: none', base)  # without JavaScript the links stay visible
        js = (settings.BASE_DIR / 'static' / 'js' / 'nav.js').read_text(encoding='utf-8')
        for part in ('aria-expanded', "e.key === 'Escape'", 'button.focus()'):
            self.assertIn(part, js)


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _mix(colour, background, percent):
    """sRGB mix like CSS color-mix: percent of colour over the background."""
    mixed = [round(int(colour[i:i + 2], 16) * percent / 100 + int(background[i:i + 2], 16) * (100 - percent) / 100) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(mixed)


class StatusColourTests(SimpleTestCase):
    STATES = ('not-started', 'waiting', 'in-progress', 'at-vendor', 'complete', 'shipped', 'paused', 'cancelled', 'overdue')
    CSS = settings.BASE_DIR / 'static' / 'css'
    TEMPLATES = settings.BASE_DIR / 'templates'

    def theme(self):
        return (self.CSS / 'theme.css').read_text(encoding='utf-8')

    def variables(self):
        return dict(re.findall(r'--([a-z-]+):\s*(#[0-9a-fA-F]{6})\b', self.theme()))

    def test_no_hex_or_functional_colours_outside_theme_css(self):
        files = [p for p in self.CSS.glob('*.css') if p.name != 'theme.css'] + list(self.TEMPLATES.rglob('*.html'))
        pattern = re.compile(r'(?<![&\w-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(')
        for path in files:
            self.assertEqual(pattern.findall(path.read_text(encoding='utf-8')), [], path.name)

    def test_every_state_has_one_variable_and_a_badge_and_a_text_label(self):
        variables = self.variables()
        base = (self.CSS / 'base.css').read_text(encoding='utf-8')
        for state in self.STATES:
            self.assertIn(f'status-{state}', variables, state)
            self.assertIn(f'.badge-{state.replace("-", "_")} {{ color: var(--status-{state})', base, state)
        from tracking.status import LABELS
        for code in ('NOT_STARTED', 'WAITING', 'IN_PROGRESS', 'AT_VENDOR', 'COMPLETE', 'SHIPPED', 'PAUSED', 'CANCELLED'):
            self.assertTrue(str(LABELS[code]), code)

    def test_text_on_every_state_colour_has_4_5_contrast(self):
        v = self.variables()
        white, text, muted = v['color-surface'], v['color-text'], v['color-muted']
        for name in [f'status-{s}' for s in self.STATES] + ['status-partial'] + [f'risk-{r}' for r in ('overdue', 'critical', 'watch', 'on-track')]:
            self.assertGreaterEqual(_contrast(v[name], white), 4.5, f'{name} on white')  # badge text, or white text on the colour
        for state in ('not-started', 'in-progress', 'at-vendor', 'complete', 'paused'):  # matrix cell backgrounds
            tint = _mix(v[f'status-{state}'], white, 16)
            self.assertGreaterEqual(_contrast(text, tint), 4.5, f'text on {state} tint')
            self.assertGreaterEqual(_contrast(muted, tint), 4.5, f'muted text on {state} tint')
        self.assertGreaterEqual(_contrast(muted, v['color-bg']), 4.5)

    def test_states_differ_in_lightness_not_only_in_hue(self):
        v = self.variables()
        levels = sorted(_luminance(v[f'status-{s}']) for s in self.STATES)
        gaps = [b - a for a, b in zip(levels, levels[1:])]
        self.assertGreaterEqual(min(gaps), 0.01, gaps)

    def test_matrix_and_legend_use_the_state_variables(self):
        base = (self.CSS / 'base.css').read_text(encoding='utf-8')
        self.assertRegex(base, r'\.matrix-done \{ background: var\(--status-complete-tint\)')
        for state, name in (('in_progress', 'in-progress'), ('at_vendor', 'at-vendor'), ('paused', 'paused')):
            self.assertIn(f'.matrix-{state} {{ background: var(--status-{name}); }}', base, state)
        self.assertIn('.matrix-pending { background: transparent;', base)
        self.assertNotIn('--matrix-', self.theme())


class DashboardTests(TestCase):
    def setUp(self):
        self.admin = make_user('boss', 'Admin')
        self.client.force_login(self.admin)
        self.now = timezone.now()

    def item(self, route=None):
        order = Order.objects.create(customer=Customer.objects.get_or_create(name='C')[0], due_date=timezone.localdate() + timedelta(days=9))
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        for i, p in enumerate(route or [Process.objects.get_or_create(name='Mat', kind=K.MATERIAL)[0]], 1):
            RouteStep.objects.create(item=item, sequence=i, process=p)
        n = OrderItem.objects.count()
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid=f'DB{n}'), item=item,
                                         start_at=self.now - timedelta(days=30))
        return item, a

    def test_only_planners_see_the_page_and_the_nav_link(self):
        for role in ('Planner', 'Admin'):
            self.client.force_login(make_user(role.lower(), role))
            self.assertEqual(self.client.get('/dashboard/').status_code, 200, role)
            self.assertContains(self.client.get('/'), 'href="/dashboard/"')
        self.client.force_login(make_user('operator', 'Operator'))
        self.assertEqual(self.client.get('/dashboard/').status_code, 403)
        self.assertNotContains(self.client.get('/'), 'href="/dashboard/"')
        self.client.force_login(User.objects.create_superuser('root', password='x'))
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)

    def test_users_per_role(self):
        make_user('op1', 'Operator')
        make_user('op2', 'Operator')
        make_user('pl', 'Planner')
        make_user('nobody')
        gone = make_user('gone', 'Planner')
        gone.is_active = False
        gone.save()
        User.objects.create_superuser('root', password='x')
        roles = self.client.get('/dashboard/').context['roles']
        self.assertEqual(roles, [('Operator', 2), ('Planner', 1), ('Admin', 2), (None, 1)])

    def test_items_per_status(self):
        self.item()  # not started
        done, a = self.item()
        record_event(ScannerDevice.objects.create(name='D'), a.tag.uid, done.steps.get().process, 'ARRIVED')
        shipped, b = self.item()
        record_event(ScannerDevice.objects.get(name='D'), b.tag.uid, shipped.steps.get().process, 'ARRIVED')
        ship_item(self.admin, shipped)
        gone, _c = self.item()
        cancel_item(self.admin, gone, 'withdrawn')
        r = self.client.get('/dashboard/')
        counts = {code: n for code, _label, n in r.context['statuses']}
        self.assertEqual(counts, {'NOT_STARTED': 1, 'WAITING': 0, 'IN_PROGRESS': 0, 'AT_VENDOR': 0, 'PAUSED': 0, 'COMPLETE': 1, 'SHIPPED': 1, 'CANCELLED': 1})
        self.assertContains(r, 'Shipped: 1')

    def test_events_today_counts_only_events_recorded_today(self):
        item, a = self.item()
        device = ScannerDevice.objects.create(name='D')
        old = record_event(device, a.tag.uid, item.steps.get().process, 'ARRIVED')[0]
        Event.objects.filter(pk=old.pk).update(received_at=self.now - timedelta(days=2))
        self.assertEqual(self.client.get('/dashboard/').context['events_today'], 0)
        for _ in range(3):
            record_event(device, a.tag.uid, item.steps.get().process, 'ARRIVED')
        self.assertEqual(self.client.get('/dashboard/').context['events_today'], 3)

    def test_scanners_not_seen_in_24_hours_are_listed_by_name(self):
        ScannerDevice.objects.create(name='Fresh', last_seen=self.now - timedelta(hours=2))
        ScannerDevice.objects.create(name='Stale', last_seen=self.now - timedelta(hours=30))
        ScannerDevice.objects.create(name='Never')
        ScannerDevice.objects.create(name='Retired', active=False, last_seen=self.now - timedelta(days=90))
        r = self.client.get('/dashboard/')
        self.assertEqual([d.name for d in r.context['silent_devices']], ['Never', 'Stale'])
        self.assertContains(r, 'Stale')
        self.assertNotContains(r, 'Fresh')
        ScannerDevice.objects.all().update(last_seen=self.now)
        self.assertContains(self.client.get('/dashboard/'), 'All scanners have reported.')

    def test_voided_events_in_the_last_seven_days(self):
        item, a = self.item()
        device = ScannerDevice.objects.create(name='D')
        events = [record_event(device, a.tag.uid, item.steps.get().process, 'ARRIVED')[0] for _ in range(3)]
        planner = make_user('pl', 'Planner')
        corrections = [correct_event(e, planner, 'mistake')[0] for e in events]
        Event.objects.filter(pk=corrections[0].pk).update(occurred_at=self.now - timedelta(days=10))
        self.assertEqual(self.client.get('/dashboard/').context['voided'], 2)

    def test_each_block_links_to_its_admin_page(self):
        html = self.client.get('/dashboard/').content.decode()
        for url in ('/admin/auth/user/', '/admin/orders/orderitem/', '/admin/tracking/event/', '/admin/tracking/scannerdevice/',
                    '/admin/tracking/event/?event_type__exact=CORRECTION'):
            self.assertIn(f'href="{url}"', html)


class LanguageTests(RawTestCase):
    """Japanese by default, English one click away, choice remembered by cookie."""

    def staff_data(self):
        self.client.force_login(make_user('s', 'Admin'))
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        RouteStep.objects.create(item=item, sequence=1, process=Process.objects.create(name='Cut', kind=K.MACHINING))
        return order

    def test_first_visit_is_japanese_even_with_english_browser(self):
        r = self.client.get('/login/', HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertContains(r, 'ログイン')
        self.assertContains(r, '<html lang="ja">')
        self.assertContains(r, 'class="lang-switch"')
        self.assertContains(r, 'value="ja" class="active" aria-current="true"')

    def test_switch_to_english_persists_in_cookie(self):
        r = self.client.post('/i18n/setlang/', {'language': 'en', 'next': '/'})
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        cookie = self.client.cookies[settings.LANGUAGE_COOKIE_NAME]
        self.assertEqual((cookie.value, cookie['max-age']), ('en', 60 * 60 * 24 * 365))
        r = self.client.get('/login/', HTTP_ACCEPT_LANGUAGE='ja')
        self.assertContains(r, 'Log in')
        self.assertContains(r, 'value="en" class="active" aria-current="true"')
        self.client.post('/i18n/setlang/', {'language': 'ja', 'next': '/'})
        self.assertContains(self.client.get('/login/'), 'ログイン')

    def test_main_pages_work_in_both_languages(self):
        order = self.staff_data()
        urls = ['/', '/orders/', '/orders/?tab=new', f'/orders/{order.job_number}/', '/tags/', '/simulate/', '/priorities/', '/boxes/']
        for lang in ('ja', 'en'):
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200, (lang, url))

    def test_japanese_pages_are_translated(self):
        order = self.staff_data()
        r = self.client.get(f'/orders/{order.job_number}/')
        self.assertContains(r, '受注一覧に戻る')
        self.assertContains(self.client.get('/'), '進捗マトリクス')
        self.assertContains(self.client.get('/tags/'), 'タグを紐付ける')

    def test_admin_login_has_the_switch(self):
        self.assertContains(self.client.get('/admin/login/'), 'class="lang-switch"')

    def test_process_name_follows_language(self):
        both = Process(name='Cutting', name_ja='切断', kind=K.MACHINING)
        only_en = Process(name='Milling', kind=K.MACHINING)
        with translation.override('ja'):
            self.assertEqual((str(both), str(only_en)), ('切断', 'Milling'))
        with translation.override('en'):
            self.assertEqual((str(both), str(only_en)), ('Cutting', 'Milling'))

    def test_duration_filter_is_translated(self):
        td = timedelta(days=2, hours=3)
        with translation.override('en'):
            self.assertEqual(duration(td), '2d 3h')
        with translation.override('ja'):
            self.assertEqual((duration(td), duration(timedelta(seconds=5))), ('2日3時間', '1分未満'))

    def test_catalog_is_complete(self):
        base = settings.BASE_DIR / 'locale' / 'ja' / 'LC_MESSAGES'
        po = (base / 'django.po').read_text(encoding='utf-8')
        entries = [b for b in po.split('\n\n')[1:] if 'msgid "' in b]
        self.assertGreater(len(entries), 200)
        self.assertNotIn('#, fuzzy', po)
        for b in entries:
            self.assertNotRegex(b, r'(?m)^msgstr(\[0\])? ""\n(?!")|^msgstr(\[0\])? ""\Z', b)
        with open(base / 'django.mo', 'rb') as f:
            catalog = gettext.GNUTranslations(f)._catalog
        self.assertFalse([k for k, v in catalog.items() if not v and k != ''])
        self.assertEqual(catalog['Home'], 'ホーム')
        self.assertGreaterEqual(len(catalog), len(entries))


class ScrollPanelTests(SimpleTestCase):
    def render(self, tag):
        from django.template import Context, Template
        return Template('{% load panels %}' + tag).render(Context())

    def test_panel_wraps_content_and_options(self):
        html = self.render('{% scroll_panel max_height="40vh" first_col=True label="Rows" %}<table></table>{% endscroll_panel %}')
        self.assertIn('class="scroll-panel scroll-panel-first"', html)
        self.assertIn('--panel-max: 40vh', html)
        self.assertIn('aria-label="Rows"', html)
        self.assertIn('<table></table>', html)

    def test_default_panel_has_no_inline_style(self):
        self.assertNotIn('style=', self.render('{% scroll_panel %}x{% endscroll_panel %}'))

    def test_every_table_in_a_template_sits_in_a_scroll_panel(self):
        for path in (settings.BASE_DIR / 'templates').rglob('*.html'):
            text = path.read_text(encoding='utf-8')
            self.assertLessEqual(text.count('<table'), text.count('{% scroll_panel'), path.name)


class AnalysisShippingPageTests(TestCase):
    def test_pages_render_without_demo_data(self):
        self.client.force_login(make_user('pl', 'Planner'))
        for url in ('/analysis/', '/analysis/?tab=plan', '/analysis/?tab=results', '/shipping/'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotContains(r, 'demo-facts')


class ManualTests(TestCase):
    def test_manual_is_japanese_and_uses_the_screen_terms(self):
        text = (settings.BASE_DIR / 'docs' / 'manual_ja.md').read_text(encoding='utf-8')
        self.assertFalse((settings.BASE_DIR / 'docs' / 'manual_en.md').exists())
        for section in ('このシステムについて', '言語を切り替える', 'ホーム', '受注一覧', '優先度', '出荷', '分析', '用語集', 'よくある質問', 'スクリーンショット'):
            self.assertIn(section, text)
        with translation.override('ja'):
            for term in ('Orders', 'New order', 'Import', 'Codes', 'Priorities', 'Shipping', 'Analysis', 'Results', 'Plan vs actual', 'Ready to ship',
                         'On-time delivery by month', 'Create new customers', 'Check file', 'Show inactive', 'Add code', 'Deactivate'):
                self.assertIn(translation.gettext(term), text, term)
        self.assertEqual(text.count('\n## よくある質問'), 1)
        self.assertGreaterEqual(text.count('**Q'), 5)
        self.assertLessEqual(text.count('**Q'), 8)


class UserPageTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)

    def post_edit(self, user, **data):
        return self.client.post(f'/users/{user.pk}/', {'name': '', 'role': 'Operator', 'is_active': 'on', **data})

    def test_operator_gets_403_and_anonymous_is_redirected(self):
        self.client.force_login(make_user('op', 'Operator'))
        for url in ('/users/', '/users/add/'):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get('/users/').status_code, 302)

    def test_list_hides_admins_from_planners_and_searches(self):
        make_user('sato', 'Operator')
        make_user('boss', 'Admin')
        User.objects.create_superuser('root', password='x')
        r = self.client.get('/users/')
        self.assertContains(r, '>sato<')
        for hidden in ('boss', 'root'):
            self.assertNotContains(r, f'>{hidden}<')
        self.assertNotContains(self.client.get('/users/?q=zzz'), '>sato<')
        self.client.force_login(make_user('adm', 'Admin'))
        self.assertContains(self.client.get('/users/'), '>boss<')

    def test_admin_user_by_url_is_404_for_planners(self):
        boss, root = make_user('boss', 'Admin'), User.objects.create_superuser('root', password='x')
        for u in (boss, root):
            for url in (f'/users/{u.pk}/', f'/users/{u.pk}/reset/'):
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, {'role': 'Operator'}).status_code, 404)
        self.client.force_login(make_user('adm', 'Admin'))
        self.assertEqual(self.client.get(f'/users/{boss.pk}/').status_code, 200)

    def test_create_shows_temporary_password_once_and_forces_change(self):
        r = self.client.post('/users/add/', {'username': 'new', 'name': 'N', 'role': 'Operator'})
        password = r.context['password']
        self.assertEqual(len(password), 12)
        self.assertFalse(set(password) & set('IOlo01'))
        user = User.objects.get(username='new')
        self.assertTrue(user.check_password(password) and user.profile.must_change_password)
        self.assertEqual(user.groups.get().name, 'Operator')
        self.assertNotContains(self.client.get('/users/'), password)
        self.assertEqual(LogEntry.objects.filter(object_id=str(user.pk), action_flag=ADDITION).count(), 1)

    def test_duplicate_username_is_rejected(self):
        make_user('dup', 'Operator')
        r = self.client.post('/users/add/', {'username': 'DUP', 'name': '', 'role': 'Operator'})
        self.assertEqual(User.objects.count(), 2)
        self.assertContains(r, 'already')

    def test_planner_cannot_create_or_promote_to_admin(self):
        r = self.client.post('/users/add/', {'username': 'x', 'name': '', 'role': 'Admin'})
        self.assertFalse(User.objects.filter(username='x').exists())
        self.assertEqual(r.status_code, 200)
        op = make_user('op', 'Operator')
        self.post_edit(op, role='Admin')
        self.assertEqual(list(op.groups.values_list('name', flat=True)), ['Operator'])
        with self.assertRaises(ValidationError):
            accounts.update_user(self.planner, op, '', 'Admin', True)

    def test_admin_may_assign_admin(self):
        self.client.force_login(make_user('adm', 'Admin'))
        self.client.post('/users/add/', {'username': 'x', 'name': '', 'role': 'Admin'})
        self.assertEqual(User.objects.get(username='x').groups.get().name, 'Admin')

    def test_edit_changes_role_and_name_and_logs(self):
        op = make_user('op', 'Operator')
        self.assertRedirects(self.post_edit(op, name='Sato', role='Planner'), '/users/')
        op.refresh_from_db()
        self.assertEqual((op.first_name, accounts.current_role(op)), ('Sato', 'Planner'))
        self.assertTrue(LogEntry.objects.filter(object_id=str(op.pk), action_flag=CHANGE).exists())

    def test_deactivate_via_switch_and_no_delete(self):
        op = make_user('op', 'Operator')
        self.post_edit(op, is_active='')
        op.refresh_from_db()
        self.assertFalse(op.is_active)
        self.assertIn('deactivated', LogEntry.objects.get(object_id=str(op.pk)).change_message)
        self.assertNotContains(self.client.get(f'/users/{op.pk}/'), 'Delete')

    def test_nobody_changes_own_role_or_deactivates_self(self):
        make_user('pl2', 'Planner')
        self.post_edit(self.planner, role='Operator', is_active='')
        self.planner.refresh_from_db()
        self.assertTrue(self.planner.is_active)
        self.assertEqual(accounts.current_role(self.planner), 'Planner')
        with self.assertRaises(ValidationError):
            accounts.update_user(self.planner, self.planner, '', 'Operator', True)
        with self.assertRaises(ValidationError):
            accounts.update_user(self.planner, self.planner, '', 'Planner', False)

    def test_last_active_planner_is_protected(self):
        self.client.force_login(make_user('adm', 'Admin'))
        self.assertContains(self.post_edit(self.planner, role='Operator'), 'last active Planner')
        self.post_edit(self.planner, role='Planner', is_active='')
        self.planner.refresh_from_db()
        self.assertTrue(self.planner.is_active)
        make_user('pl2', 'Planner')
        self.post_edit(self.planner, role='Operator')
        self.assertEqual(accounts.current_role(User.objects.get(pk=self.planner.pk)), 'Operator')

    def test_reset_makes_new_password_and_forces_change(self):
        op = make_user('op', 'Operator')
        self.assertContains(self.client.get(f'/users/{op.pk}/reset/'), 'Reset password')
        r = self.client.post(f'/users/{op.pk}/reset/')
        op.refresh_from_db()
        self.assertTrue(op.check_password(r.context['password']) and op.profile.must_change_password)
        self.assertIn('reset', LogEntry.objects.get(object_id=str(op.pk)).change_message)

    def test_temporary_password_alphabet(self):
        self.assertTrue(all(len(accounts.temp_password()) == 12 for _i in range(50)))
        self.assertFalse(set(accounts.ALPHABET) & set('IOlo01'))


class ForcedPasswordChangeTests(TestCase):
    def login(self, must_change=True):
        user = make_user('op', 'Operator')
        Profile.objects.filter(user=user).update(must_change_password=must_change)
        self.client.force_login(user)
        return user

    def test_profile_is_created_automatically(self):
        self.assertFalse(make_user('anyone').profile.must_change_password)

    def test_every_page_redirects_to_password_change(self):
        self.login()
        for url in ('/', '/orders/', '/users/'):
            self.assertRedirects(self.client.get(url), '/account/password/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/account/password/').status_code, 200)

    def test_logout_stays_reachable(self):
        self.login()
        self.assertRedirects(self.client.post('/logout/'), '/login/', fetch_redirect_response=False)

    def test_change_clears_flag(self):
        user = self.login()
        r = self.client.post('/account/password/', {'old_password': 'x', 'new_password1': 'Zq7-long-pass-9', 'new_password2': 'Zq7-long-pass-9'})
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertFalse(user.profile.must_change_password)
        self.assertEqual(self.client.get('/orders/').status_code, 200)

    def test_weak_password_keeps_flag(self):
        user = self.login()
        r = self.client.post('/account/password/', {'old_password': 'x', 'new_password1': '123', 'new_password2': '123'})
        self.assertEqual(r.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.profile.must_change_password)

    def test_login_sets_flag_from_profile(self):
        self.login()
        self.client.logout()
        r = self.client.post('/login/', {'username': 'op', 'password': 'x'})
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertRedirects(self.client.get('/'), '/account/password/', fetch_redirect_response=False)

    def test_everyone_has_change_password_link(self):
        self.login(False)
        self.assertContains(self.client.get('/'), 'href="/account/password/"')
