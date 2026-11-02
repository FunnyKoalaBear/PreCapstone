from datetime import timedelta
from io import StringIO

from django.contrib.admin.models import CHANGE, LogEntry
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import SiteSetting
from core.rules import min_rate_quantity, silent_after, void_window
from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Event, Machine, ScannerDevice, Tag, TagAssignment
from tracking.services import correct_event, record_event

URL = '/settings/?tab=rules'
GOOD = {'silent_after_hours': 48, 'void_window_days': 14, 'min_rate_quantity': 10}


class RulesTabTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.now = timezone.now()

    def save(self, **data):
        return self.client.post(URL, {'action': 'save', **{**{k: getattr(SiteSetting.get(), k) for k in GOOD}, **data}}, follow=True)

    def item(self):
        proc = Process.objects.create(name='Mill', kind=Process.Kind.MACHINING)
        item = OrderItem.objects.create(
            order=Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=9)), part_name='P', quantity=1)
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid='RU1'), item=item, start_at=self.now - timedelta(days=30))
        return item, a, proc

    def test_defaults_are_unchanged(self):
        s = SiteSetting.get()
        self.assertEqual((s.pk, s.silent_after_hours, s.void_window_days, s.min_rate_quantity), (1, 24, 7, 20))
        self.assertEqual((silent_after(), void_window(), min_rate_quantity()), (timedelta(hours=24), timedelta(days=7), 20))
        self.assertEqual(SiteSetting.objects.count(), 1)
        SiteSetting().save()  # a second object still lands on the single row
        self.assertEqual(SiteSetting.objects.count(), 1)

    def test_page_shows_each_meaning_and_default(self):
        r = self.client.get(URL)
        for text in ('Default: 24', 'Default: 7', 'Default: 20', 'marked silent', 'voided within', 'few data', 'Reset to defaults'):
            self.assertContains(r, text)
        self.assertContains(self.client.get('/settings/'), 'tab=rules')

    def test_operator_gets_403(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.post(URL, {'action': 'save', **GOOD}).status_code, 403)
        self.assertEqual(SiteSetting.get().silent_after_hours, 24)

    def test_save_changes_values_and_logs_old_to_new(self):
        self.assertContains(self.save(**GOOD), 'Rules saved.')
        s = SiteSetting.get()
        self.assertEqual((s.silent_after_hours, s.void_window_days, s.min_rate_quantity), (48, 14, 10))
        msg = LogEntry.objects.get(action_flag=CHANGE, object_id='1').change_message
        for part in ('silent_after_hours 24 -> 48', 'void_window_days 7 -> 14', 'min_rate_quantity 20 -> 10'):
            self.assertIn(part, msg)

    def test_saving_without_a_change_writes_no_log(self):
        self.save()
        self.assertFalse(LogEntry.objects.filter(object_id='1').exists())

    def test_out_of_range_is_rejected_in_place(self):
        for field, bad in (('silent_after_hours', 0), ('silent_after_hours', 169), ('void_window_days', 91), ('void_window_days', 0),
                           ('min_rate_quantity', 1001), ('min_rate_quantity', 'x')):
            r = self.save(**{field: bad})
            self.assertContains(r, 'errorlist', msg_prefix=f'{field}={bad}')
            self.assertEqual(r.redirect_chain, [])
        self.assertEqual(SiteSetting.get().silent_after_hours, 24)
        self.save(silent_after_hours=1, void_window_days=90, min_rate_quantity=1000)  # the limits themselves are fine
        self.assertEqual(SiteSetting.get().min_rate_quantity, 1000)

    def test_reset_restores_defaults_and_logs(self):
        self.save(**GOOD)
        self.assertContains(self.client.post(URL, {'action': 'reset'}, follow=True), 'reset to the defaults')
        s = SiteSetting.get()
        self.assertEqual((s.silent_after_hours, s.void_window_days, s.min_rate_quantity), (24, 7, 20))
        self.assertIn('Reset', LogEntry.objects.filter(object_id='1').latest('action_time').change_message)

    def test_silent_hours_change_which_scanners_the_dashboard_lists(self):
        ScannerDevice.objects.create(name='Stale', last_seen=self.now - timedelta(hours=30))
        names = lambda: [d.name for d in self.client.get('/dashboard/').context['silent_devices']]
        self.assertEqual(names(), ['Stale'])
        self.save(silent_after_hours=48)
        self.assertEqual(names(), [])
        self.save(silent_after_hours=12)
        self.assertEqual(names(), ['Stale'])

    def test_void_window_changes_the_dashboard_count(self):
        item, a, proc = self.item()
        device = ScannerDevice.objects.create(name='D')
        event = record_event(device, a.tag.uid, proc, 'STARTED')[0]
        correction = correct_event(event, self.planner, 'mistake')[0]
        Event.objects.filter(pk=correction.pk).update(occurred_at=self.now - timedelta(days=10))
        count = lambda: self.client.get('/dashboard/').context['voided']
        self.assertEqual(count(), 0)
        self.save(void_window_days=14)
        self.assertEqual(count(), 1)
        self.save(void_window_days=7)
        self.assertEqual(count(), 0)

    def test_min_quantity_changes_the_few_data_grey_out(self):
        item, a, proc = self.item()
        machine = Machine.objects.create(name='M1')
        e = Event(tag_uid=a.tag.uid, process=proc, event_type=Event.Type.RESULT, quantity=15, defects=1, machine=machine,
                  created_by=self.planner, occurred_at=self.now - timedelta(days=1))
        e.full_clean()
        e.save()
        few = lambda: [r['few'] for r in self.client.get('/analysis/', {'tab': 'results'}).context['machine_rows']]
        self.assertEqual(few(), [True])
        self.assertContains(self.client.get('/analysis/', {'tab': 'results'}), 'fewer than 20 pieces')
        self.save(min_rate_quantity=10)
        self.assertEqual(few(), [False])
        self.assertContains(self.client.get('/analysis/', {'tab': 'results'}), 'fewer than 10 pieces')

    @override_settings(DEBUG=True)
    def test_seed_demo_reset_restores_the_defaults(self):
        self.save(**GOOD)
        call_command('seed_demo', '--reset', stdout=StringIO())
        s = SiteSetting.get()
        self.assertEqual((s.silent_after_hours, s.void_window_days, s.min_rate_quantity), (24, 7, 20))
