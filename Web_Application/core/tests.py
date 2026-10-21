import gettext
import re
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase as RawTestCase
from django.test import override_settings
from django.utils import translation
from django.utils import timezone

from core.templatetags.duration import duration
from core.management.commands.seed_demo import read_csv
from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from tracking.models import Box, Event, Machine, ScannerDevice, Tag, TagAssignment, Worker
from tracking.overview import board_groups
from tracking.planning import process_averages
from tracking.priority import priority
from tracking.services import record_event
from tracking.status import days_late, due_back, is_stagnant, item_status

K = Process.Kind


class RoleTests(TestCase):
    """Page group -> lowest role allowed (Operator < Planner < Admin; superusers always pass)."""
    GROUPS = {
        'Operator': ['/', '/orders/', '/priorities/', '/boxes/'],
        'Planner': ['/orders/new/', '/customers/', '/tags/', '/tags/history/'],
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
        for hidden in ('/orders/new/', '/customers/', '/tags/', '/simulate/'):
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
            tag=Tag.objects.create(uid=uid), box=Box.objects.create(label=f'B{self.n}'), item=item,
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
        self.assertContains(r, 'href="/matrix/"')
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
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2X'), box=Box.objects.create(label='BX2'), item=item,
                                     start_at=now - timedelta(days=3))
        record_event(self.device, uid1, self.material, 'ARRIVED')
        record_event(self.device, uid1, self.milling, 'STARTED')
        record_event(self.device, 'T2X', self.material, 'ARRIVED', occurred_at=now - timedelta(days=2))
        r = self.home()
        milling = self.card(r, 'in_house', 'Milling')
        rows = milling['waiting'] + milling['running']
        self.assertEqual([(e['box_label'], e['code'], e['stagnant'], e['multi']) for e in rows],
                         [('BX2', 'WAITING', True, True), ('B1', 'IN_PROGRESS', False, True)])
        c = r.context['counters']
        self.assertEqual((c['in_progress'], c['waiting'], c['not_started'], c['stagnant'], c['overdue']), (1, 1, 1, 1, 0))

    def test_vendor_lateness_is_per_box(self):
        proc = Process.objects.create(name='Paint', kind=K.OUTSOURCED, vendor_name='V', vendor_lead_days=2)
        item, uid1 = self.make_item([proc])
        item.box_count = 2
        item.save()
        now = timezone.now()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2Y'), box=Box.objects.create(label='BY2'), item=item,
                                     start_at=now - timedelta(days=9))
        record_event(self.device, uid1, proc, 'SENT_OUT')
        record_event(self.device, 'T2Y', proc, 'SENT_OUT', occurred_at=now - timedelta(days=6))
        rows = [c for c in self.groups('outsourced') if c['process'] == proc][0]['running']
        self.assertEqual([(e['box_label'], e['late_days'] > 0) for e in rows], [('BY2', True), ('B1', False)])


class SeedDemoTests(TestCase):
    def seed(self, *args):
        call_command('seed_demo', *args, stdout=StringIO())

    def status(self, part):
        item = OrderItem.objects.get(part_name=part)
        return item, item_status(item)

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
        results = Event.objects.filter(event_type='RESULT')
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
        self.assertEqual(Event.objects.filter(event_type='CANCELLED').count(), 1)

    @override_settings(DEBUG=True)
    def test_seed_gives_the_plan_vs_actual_page_some_rows(self):
        self.seed()
        self.assertTrue(process_averages())

    def test_static_demo_data_matches_the_csv_files(self):
        js = (settings.BASE_DIR / 'demo' / 'js' / 'data.js').read_text(encoding='utf-8')
        demo = re.findall(r"name: '([^']+)', ja: '([^']+)', kind: '(\w+)'", js)
        self.assertEqual(demo, [(r['name'], r['name_ja'], r['kind']) for r in read_csv('processes.csv')])
        codes = {r['code']: (r['name'], r['name_ja']) for r in read_csv('codes.csv')}
        used = re.findall(r"vendor: '(\d{5})'", js)
        self.assertTrue(used)
        self.assertEqual({c: codes[c] for c in used}, {c: tuple(re.search(rf"'{c}': \['([^']+)', '([^']+)'\]", js).groups()) for c in used})
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
        Process.objects.create(name='Milling', kind=K.MATERIAL)  # wrong kind makes STARTED fail
        with self.assertRaises(Exception):
            self.seed()
        self.assertEqual((Customer.objects.count(), Order.objects.count(), Event.objects.count()), (0, 0, 0))


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
        urls = ['/', '/orders/', '/orders/new/', f'/orders/{order.job_number}/', '/tags/', '/simulate/', '/priorities/', '/boxes/']
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
