import json
import tempfile
from collections import Counter
from io import BytesIO, StringIO
from pathlib import Path
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from types import SimpleNamespace

from unittest import mock

from django.template.loader import render_to_string
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from PIL import Image
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.utils import timezone, translation

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, PrintJob, Process, RouteStep, Vendor

from .forms import AssignTagForm
from .matrix import matrix as build_matrix
from .models import DefectCause, Event, Machine, ScannerDevice, Tag, TagAssignment, Worker
from .analysis import Filters, defect_detail, result_events, vendor_rows
from .photos import process_photo
from .overview import active_boxes, process_board
from .planning import step_plan
from .priority import priority
from .services import assign_tag, cancel_item, correct_event, record_event, record_step_state, release_assignment, replace_tag, ship_item
from .status import box_caption, box_captions, manual_actions, order_summary, gantt, AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, BoxStatus, ItemStatus, _roll_up, events_for_item, days_late, next_action, due_back, idle_for, is_stagnant, item_status, timeline

E = Event.Type
K = Process.Kind


class ItemStatusTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.procs = {k: Process.objects.create(name=k, kind=k) for k in (K.MATERIAL, K.MACHINING, K.OUTSOURCED)}
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=self.now.date())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        for i, k in enumerate((K.MATERIAL, K.MACHINING, K.OUTSOURCED), 1):
            RouteStep.objects.create(item=self.item, sequence=i, process=self.procs[k])
        self.device = ScannerDevice.objects.create(name='D')
        self.start = self.now - timedelta(hours=10)
        TagAssignment.objects.create(
            tag=Tag.objects.create(uid='AB12'), item=self.item, start_at=self.start,
        )

    def ev(self, kind, etype, hours=0, **kw):
        return Event.objects.create(
            device=self.device, tag_uid='AB12', event_type=etype, process=self.procs.get(kind),
            occurred_at=self.start + timedelta(hours=hours), **kw,
        )

    def code(self):
        return item_status(self.item).code

    def test_no_events(self):
        self.assertEqual(self.code(), NOT_STARTED)

    def test_material_arrived_is_waiting(self):
        self.ev(K.MATERIAL, E.ARRIVED, 1)
        self.assertEqual(self.code(), WAITING)

    def test_started(self):
        self.ev(K.MATERIAL, E.ARRIVED, 1)
        self.ev(K.MACHINING, E.STARTED, 2)
        self.assertEqual(self.code(), IN_PROGRESS)

    def test_finished_waits_for_next(self):
        self.ev(K.MATERIAL, E.ARRIVED, 1)
        self.ev(K.MACHINING, E.STARTED, 2)
        self.ev(K.MACHINING, E.FINISHED, 3)
        s = item_status(self.item)
        self.assertEqual((s.code, s.current_step.process.kind), (WAITING, K.OUTSOURCED))

    def test_sent_out_is_at_vendor(self):
        for k, t, h in ((K.MATERIAL, E.ARRIVED, 1), (K.MACHINING, E.STARTED, 2),
                        (K.MACHINING, E.FINISHED, 3), (K.OUTSOURCED, E.SENT_OUT, 4)):
            self.ev(k, t, h)
        self.assertEqual(self.code(), AT_VENDOR)

    def test_returned_is_complete(self):
        for k, t, h in ((K.MATERIAL, E.ARRIVED, 1), (K.MACHINING, E.STARTED, 2), (K.MACHINING, E.FINISHED, 3),
                        (K.OUTSOURCED, E.SENT_OUT, 4), (K.OUTSOURCED, E.RETURNED, 5)):
            self.ev(k, t, h)
        self.assertEqual(self.code(), COMPLETE)

    def test_corrected_event_is_ignored(self):
        wrong = self.ev(K.MATERIAL, E.ARRIVED, 1)
        self.ev(None, E.CORRECTION, 2, corrects=wrong)
        self.assertEqual(self.code(), NOT_STARTED)

    def test_events_before_assignment_are_ignored(self):
        self.ev(K.MATERIAL, E.ARRIVED, -1)
        self.assertEqual(self.code(), NOT_STARTED)

    def test_active_step_after_a_pending_one_is_in_progress(self):
        # Material never scanned, but milling started: the item is being worked on
        self.ev(K.MACHINING, E.STARTED, 1)
        s = item_status(self.item)
        self.assertEqual((s.code, s.current_step.process.kind), (IN_PROGRESS, K.MACHINING))
        self.assertTrue(s.warnings)


class RecordEventTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='D')
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.now().date())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)

    def assign(self, uid):
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=self.item)

    def test_saves_event_and_returns_assignment(self):
        self.assign('AB12')
        event, assignment = record_event(self.device, ' ab12 ', self.proc, E.ARRIVED)
        self.assertEqual((event.tag_uid, Event.objects.count()), ('AB12', 1))
        self.assertEqual(assignment.item, self.item)

    def test_updates_last_seen(self):
        record_event(self.device, 'ZZ', self.proc, E.ARRIVED)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_unassigned_tag_returns_none(self):
        event, assignment = record_event(self.device, 'ZZ', self.proc, E.ARRIVED)
        self.assertIsNone(assignment)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_simulate_page_records_event(self):
        user = make_user('staff', 'Admin')
        self.client.force_login(user)
        r = self.client.post('/simulate/', {'tag_uid': 'zz', 'device': self.device.pk, 'action': f'{self.proc.pk}:ARRIVED'})
        self.assertRedirects(r, '/simulate/')
        self.assertEqual(Event.objects.get().tag_uid, 'ZZ')
        self.assertEqual(self.client.get('/simulate/').status_code, 200)


class TagSearchAndHistoryTests(TestCase):
    def setUp(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        self.item = OrderItem.objects.create(order=order, part_name='Gear', quantity=1, box_count=9)
        self.now = timezone.now()
        self.client.force_login(make_user('s', 'Admin'))

    def link(self, uid, start_days, end_days=None):
        """An assignment that began start_days ago and ended end_days ago (open when None)."""
        return TagAssignment.objects.create(
            tag=Tag.objects.get_or_create(uid=uid)[0], item=self.item,
            start_at=self.now - timedelta(days=start_days), end_at=None if end_days is None else self.now - timedelta(days=end_days))

    def history(self, **params):
        r = self.client.get('/tags/history/', params)
        self.assertEqual(r.status_code, 200)
        return [a.tag.uid for a in r.context['rows']], r

    def test_in_use_shows_count_without_rows_until_searched(self):
        self.link('AA11', 5)
        self.link('BB22', 6)
        r = self.client.get('/tags/')
        self.assertEqual((r.context['open'], r.context['in_use']), ([], 2))
        self.assertContains(r, '2 tags in use.')
        self.assertNotContains(r, '<table')
        self.assertContains(r, 'href="/tags/history/"')
        r = self.client.get('/tags/', {'q': 'bb22'})
        self.assertEqual([a.tag.uid for a in r.context['open']], ['BB22'])
        self.assertContains(r, 'Since (JST)')
        self.assertContains(self.client.get('/tags/', {'q': 'zzz'}), 'No tags in use match this search.')

    def test_release_keeps_the_search(self):
        a = self.link('AA11', 5)
        r = self.client.post('/tags/', {'action': 'release', 'assignment': a.pk, 'q': 'aa'})
        self.assertRedirects(r, '/tags/?q=aa')
        self.assertRedirects(self.client.post('/tags/', {'action': 'release', 'assignment': self.link('CC33', 1).pk}), '/tags/')

    def test_history_page_is_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get('/tags/history/').status_code, 302)

    def test_history_ranges_overlap_rule(self):
        self.link('D10', 20, 10)      # inside every range
        self.link('D100', 120, 100)  # 3-6 months ago
        self.link('D200', 250, 200)  # 6-12 months ago
        self.link('D400', 500, 400)  # older than a year
        self.link('OPEN', 400)        # open since long ago: overlaps everything
        self.link('NEW', 2)           # open, started recently
        expect = {'1m': {'D10', 'OPEN', 'NEW'}, '3m': {'D10', 'OPEN', 'NEW'}, '6m': {'D10', 'D100', 'OPEN', 'NEW'},
                  '1y': {'D10', 'D100', 'D200', 'OPEN', 'NEW'}}
        for key, uids in expect.items():
            self.assertEqual(set(self.history(range=key)[0]), uids, key)
        self.assertEqual(set(self.history()[0]), expect['3m'])
        self.assertEqual(set(self.history(range='bogus')[0]), expect['3m'])

    def test_history_is_newest_first_and_shows_in_use(self):
        self.link('OLD1', 30, 20)
        self.link('NEW1', 3)
        uids, r = self.history()
        self.assertEqual(uids, ['NEW1', 'OLD1'])
        self.assertContains(r, 'In use')
        self.assertContains(r, f'href="/orders/{self.item.order.job_number}/"')

    def test_history_custom_range_and_fallbacks(self):
        self.link('D10', 20, 10)
        self.link('D100', 120, 100)
        day = lambda n: timezone.localtime(self.now - timedelta(days=n)).date().isoformat()
        uids, r = self.history(range='custom', **{'from': day(125), 'to': day(95)})
        self.assertEqual(uids, ['D100'])
        self.assertContains(r, 'value="custom" selected')
        self.assertEqual(self.history(range='custom', **{'from': day(25), 'to': day(5)})[0], ['D10'])
        edge = self.history(range='custom', **{'from': day(100), 'to': day(100)})[0]  # the end day is included
        self.assertEqual(edge, ['D100'])
        for params in ({'from': 'x', 'to': day(5)}, {'from': day(5)}, {'from': day(5), 'to': day(50)}):
            uids, r = self.history(range='custom', **params)
            self.assertEqual(uids, ['D10'], params)  # 3m fallback
            self.assertEqual(r.context['range'], '3m')

    def test_history_search_by_tag(self):
        self.link('AB12', 5, 2)
        self.link('CD34', 5, 2)
        self.assertEqual(self.history(q='ab1')[0], ['AB12'])
        self.assertEqual(self.history(q='cd')[0], ['CD34'])
        self.assertEqual(self.history(q='nothing')[0], [])

    def test_history_cap(self):
        for i in range(201):
            self.link(f'T{i:03d}', 10, 5)
        uids, r = self.history()
        self.assertEqual((len(uids), r.context['capped']), (200, True))
        self.assertContains(r, 'Showing only the newest 200 records.')


class TagManageTests(TestCase):
    def setUp(self):
        self.proc = Process.objects.create(name='Mat', kind=K.MATERIAL)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        self.item1 = OrderItem.objects.create(order=order, part_name='A', quantity=1)
        self.item2 = OrderItem.objects.create(order=order, part_name='B', quantity=1)
        RouteStep.objects.create(item=self.item2, sequence=1, process=self.proc)

    def test_assign_creates_tag_and_open_assignment_without_a_box(self):
        a = assign_tag(self.item1, ' ab12 ')
        self.assertEqual((a.tag.uid, a.item, a.end_at, a.replaces), ('AB12', self.item1, None, None))

    def test_uid_is_uppercased(self):
        assign_tag(self.item1, ' ab12 ')
        self.assertTrue(Tag.objects.filter(uid='AB12').exists())

    def test_tag_in_use_is_rejected(self):
        assign_tag(self.item1, 'AB')
        with self.assertRaises(ValidationError):
            assign_tag(self.item2, 'ab')
        self.assertEqual(TagAssignment.objects.count(), 1)

    def test_item_with_assignment_is_rejected(self):
        assign_tag(self.item1, 'AB')
        with self.assertRaises(ValidationError):
            assign_tag(self.item1, 'CD')

    def test_release_then_reuse_and_events_count_for_new_item(self):
        old = assign_tag(self.item1, 'AB')
        release_assignment(old)
        old.refresh_from_db()
        self.assertIsNotNone(old.end_at)
        assign_tag(self.item2, 'AB')
        record_event(ScannerDevice.objects.create(name='D'), 'AB', self.proc, E.ARRIVED)
        self.assertEqual(item_status(self.item2).code, COMPLETE)
        self.assertEqual(item_status(self.item1).code, NOT_STARTED)

    def test_page_is_staff_only_and_assign_release_work(self):
        self.assertEqual(self.client.get('/tags/').status_code, 302)
        self.client.force_login(make_user('s', 'Admin'))
        self.assertEqual(self.client.get('/tags/').status_code, 200)
        r = self.client.post('/tags/', {'action': 'assign', 'item': self.item1.pk, 'box_label': 'STRAY', 'tag_uid': 'ab'})
        self.assertRedirects(r, '/tags/')
        a = TagAssignment.objects.get()
        self.assertEqual(a.tag.uid, 'AB')  # the stray box_label is ignored
        self.client.post('/tags/', {'action': 'release', 'assignment': a.pk})
        a.refresh_from_db()
        self.assertIsNotNone(a.end_at)
        self.assertEqual(self.client.post('/tags/', {'action': 'release', 'assignment': a.pk}).status_code, 404)


    def test_assign_dropdown_only_offers_items_with_a_free_box_slot(self):
        self.item2.box_count = 2
        self.item2.save()
        assign_tag(self.item1, 'AB')
        assign_tag(self.item2, 'CD')
        field = AssignTagForm().fields['item']
        self.assertEqual(list(field.queryset), [self.item2])
        label = field.label_from_instance(field.queryset.get())
        self.assertIn('(1 of 2 boxes linked)', label)
        self.assertTrue(label.startswith(self.item2.item_code + ' - '))

    def test_tags_page_labels_each_open_box_of_an_item(self):
        self.item1.box_count = 2
        self.item1.save()
        assign_tag(self.item1, 'AB')
        assign_tag(self.item1, 'CD')
        self.client.force_login(make_user('s', 'Admin'))
        r = self.client.get('/tags/', {'q': self.item1.order.job_number})
        self.assertContains(r, 'Box 1 of 2')
        self.assertContains(r, 'Box 2 of 2')


class DueBackTests(TestCase):
    def test_due_back_adds_lead_days_in_jst(self):
        # 20:00 UTC on Jan 1 is 05:00 JST on Jan 2
        out = datetime(2030, 1, 1, 20, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(due_back(out, 7), date(2030, 1, 9))

    def test_due_back_without_lead_days_is_none(self):
        self.assertIsNone(due_back(timezone.now(), None))

    def test_days_late_only_when_positive(self):
        today = date(2030, 1, 10)
        self.assertEqual((days_late(date(2030, 1, 7), today), days_late(today, today), days_late(None, today)), (3, 0, 0))


class GanttTests(TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, 9, 0, tzinfo=dt_timezone.utc)

    def row(self, state, start=None, end=None):
        from .status import StepRow
        h = lambda x: None if x is None else self.t0 + timedelta(hours=x)
        return StepRow(None, state, h(start), h(end))

    def test_finished_steps_and_wait_gap(self):
        g = gantt([self.row('done', 0, 2), self.row('done', 4, 8)])
        a, b = g['rows']
        self.assertEqual((a['kind'], a['left'], a['width'], a['wait_left']), ('done', 0, 25, None))
        self.assertEqual((b['left'], b['width'], b['wait_left'], b['wait_width']), (50, 50, 25, 25))
        self.assertIsNone(g['now_pct'])

    def test_running_step_ends_at_now(self):
        g = gantt([self.row('done', 0, 2), self.row('in_progress', 2)], now=self.t0 + timedelta(hours=4))
        run = g['rows'][1]
        self.assertEqual((run['kind'], run['left'], run['width'], run['wait_width'], g['now_pct'], g['end']), ('in_progress', 50, 50, None, 100, self.t0 + timedelta(hours=4)))

    def test_single_instant_step(self):
        g = gantt([self.row('done', 1, 1)], now=self.t0 + timedelta(hours=5))
        self.assertEqual((g['rows'][0]['kind'], g['rows'][0]['left'], g['start'], g['end']), ('instant', 0, g['end'], g['start']))

    def test_no_wait_without_positive_gap(self):
        g = gantt([self.row('done', 0, 2), self.row('done', 2, 3), self.row('done', 1, 4)])
        self.assertEqual([r['wait_width'] for r in g['rows']], [None, None, None])

    def test_pending_steps_and_no_started(self):
        self.assertIsNone(gantt([self.row('pending'), self.row('pending')]))
        g = gantt([self.row('done', 0, 1), self.row('pending')])
        self.assertEqual(g['rows'][1]['kind'], 'pending')

    def test_zero_span_does_not_divide_by_zero(self):
        g = gantt([self.row('done', 0, 0), self.row('done', 0, 0)])
        self.assertEqual([r['kind'] for r in g['rows']], ['instant', 'instant'])

    def test_bars_at_least_one_percent(self):
        g = gantt([self.row('done', 0, 0.01), self.row('done', 50, 100)])
        self.assertGreaterEqual(g['rows'][0]['width'], 1)
        self.assertLessEqual(g['rows'][0]['left'] + g['rows'][0]['width'], 100)


class TimelineTests(TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, 9, 0, tzinfo=dt_timezone.utc)

    def row(self, state, start=None, end=None):
        from .status import StepRow
        h = lambda x: None if x is None else self.t0 + timedelta(hours=x)
        return StepRow(None, state, h(start), h(end))

    def test_finished_step(self):
        t = timeline([self.row('done', 0, 2)], now=self.t0 + timedelta(hours=9))[0]
        self.assertEqual((t['duration'], t['running']), (timedelta(hours=2), False))

    def test_running_step(self):
        t = timeline([self.row('in_progress', 1)], now=self.t0 + timedelta(hours=4))[0]
        self.assertEqual((t['duration'], t['running']), (timedelta(hours=3), True))

    def test_running_at_vendor(self):
        t = timeline([self.row('at_vendor', 0)], now=self.t0 + timedelta(hours=5))[0]
        self.assertEqual((t['running'], t['duration']), (True, timedelta(hours=5)))

    def test_arrived_step_has_no_duration(self):
        t = timeline([self.row('done', 2, 2)], now=self.t0 + timedelta(hours=9))[0]
        self.assertIsNone(t['duration'])

    def test_first_step_has_no_wait(self):
        self.assertIsNone(timeline([self.row('done', 1, 2)])[0]['wait_before'])

    def test_unstarted_step(self):
        t = timeline([self.row('pending')])[0]
        self.assertEqual((t['duration'], t['running'], t['wait_before']), (None, False, None))

    def test_missing_previous_finish(self):
        t = timeline([self.row('in_progress', 0), self.row('in_progress', 3)], now=self.t0 + timedelta(hours=9))
        self.assertIsNone(t[1]['wait_before'])

    def test_wait_uses_last_finished_step(self):
        rows = [self.row('done', 0, 1), self.row('pending'), self.row('done', 4, 5)]
        t = timeline(rows, now=self.t0 + timedelta(hours=9))
        self.assertEqual(t[2]['wait_before'], timedelta(hours=3))
        self.assertIsNone(t[0]['wait_before'])

    def test_negative_wait_is_none(self):
        t = timeline([self.row('done', 0, 5), self.row('done', 2, 3)])
        self.assertIsNone(t[1]['wait_before'])


class PriorityTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.procs = {k: Process.objects.create(name=k, kind=k, vendor_lead_days=3 if k == K.OUTSOURCED else None)
                      for k in (K.MATERIAL, K.MACHINING, K.OUTSOURCED)}
        self.device = ScannerDevice.objects.create(name='D')
        self.n = 0

    def make(self, days, kinds=(K.MACHINING,), send_days_ago=None):
        """Item due `days` from today with a route of `kinds`; optionally sent out N days ago."""
        self.n += 1
        order = Order.objects.create(customer=Customer.objects.create(name=f'C{self.n}'), due_date=self.today + timedelta(days=days))
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        for i, k in enumerate(kinds, 1):
            RouteStep.objects.create(item=item, sequence=i, process=self.procs[k])
        tag = Tag.objects.create(uid=f'T{self.n}')
        TagAssignment.objects.create(tag=tag, item=item, start_at=timezone.now() - timedelta(days=30))
        if send_days_ago is not None:
            Event.objects.create(device=self.device, tag_uid=tag.uid, event_type=E.SENT_OUT, process=self.procs[K.OUTSOURCED],
                                 occurred_at=timezone.now() - timedelta(days=send_days_ago))
        return item

    def pr(self, item):
        return priority(item.order, item_status(item), self.today)

    def test_overdue(self):
        self.assertEqual(self.pr(self.make(-1))['tier'], 'OVERDUE')

    def test_critical_not_due_yet(self):
        p = self.pr(self.make(1, (K.MATERIAL, K.MACHINING, K.MACHINING)))
        self.assertEqual((p['tier'], p['slack'], p['days_needed']), ('CRITICAL', -2, 3))

    def test_watch_and_on_track(self):
        self.assertEqual(self.pr(self.make(3))['tier'], 'WATCH')
        self.assertEqual(self.pr(self.make(4))['tier'], 'ON_TRACK')

    def test_vendor_lead_days_count_for_pending_step(self):
        self.assertEqual(self.pr(self.make(10, (K.OUTSOURCED,)))['days_needed'], 3)

    def test_vendor_step_uses_remaining_days(self):
        p = self.pr(self.make(10, (K.OUTSOURCED,), send_days_ago=1))
        self.assertEqual(p['days_needed'], 2)

    def test_vendor_late(self):
        p = self.pr(self.make(10, (K.OUTSOURCED,), send_days_ago=5))
        self.assertEqual(p['days_needed'], 0)
        self.assertTrue(any('late' in r for r in p['reasons']))

    def test_view_sorts_overdue_first_and_filters_tier(self):
        self.make(30)
        late = self.make(-5)
        self.client.force_login(make_user('s', 'Admin'))
        rows = self.client.get('/priorities/').context['rows']
        self.assertEqual((rows[0]['item'], rows[0]['tier']), (late, 'OVERDUE'))
        only = self.client.get('/priorities/?tier=OVERDUE').context['rows']
        self.assertEqual([r['tier'] for r in only], ['OVERDUE'])
        self.assertEqual(len(self.client.get('/priorities/?tier=bogus').context['rows']), 2)

    def idle_scan(self, item, hours_ago):
        Event.objects.create(device=self.device, tag_uid=item.tagassignment_set.get().tag.uid, event_type=E.STARTED,
                             process=self.procs[K.MACHINING], occurred_at=timezone.now() - timedelta(hours=hours_ago))

    def sorted_rows(self, query=''):
        self.client.force_login(make_user('s', 'Admin'))
        return self.client.get('/priorities/' + query).context['rows']

    def test_sort_options_and_fallback(self):
        a = self.make(-5)  # overdue, never scanned (no idle)
        b = self.make(30)
        c = self.make(10, (K.OUTSOURCED,))
        d = self.make(2)
        e = self.make(20)
        for item, h in ((b, 5), (d, 50), (e, 20)):
            self.idle_scan(item, h)
        items = lambda q: [r['item'] for r in self.sorted_rows(q)]
        self.assertEqual(items(''), items('?sort=risk'))
        self.assertEqual(items('?sort=risk')[0], a)  # overdue first
        self.assertEqual(items('?sort=waiting'), [d, e, b, a, c][:3] + items('?sort=waiting')[3:])  # longest idle first, unscanned last
        self.assertEqual(set(items('?sort=waiting')[3:]), {a, c})
        procs = [r['process'].name if r['process'] else '' for r in self.sorted_rows('?sort=process')]
        self.assertEqual(procs, sorted(procs))
        self.assertEqual(procs[0], K.MACHINING)
        for bad in ('?sort=bogus', '?sort='):
            self.assertEqual(items(bad), items('?sort=risk'))

    def test_process_sort_breaks_ties_by_risk(self):
        far = self.make(30)
        near = self.make(-2)
        mid = self.make(2)
        self.assertEqual([r['item'] for r in self.sorted_rows('?sort=process')], [near, mid, far])

    def test_tier_filter_works_with_sort_and_is_kept_by_the_form(self):
        self.make(-5)
        late = self.make(-1)
        self.make(30)
        self.idle_scan(late, 10)
        self.client.force_login(make_user('s', 'Admin'))
        r = self.client.get('/priorities/?tier=OVERDUE&sort=waiting')
        self.assertEqual([x['item'] for x in r.context['rows']][0], late)
        self.assertEqual({x['tier'] for x in r.context['rows']}, {'OVERDUE'})
        self.assertContains(r, '<input type="hidden" name="tier" value="OVERDUE">')
        self.assertContains(r, '<option value="waiting" data-dir="desc" selected>')
        self.assertContains(r, 'href="?tier=CRITICAL&amp;sort=waiting"')

    def test_idle_column_shows_duration(self):
        item = self.make(10)
        self.idle_scan(item, 5)
        rows = self.sorted_rows()
        self.assertGreaterEqual(rows[0]['idle'], timedelta(hours=5))

    def test_priorities_one_row_per_linked_box_with_own_risk(self):
        item = self.make(1, (K.MACHINING, K.MACHINING))
        item.box_count = 3
        item.save()
        mach = self.procs[K.MACHINING]
        a1 = item.tagassignment_set.get()
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2B'), item=item,
                                     start_at=timezone.now() - timedelta(days=5))
        for etype in (E.STARTED, E.FINISHED):  # box 1 finishes step 1
            Event.objects.create(device=self.device, tag_uid=a1.tag.uid, event_type=etype, process=mach)
        self.client.force_login(make_user('s', 'Admin'))
        rows = self.client.get('/priorities/').context['rows']
        self.assertEqual([(r['caption'], r['tier']) for r in rows],
                         [('Box 2 of 3', 'CRITICAL'), ('Box 1 of 3', 'WATCH')])  # unlinked third box left out


@override_settings(STAGNATION_DEFAULT_HOURS=24)
class StagnationTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def st(self, code=WAITING, idle_hours=30, limit=None):
        step = SimpleNamespace(process=SimpleNamespace(stagnation_hours=limit))
        last = None if idle_hours is None else self.now - timedelta(hours=idle_hours)
        return ItemStatus(code, code, step, last_event_at=last)

    def test_over_and_under_default(self):
        self.assertTrue(is_stagnant(self.st(idle_hours=25), self.now))
        self.assertFalse(is_stagnant(self.st(idle_hours=23), self.now))

    def test_in_progress_counts(self):
        self.assertTrue(is_stagnant(self.st(IN_PROGRESS, 30), self.now))

    def test_per_process_override(self):
        self.assertTrue(is_stagnant(self.st(idle_hours=5, limit=4), self.now))
        self.assertFalse(is_stagnant(self.st(idle_hours=30, limit=48), self.now))

    def test_other_states_never_stagnant(self):
        for code in (AT_VENDOR, NOT_STARTED, COMPLETE):
            self.assertFalse(is_stagnant(self.st(code, 1000), self.now))

    def test_no_events(self):
        st = self.st(idle_hours=None)
        self.assertIsNone(idle_for(st, self.now))
        self.assertFalse(is_stagnant(st, self.now))


class MultiBoxTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.start = self.now - timedelta(days=2)
        self.procs = {k: Process.objects.create(name=k, kind=k) for k in (K.MATERIAL, K.MACHINING)}
        self.order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=self.now.date())
        self.item = self.new_item(2)
        self.device = ScannerDevice.objects.create(name='D')

    def new_item(self, boxes):
        item = OrderItem.objects.create(order=self.order, part_name='P', quantity=1, box_count=boxes)
        for i, k in enumerate((K.MATERIAL, K.MACHINING), 1):
            RouteStep.objects.create(item=item, sequence=i, process=self.procs[k])
        return item

    def link(self, uid, item=None, start=None, end=None):
        return TagAssignment.objects.create(
            tag=Tag.objects.get_or_create(uid=uid)[0], item=item or self.item,
            start_at=start or self.start, end_at=end,
        )

    def ev(self, uid, kind, etype, hours):
        return Event.objects.create(
            device=self.device, tag_uid=uid, event_type=etype, process=self.procs[kind],
            occurred_at=self.start + timedelta(hours=hours),
        )

    def run_route(self, uid, h=1):
        self.ev(uid, K.MATERIAL, E.ARRIVED, h)
        self.ev(uid, K.MACHINING, E.STARTED, h + 1)
        self.ev(uid, K.MACHINING, E.FINISHED, h + 2)

    def test_single_box_item_has_one_box_with_item_rows(self):
        item = self.new_item(1)
        self.link('S1', item)
        st = item_status(item)
        self.assertEqual((len(st.boxes), st.code), (1, NOT_STARTED))
        self.assertIs(st.rows, st.boxes[0].rows)

    def test_boxes_at_different_steps_give_no_warnings(self):
        self.link('A'), self.link('B')
        self.ev('A', K.MATERIAL, E.ARRIVED, 1)
        self.ev('A', K.MACHINING, E.STARTED, 2)
        self.ev('B', K.MATERIAL, E.ARRIVED, 3)
        st = item_status(self.item)
        self.assertEqual((st.code, st.warnings), (IN_PROGRESS, []))
        self.assertEqual([b.code for b in st.boxes], [IN_PROGRESS, WAITING])

    def test_boxes_doing_the_same_step(self):
        self.link('A'), self.link('B')
        for uid in 'AB':
            self.ev(uid, K.MATERIAL, E.ARRIVED, 1)
            self.ev(uid, K.MACHINING, E.STARTED, 2)
        st = item_status(self.item)
        self.assertEqual((st.code, st.warnings, [b.code for b in st.boxes]), (IN_PROGRESS, [], [IN_PROGRESS] * 2))

    def test_placeholder_box_blocks_complete(self):
        self.link('A')
        self.run_route('A')
        st = item_status(self.item)
        self.assertEqual((st.code, [b.code for b in st.boxes]), (WAITING, [COMPLETE, NOT_STARTED]))
        self.assertIsNone(st.boxes[1].assignment)

    def test_released_finished_box_still_counts(self):
        item = self.new_item(1)
        self.link('R', item, end=self.start + timedelta(days=1))
        self.run_route('R')
        self.assertEqual(item_status(item).code, COMPLETE)

    def test_all_boxes_complete_is_complete(self):
        self.link('A'), self.link('B')
        self.run_route('A'), self.run_route('B', 4)
        self.assertEqual(item_status(self.item).code, COMPLETE)

    def test_roll_up_codes(self):
        box = lambda c: BoxStatus(None, None, c, c)
        cases = [
            ([COMPLETE, COMPLETE], COMPLETE), ([NOT_STARTED, NOT_STARTED], NOT_STARTED),
            ([IN_PROGRESS, WAITING], IN_PROGRESS), ([WAITING, AT_VENDOR], WAITING),
            ([AT_VENDOR, COMPLETE], AT_VENDOR), ([AT_VENDOR, NOT_STARTED], AT_VENDOR),
            ([COMPLETE, NOT_STARTED], WAITING), ([IN_PROGRESS, COMPLETE, NOT_STARTED], IN_PROGRESS),
        ]
        for codes, expected in cases:
            self.assertEqual(_roll_up([box(c) for c in codes]).code, expected, codes)

    def test_assign_tag_respects_box_count(self):
        assign_tag(self.item, 'U1')
        assign_tag(self.item, 'U2')
        with self.assertRaisesMessage(ValidationError, 'already has all 2 boxes linked'):
            assign_tag(self.item, 'U3')
        self.item.box_count = 3
        self.item.save()
        assign_tag(self.item, 'U3')
        self.assertEqual(self.item.tagassignment_set.count(), 3)

    def test_reassigned_tag_does_not_leak_events(self):
        other = self.new_item(1)
        self.link('X', end=self.start + timedelta(hours=10))
        self.link('X', other, start=self.start + timedelta(hours=10))
        self.ev('X', K.MATERIAL, E.ARRIVED, 5)
        self.ev('X', K.MATERIAL, E.ARRIVED, 12)
        self.assertEqual(len(list(events_for_item(self.item))), 1)
        self.assertEqual(len(list(events_for_item(other))), 1)
        self.assertEqual(item_status(other).boxes[0].rows[0].started_at, self.start + timedelta(hours=12))


class OverviewBase(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.cut = Process.objects.create(name='Cut', kind=K.MACHINING)
        self.weld = Process.objects.create(name='Weld', kind=K.MACHINING)
        self.paint = Process.objects.create(name='Paint', kind=K.OUTSOURCED, vendor_name='PV', vendor_lead_days=3)
        self.device = ScannerDevice.objects.create(name='D')
        self.cust = Customer.objects.create(name='C')
        self.n = 0

    def item(self, route, due=10, boxes=1, uids=(), ended=()):
        self.n += 1
        order = Order.objects.create(customer=self.cust, due_date=timezone.localdate() + timedelta(days=due))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=1, box_count=boxes)
        for i, p in enumerate(route, 1):
            RouteStep.objects.create(item=item, sequence=i, process=p)
        for uid in uids:
            TagAssignment.objects.create(
                tag=Tag.objects.create(uid=uid), item=item,
                start_at=self.now - timedelta(days=20), end_at=self.now - timedelta(days=1) if uid in ended else None,
            )
        return item

    def scan(self, uid, proc, etype, hours_ago):
        Event.objects.create(device=self.device, tag_uid=uid, event_type=etype, process=proc,
                             occurred_at=self.now - timedelta(hours=hours_ago))

    def staff(self):
        self.client.force_login(make_user('s', 'Admin'))

    def scenario(self):
        """7 unfinished boxes: one of each status plus 4 not started (2 are unlinked placeholders)."""
        self.item([self.cut], uids=['A'])
        self.scan('A', self.cut, E.STARTED, 2)
        self.item([self.cut, self.weld], due=-2, uids=['B'])  # overdue, waiting 30h = stagnant
        self.scan('B', self.cut, E.STARTED, 40)
        self.scan('B', self.cut, E.FINISHED, 30)
        self.item([self.paint], uids=['C'])
        self.scan('C', self.paint, E.SENT_OUT, 24 * 5)  # lead 3 days: late
        self.item([self.cut], uids=['D'])
        self.item([self.cut], boxes=2, uids=['E'])
        self.item([self.cut], boxes=2, uids=['F'], ended=['F'])  # released finished box + placeholder
        self.scan('F', self.cut, E.STARTED, 100)
        self.scan('F', self.cut, E.FINISHED, 99)
        self.item([self.cut], uids=['G'])  # complete: not listed
        self.scan('G', self.cut, E.STARTED, 5)
        self.scan('G', self.cut, E.FINISHED, 4)


class ActiveBoxesTests(OverviewBase):
    def test_each_status_placeholder_and_released_box(self):
        self.scenario()
        rows = {(e['item'].part_name, e['tag_uid']): e for e in active_boxes(self.now)}
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows['P1', 'A']['code'], IN_PROGRESS)
        self.assertEqual(rows['P1', 'A']['process'], self.cut)
        b = rows['P2', 'B']
        self.assertEqual((b['code'], b['process'], b['stagnant'], b['late']), (WAITING, self.weld, True, True))
        self.assertEqual(b['idle'], timedelta(hours=30))
        c = rows['P3', 'C']
        self.assertEqual((c['code'], c['late_days'], c['vendor']), (AT_VENDOR, 2, 'PV'))
        self.assertEqual(c['due_back'], due_back(c['started_at'], 3))
        self.assertEqual(rows['P4', 'D']['code'], NOT_STARTED)
        self.assertIn(('P5', None), rows)  # placeholder
        self.assertEqual(rows['P5', None]['code'], NOT_STARTED)
        self.assertIn(('P6', None), rows)
        self.assertNotIn(('P6', 'F'), rows)  # released finished box is done
        self.assertFalse([k for k in rows if k[0] == 'P7'])  # complete item


class BoxesPageTests(OverviewBase):
    def rows(self, query=''):
        r = self.client.get('/boxes/' + query)
        self.assertEqual(r.status_code, 200)
        return r.context['rows']

    def test_staff_only(self):
        self.assertEqual(self.client.get('/boxes/').status_code, 302)

    def test_filters_and_invalid_values(self):
        self.scenario()
        self.staff()
        sizes = {q: len(self.rows(q)) for q in (
            '', '?status=in_progress', '?status=waiting', '?status=at_vendor', '?status=not_started',
            '?stagnant=1', '?vendor_late=1', '?status=waiting&stagnant=1', '?status=in_progress&stagnant=1',
            '?status=bogus&stagnant=maybe&vendor_late=2',
        )}
        self.assertEqual(list(sizes.values()), [7, 1, 1, 1, 4, 1, 1, 1, 0, 7])

    def test_title_placeholder_link_and_empty_message(self):
        self.scenario()
        self.staff()
        r = self.client.get('/boxes/?status=not_started')
        self.assertContains(r, 'Not linked yet')
        self.assertContains(r, 'href="/tags/"')
        self.assertContains(r, 'Back to Home')
        self.assertContains(self.client.get('/boxes/?status=waiting&stagnant=1'), 'Waiting, Stagnant')
        self.assertContains(self.client.get('/boxes/?status=in_progress&stagnant=1'), 'Nothing matches this filter.')

    def test_titles_say_what_the_list_shows(self):
        self.scenario()
        self.staff()
        for query, title in (('?status=in_progress', 'In progress'), ('?status=waiting', 'Waiting'), ('?status=at_vendor', 'At vendor'),
                             ('?status=not_started', 'Not started'), ('?stagnant=1', 'Stagnant'),
                             ('?status=at_vendor&vendor_late=1', 'Vendor overdue'), ('', 'Active boxes')):
            r = self.client.get('/boxes/' + query)
            self.assertEqual(r.context['title'], title)
            self.assertContains(r, f'<h1>{title}</h1>')
            self.assertContains(r, f'<title>{title} - Factory Tracker</title>')
        self.assertNotContains(self.client.get('/boxes/?status=waiting&stagnant=1'), '<h1>Boxes')

    def test_sort_options_and_fallback(self):
        self.scenario()
        other = Customer.objects.create(name='AAA')
        order = Order.objects.create(customer=other, due_date=timezone.localdate() + timedelta(days=30))
        OrderItem.objects.create(order=order, part_name='Z', quantity=1)
        self.staff()
        due = [(r['due_date'], r['order'].job_number) for r in self.rows('?sort=due')]
        self.assertEqual(due, sorted(due))
        self.assertEqual(self.rows('?sort=due')[0]['due_date'], timezone.localdate() - timedelta(days=2))
        procs = [r['process'].name if r['process'] else None for r in self.rows('?sort=process')]
        named = [p for p in procs if p]
        self.assertEqual((named, procs[len(named):]), (sorted(named), [None] * (len(procs) - len(named))))
        self.assertEqual(named[0], 'Cut')
        customers = [str(r['order'].customer) for r in self.rows('?sort=customer')]
        self.assertEqual((customers, customers[0]), (sorted(customers), 'AAA'))
        idles = [r['idle'] for r in self.rows('?sort=waiting')]
        known = [i for i in idles if i is not None]
        self.assertEqual((known, idles[len(known):]), (sorted(known, reverse=True), [None] * (len(idles) - len(known))))
        self.assertGreater(len(known), 1)
        for bad in ('?sort=bogus', '?sort='):
            self.assertEqual(self.client.get('/boxes/' + bad).context['sort'], 'due')
            key = lambda rows: [(r['item'].pk, r['tag_uid']) for r in rows]
            self.assertEqual(key(self.rows(bad)), key(self.rows('?sort=due')))

    def test_sort_control_keeps_filters(self):
        self.scenario()
        self.staff()
        r = self.client.get('/boxes/?status=waiting&stagnant=1&sort=customer')
        self.assertContains(r, '<input type="hidden" name="status" value="waiting">')
        self.assertContains(r, '<input type="hidden" name="stagnant" value="1">')
        self.assertContains(r, '<option value="customer" selected>')

    def test_board_page_is_gone(self):
        self.staff()
        self.assertEqual(self.client.get('/board/').status_code, 404)

    def test_home_counters_equal_page_rows(self):
        self.scenario()
        self.staff()
        home = self.client.get('/')
        links = {
            'in_progress': '/boxes/?status=in_progress', 'waiting': '/boxes/?status=waiting',
            'at_vendor': '/boxes/?status=at_vendor', 'not_started': '/boxes/?status=not_started',
            'stagnant': '/boxes/?stagnant=1', 'vendor_overdue': '/boxes/?status=at_vendor&vendor_late=1',
        }
        for key, url in links.items():
            self.assertContains(home, f'href="{url.replace("&", "&amp;")}"')
            self.assertEqual(home.context['counters'][key], len(self.rows(url[len('/boxes/'):])), key)
        self.assertContains(home, 'href="/orders/?late=1"')
        late_orders = self.client.get('/orders/?late=1').context['rows']
        self.assertEqual(home.context['counters']['overdue'], len(late_orders))
        self.assertEqual(home.context['counters']['not_started'], 4)


class ProcessBoardTests(OverviewBase):
    def build(self):
        self.item([self.cut], uids=['R1'])
        self.scan('R1', self.cut, E.STARTED, 5)
        self.item([self.cut], uids=['R2'])
        self.scan('R2', self.cut, E.STARTED, 2)
        for uid, idle in (('W1', 10), ('W2', 4), ('W3', 7)):
            self.item([self.cut, self.weld], uids=[uid])
            self.scan(uid, self.cut, E.STARTED, idle + 1)
            self.scan(uid, self.cut, E.FINISHED, idle)
        self.item([self.paint], uids=['V1'])
        self.scan('V1', self.paint, E.SENT_OUT, 6)

    def test_running_waiting_split_sorting_and_queue(self):
        self.build()
        cards = {c['process'].name: c for c in process_board(self.now)}
        self.assertEqual(sorted(cards), ['Cut', 'Paint', 'Weld'])  # material is skipped
        cut, weld, paint = cards['Cut'], cards['Weld'], cards['Paint']
        self.assertEqual([b['tag_uid'] for b in cut['running']], ['R1', 'R2'])  # longest first
        self.assertEqual(cut['running'][0]['running_for'], timedelta(hours=5))
        self.assertEqual((cut['waiting'], cut['queue']), ([], False))
        self.assertEqual(weld['running'], [])
        self.assertEqual([b['tag_uid'] for b in weld['waiting']], ['W1', 'W3', 'W2'])  # longest idle first
        self.assertTrue(weld['queue'])
        self.assertEqual(([b['tag_uid'] for b in paint['running']], paint['outsourced']), (['V1'], True))


class InactiveProcessBoardTests(OverviewBase):
    def test_board_hides_an_unused_inactive_process_but_not_one_with_boxes(self):
        unused = Process.objects.create(name='Unused', kind=K.MACHINING, active=False)
        self.item([self.cut, self.weld], uids=['W1'])
        self.scan('W1', self.cut, E.STARTED, 3)
        self.scan('W1', self.cut, E.FINISHED, 2)  # waiting at Weld
        self.weld.active = False
        self.weld.save()
        names = [c['process'].name for c in process_board(self.now)]
        self.assertIn('Weld', names)
        self.assertNotIn('Unused', names)
        self.assertIn('Cut', names)
        self.staff()
        r = self.client.get('/')
        self.assertContains(r, 'Weld')
        self.assertNotContains(r, unused.name)
        self.weld.active = True
        self.weld.save()

    def test_inactive_process_without_boxes_is_hidden_when_idle(self):
        self.weld.active = False
        self.weld.save()
        self.assertNotIn('Weld', [c['process'].name for c in process_board(self.now)])

    def test_simulate_confirm_accepts_an_inactive_process_in_the_route_only(self):
        item = self.item([self.cut], uids=['S1'])
        other = Process.objects.create(name='Other', kind=K.MACHINING, active=False)
        self.cut.active = False
        self.cut.save()
        self.staff()
        post = lambda proc: self.client.post('/simulate/', {'tag_uid': 's1', 'device': self.device.pk, 'action': f'{proc.pk}:STARTED'})
        self.assertEqual(post(other).status_code, 200)  # inactive and not in the route
        self.assertEqual(post(self.cut).status_code, 302)  # inactive but in the route
        self.assertEqual(Event.objects.get().process, self.cut)
        self.assertEqual(item.steps.count(), 1)


class ReplaceTagTests(OverviewBase):
    """Lost-tag replacement, box = (item, box) pair, next_action and the simulate page."""

    def setUp(self):
        super().setUp()
        self.item1 = self.item([self.mat, self.cut], uids=['OLD'])
        self.old = self.item1.tagassignment_set.get()

    def test_status_continues_across_a_replaced_tag(self):
        self.scan('OLD', self.mat, E.ARRIVED, 3)
        self.scan('OLD', self.cut, E.STARTED, 2)
        new = replace_tag(self.old, 'new')
        Event.objects.create(device=self.device, tag_uid='NEW', event_type=E.FINISHED, process=self.cut)
        st = item_status(self.item1)
        self.assertEqual((len(st.boxes), st.code, st.warnings), (1, COMPLETE, []))
        self.assertEqual(st.boxes[0].assignment, new)
        self.assertEqual(len(st.boxes[0].assignments), 2)

    def test_replacement_keeps_history_and_links_the_old_tag(self):
        new = replace_tag(self.old, ' new ')
        self.old.refresh_from_db()
        self.assertEqual((new.tag.uid, new.replaces, new.item, new.end_at), ('NEW', self.old, self.item1, None))
        self.assertEqual(self.old.end_at, new.start_at)
        self.assertEqual(TagAssignment.objects.count(), 2)

    def test_placeholders_count_distinct_boxes(self):
        item = self.item([self.cut], boxes=2, uids=['X1'])
        replace_tag(item.tagassignment_set.get(), 'X2')  # two assignments, still one box
        st = item_status(item)
        self.assertEqual(len(st.boxes), 2)
        self.assertIsNone(st.boxes[1].assignment)

    def test_rejections(self):
        other = self.item([self.cut], uids=['USED'])
        Tag.objects.create(uid='DEAD', active=False)
        for uid, msg in (('old', 'must differ'), ('used', 'already in use'), ('dead', 'inactive'), ('  ', 'required')):
            with self.assertRaisesMessage(ValidationError, msg):
                replace_tag(self.old, uid)
        self.assertEqual((TagAssignment.objects.filter(end_at__isnull=True).count(), other.tagassignment_set.count()), (2, 1))
        replace_tag(self.old, 'fresh')
        with self.assertRaisesMessage(ValidationError, 'already ended'):
            replace_tag(self.old, 'again')

    def test_lost_flag_and_old_tag_becomes_unusable(self):
        replace_tag(self.old, 'n1')
        self.old.tag.refresh_from_db()
        self.assertFalse(self.old.tag.active)
        with self.assertRaisesMessage(ValidationError, 'inactive'):
            assign_tag(self.item([self.cut]), 'OLD')
        a = self.item([self.cut], uids=['KEEP']).tagassignment_set.get()
        replace_tag(a, 'n2', mark_lost=False)
        a.tag.refresh_from_db()
        self.assertTrue(a.tag.active)

    def test_tags_page_search_and_replace(self):
        self.item([self.cut], uids=['OTHER'])
        self.staff()
        uids = lambda q: [a.tag.uid for a in self.client.get('/tags/', {'q': q}).context['open']]
        self.assertEqual(uids(''), [])  # no query: no rows
        self.assertEqual(uids('old'), ['OLD'])
        self.assertEqual(uids('other'), ['OTHER'])
        self.assertEqual(uids(self.item1.part_name), ['OLD'])
        self.assertEqual(uids(self.item1.order.job_number), ['OLD'])
        self.assertEqual(uids('nothing'), [])
        r = self.client.post('/tags/', {'action': 'replace', 'assignment': self.old.pk, 'new_uid': 'brandnew', 'mark_lost': 'on'}, follow=True)
        self.assertContains(r, 'Tag OLD replaced by BRANDNEW')
        self.assertFalse(Tag.objects.get(uid='OLD').active)
        r = self.client.post('/tags/', {'action': 'replace', 'assignment': self.item1.tagassignment_set.last().pk, 'new_uid': 'other'}, follow=True)
        self.assertContains(r, 'already in use')
        self.assertContains(self.client.get('/tags/', {'q': 'other'}), 'Mark the old tag as lost')

    def test_replace_without_checkbox_keeps_tag_active(self):
        self.staff()
        self.client.post('/tags/', {'action': 'replace', 'assignment': self.old.pk, 'new_uid': 'z9'})
        self.assertTrue(Tag.objects.get(uid='OLD').active)

    def box(self, item):
        return item_status(item).boxes[0]

    def test_next_action_for_every_state(self):
        a = self.item([self.mat, self.cut, self.paint], uids=['N1'])
        self.assertEqual(next_action(self.box(a)), (E.ARRIVED, self.mat))
        self.scan('N1', self.mat, E.ARRIVED, 5)
        self.assertEqual(next_action(self.box(a)), (E.STARTED, self.cut))
        self.scan('N1', self.cut, E.STARTED, 4)
        self.assertEqual(next_action(self.box(a)), (E.FINISHED, self.cut))
        self.scan('N1', self.cut, E.FINISHED, 3)
        self.assertEqual(next_action(self.box(a)), (E.SENT_OUT, self.paint))
        self.scan('N1', self.paint, E.SENT_OUT, 2)
        self.assertEqual(next_action(self.box(a)), (E.RETURNED, self.paint))
        self.scan('N1', self.paint, E.RETURNED, 1)
        self.assertIsNone(next_action(self.box(a)))
        self.assertIsNone(next_action(self.box(self.item([], uids=['N2']))))  # no route

    def test_simulate_shows_only_the_next_action(self):
        self.staff()
        r = self.client.get('/simulate/', {'tag': 'old', 'device': self.device.pk})
        self.assertEqual(r.context['step']['label'], 'Arrived - Mat')
        self.assertContains(r, '<option value="%d:ARRIVED" selected>Arrived - Mat</option>' % self.mat.pk)
        self.assertNotContains(r, 'Other action')
        self.assertNotContains(r, 'Record event')
        self.assertNotContains(r, 'tag-uids')
        self.assertContains(r, f'OLD - {self.item1.item_code} - {self.item1.part_name}')
        self.assertContains(r, 'Confirm')
        r = self.client.post('/simulate/', {'tag_uid': 'OLD', 'device': self.device.pk, 'action': r.context['step']['value']})
        self.assertRedirects(r, '/simulate/')
        self.assertEqual(Event.objects.get().event_type, 'ARRIVED')

    def test_simulate_finished_and_unassigned(self):
        self.staff()
        self.scan('OLD', self.mat, E.ARRIVED, 3)
        self.scan('OLD', self.cut, E.STARTED, 2)
        self.scan('OLD', self.cut, E.FINISHED, 1)
        r = self.client.get('/simulate/', {'tag': 'OLD', 'device': self.device.pk})
        self.assertEqual(r.context['step']['state'], 'finished')
        self.assertContains(r, 'Nothing left to scan')
        self.assertNotContains(r, 'id="next-action"')
        r = self.client.get('/simulate/', {'tag': 'ghost', 'device': self.device.pk})
        self.assertEqual(r.context['step']['state'], 'unassigned')
        self.assertContains(r, 'is not linked to any item.')
        self.assertContains(r, 'href="/tags/"')
        self.assertNotContains(r, 'Other action')

    def test_simulate_page_has_no_other_action_section(self):
        self.staff()
        r = self.client.get('/simulate/')
        for text in ('Other action', 'test a mistake', 'Record event', '<details'):
            self.assertNotContains(r, text)

    def test_simulate_invalid_post_shows_errors_and_saves_nothing(self):
        self.staff()
        r = self.client.post('/simulate/', {'tag_uid': 'OLD', 'device': self.device.pk, 'action': '999:ARRIVED'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'errorlist')
        self.assertFalse(Event.objects.exists())


class TagInfoApiTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='Scanner')
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mach = Process.objects.create(name='Mill', kind=K.MACHINING)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=date(2026, 12, 1),
                                     customer_order_number='PO-9')
        self.item = OrderItem.objects.create(order=order, part_name='Gear', quantity=4, material='S45C',
                                             drawing_number='DWG-1', revision='B', part_number='PN-7')
        for n, p in enumerate((self.mat, self.mach), 1):
            RouteStep.objects.create(item=self.item, sequence=n, process=p)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB12'), item=self.item)

    def get(self, uid='ab12', key='default'):
        headers = {'HTTP_X_API_KEY': self.device.api_key if key == 'default' else key}
        return self.client.get(f'/api/tag/{uid}/', **headers)

    def test_needs_key_and_known_tag(self):
        self.assertEqual(self.get(key='nope').status_code, 401)
        r = self.get('ZZ')
        self.assertEqual((r.status_code, r.json()), (404, {'ok': False, 'error': 'unassigned_tag'}))

    def test_shows_the_next_step_and_saves_nothing(self):
        j = self.get().json()
        self.assertEqual({k: j[k] for k in ('state', 'tag_uid', 'item', 'part', 'customer', 'order_number', 'drawing', 'material',
                                            'part_number', 'due', 'step', 'steps', 'process', 'event_type')},
                         {'state': 'open', 'tag_uid': 'AB12', 'item': self.item.item_code, 'part': 'Gear', 'customer': 'C',
                          'order_number': 'PO-9', 'drawing': 'DWG-1 / B', 'material': 'S45C', 'part_number': 'PN-7',
                          'due': '2026-12-01', 'step': 1, 'steps': 2, 'process': 'Mat', 'event_type': 'ARRIVED'})
        self.assertFalse(Event.objects.exists())
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_follows_the_route_to_the_end(self):
        record_event(self.device, 'AB12', self.mat, E.ARRIVED)
        j = self.get().json()
        self.assertEqual((j['step'], j['process'], j['event_type']), (2, 'Mill', 'STARTED'))
        record_event(self.device, 'AB12', self.mach, E.STARTED)
        self.assertEqual(self.get().json()['event_type'], 'FINISHED')
        record_event(self.device, 'AB12', self.mach, E.FINISHED)
        j = self.get().json()
        self.assertEqual((j['state'], j['step'], j['steps'], j['process'], j['event_type']), ('finished', 2, 2, '', ''))


class ScanApiTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='D')
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mach = Process.objects.create(name='Mach', kind=K.MACHINING)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.now().date())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        RouteStep.objects.create(item=self.item, sequence=1, process=self.mat)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB12'), item=self.item)

    def post(self, body=None, key='default', **kw):
        body = {'tag_uid': 'ab12', 'process': self.mat.pk, 'event_type': 'ARRIVED'} if body is None else body
        headers = {'HTTP_X_API_KEY': self.device.api_key if key == 'default' else key} if key is not None else {}
        data = body if isinstance(body, str) else json.dumps(body)
        return self.client.post('/api/scan/', data, content_type='application/json', **headers, **kw)

    def test_no_key(self):
        r = self.post(key=None)
        self.assertEqual((r.status_code, r.json()), (401, {'ok': False, 'error': 'bad_key'}))

    def test_wrong_key(self):
        self.assertEqual(self.post(key='nope').status_code, 401)

    def test_inactive_device(self):
        self.device.active = False
        self.device.save()
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(Event.objects.count(), 0)

    def test_valid_scan(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertEqual((j['ok'], j['tag_uid'], j['item']), (True, 'AB12', self.item.item_code))
        self.assertEqual(j['event_id'], Event.objects.get().pk)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_unassigned_tag(self):
        r = self.post({'tag_uid': 'ZZ', 'process': self.mat.pk, 'event_type': 'ARRIVED'})
        self.assertEqual((r.status_code, r.json()), (404, {'ok': False, 'error': 'unassigned_tag'}))
        self.assertEqual(Event.objects.count(), 0)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_wrong_process_kind(self):
        r = self.post({'tag_uid': 'AB12', 'process': self.mach.pk, 'event_type': 'ARRIVED'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'))
        self.assertEqual(Event.objects.count(), 0)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get('/api/scan/', HTTP_X_API_KEY=self.device.api_key).status_code, 405)

    def test_bad_json(self):
        r = self.post('{not json')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'))

    def auto(self, **kw):
        return self.post({'tag_uid': 'ab12'}, **kw)

    def add_machining_step(self):
        RouteStep.objects.create(item=self.item, sequence=2, process=self.mach)

    @override_settings(SCAN_REPEAT_SECONDS=0)
    def test_auto_saves_next_steps_in_order(self):
        self.add_machining_step()
        j = self.auto().json()
        self.assertEqual((j['state'], j['event_type'], j['process_id'], j['process_name']), ('recorded', 'ARRIVED', self.mat.pk, 'Mat'))
        j = self.auto().json()
        self.assertEqual((j['state'], j['event_type'], j['process_id']), ('recorded', 'STARTED', self.mach.pk))
        self.assertEqual(Event.objects.count(), 2)

    @override_settings(SCAN_REPEAT_SECONDS=0)
    def test_auto_finished_box_saves_nothing(self):
        self.auto()
        r = self.auto()
        self.assertEqual((r.status_code, r.json()['state']), (200, 'finished'))
        self.assertEqual(Event.objects.count(), 1)

    @override_settings(SCAN_REPEAT_SECONDS=60)
    def test_auto_duplicate_inside_window(self):
        self.add_machining_step()
        self.auto()
        r = self.auto()
        self.assertEqual((r.status_code, r.json()['state']), (200, 'duplicate'))
        self.assertEqual(Event.objects.count(), 1)
        self.assertEqual(self.post().json()['state'], 'recorded')

    def test_only_one_of_process_or_event_type(self):
        for body in ({'tag_uid': 'AB12', 'process': self.mat.pk}, {'tag_uid': 'AB12', 'event_type': 'ARRIVED'}):
            r = self.post(body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'))
        self.assertEqual(Event.objects.count(), 0)

    def test_explicit_mode_reply(self):
        j = self.post().json()
        self.assertEqual((j['state'], j['event_type'], j['process_id']), ('recorded', 'ARRIVED', self.mat.pk))

    def test_auto_unassigned_tag(self):
        r = self.post({'tag_uid': 'ZZ'})
        self.assertEqual((r.status_code, r.json()['error']), (404, 'unassigned_tag'))
        self.assertEqual(Event.objects.count(), 0)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_auto_bad_key(self):
        self.assertEqual(self.auto(key='nope').status_code, 401)


class SetterApiTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='Setter')
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=date(2026, 12, 1))
        self.one = OrderItem.objects.create(order=order, part_name='One', quantity=1)
        self.two = OrderItem.objects.create(order=order, part_name='Two', quantity=1, box_count=2)
        self.key = {'HTTP_X_API_KEY': self.device.api_key}

    def items(self, query='', **headers):
        return self.client.get('/api/assignable-items/' + query, **(headers or self.key))

    def assign(self, body, **headers):
        data = body if isinstance(body, str) else json.dumps(body)
        return self.client.post('/api/assign/', data, content_type='application/json', **(headers or self.key))

    def test_list_needs_key(self):
        self.assertEqual(self.items(HTTP_X_API_KEY='nope').status_code, 401)
        self.device.active = False
        self.device.save()
        self.assertEqual(self.items().status_code, 401)

    def test_list_shows_items_with_free_slots(self):
        assign_tag(self.one, 'A1')
        assign_tag(self.two, 'B1')
        j = self.items().json()
        self.assertEqual((j['ok'], j['total'], j['offset']), (True, 1, 0))
        self.assertEqual(j['items'], [{
            'id': self.two.pk, 'item': self.two.item_code, 'part': 'Two', 'customer': 'C',
            'due': '2026-12-01', 'linked': 1, 'boxes': 2, 'quantity': 1, 'order_number': '',
            'ordered': timezone.localdate(self.two.order.created_at).isoformat(), 'steps': 0,
        }])
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_list_counts_route_steps_and_shows_the_order_number(self):
        order = self.one.order
        order.customer_order_number = 'PO-7'
        order.save()
        for n, name in enumerate(('Mill', 'Lathe'), 1):
            RouteStep.objects.create(item=self.one, sequence=n, process=Process.objects.create(name=name, kind=K.MACHINING))
        one = self.items().json()['items'][0]
        self.assertEqual((one['id'], one['steps'], one['order_number']), (self.one.pk, 2, 'PO-7'))
        self.assertEqual(self.items().json()['items'][1]['steps'], 0)

    def test_list_pages(self):
        j = self.items('?offset=1&limit=1').json()
        self.assertEqual((j['total'], [i['id'] for i in j['items']]), (2, [self.two.pk]))
        for q in ('?limit=0', '?limit=51', '?offset=-1', '?limit=x'):
            self.assertEqual((self.items(q).status_code, self.items(q).json()['error']), (400, 'invalid'), q)

    def test_assign_links_tag(self):
        r = self.assign({'tag_uid': ' ab12 ', 'item': self.two.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True, 'state': 'assigned', 'tag_uid': 'AB12', 'item': self.two.item_code, 'linked': 1, 'boxes': 2, 'print': 'queued'})
        a = TagAssignment.objects.get(end_at__isnull=True)
        self.assertEqual(a.item, self.two)
        job = PrintJob.objects.get()
        self.assertEqual((job.item, job.assignment, job.source, job.status), (self.two, a, PrintJob.Source.ASSIGN, PrintJob.Status.PENDING))
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_assign_needs_key(self):
        self.assertEqual(self.assign({'tag_uid': 'AB', 'item': self.one.pk}, HTTP_X_API_KEY='nope').status_code, 401)
        self.assertFalse(TagAssignment.objects.exists())

    def test_assign_refused_cases(self):
        assign_tag(self.one, 'USED')
        Tag.objects.create(uid='OFF', active=False)
        for body in ({'tag_uid': 'USED', 'item': self.two.pk}, {'tag_uid': 'OFF', 'item': self.two.pk}, {'tag_uid': 'NEW', 'item': self.one.pk}):
            r = self.assign(body)
            self.assertEqual((r.status_code, r.json()['error']), (409, 'refused'), body)
        self.assertEqual(TagAssignment.objects.count(), 1)
        self.assertFalse(PrintJob.objects.exists())

    def test_refusals_say_why(self):
        assign_tag(self.one, 'USED')
        Tag.objects.create(uid='OFF', active=False)
        cases = [
            ({'tag_uid': 'USED', 'item': self.two.pk}, {'reason': 'tag_in_use', 'current_item': self.one.item_code}),
            ({'tag_uid': 'OFF', 'item': self.two.pk}, {'reason': 'tag_inactive'}),
            ({'tag_uid': 'NEW', 'item': self.one.pk}, {'reason': 'item_full'}),
        ]
        for body, want in cases:
            j = self.assign(body).json()
            self.assertEqual({k: j.get(k) for k in want}, want, body)

    def test_replace_moves_the_tag_to_the_new_item(self):
        old = assign_tag(self.one, 'USED')
        r = self.assign({'tag_uid': 'USED', 'item': self.two.pk, 'replace': True})
        self.assertEqual((r.status_code, r.json()['released'], r.json()['item']), (200, self.one.item_code, self.two.item_code))
        old.refresh_from_db()
        self.assertIsNotNone(old.end_at)
        self.assertEqual(TagAssignment.objects.get(end_at__isnull=True).item, self.two)
        self.assertEqual(PrintJob.objects.get().item, self.two)

    def test_replace_is_undone_when_the_new_item_is_full(self):
        assign_tag(self.one, 'A1')
        old = assign_tag(self.two, 'USED')
        r = self.assign({'tag_uid': 'USED', 'item': self.one.pk, 'replace': True})
        self.assertEqual((r.status_code, r.json()['reason']), (409, 'item_full'))
        old.refresh_from_db()
        self.assertIsNone(old.end_at)

    def test_replace_must_be_true_not_just_truthy(self):
        assign_tag(self.one, 'USED')
        r = self.assign({'tag_uid': 'USED', 'item': self.two.pk, 'replace': 'yes'})
        self.assertEqual((r.status_code, r.json()['reason']), (409, 'tag_in_use'))

    def test_failed_print_queue_rolls_back_the_link(self):
        with mock.patch('tracking.api.queue_print', side_effect=RuntimeError('db down')), self.assertRaises(RuntimeError):
            self.assign({'tag_uid': 'AB12', 'item': self.two.pk})
        self.assertFalse(TagAssignment.objects.exists())

    def test_assign_bad_requests(self):
        for body in ('{bad', {'item': self.one.pk}, {'tag_uid': ' ', 'item': self.one.pk}, {'tag_uid': 'AB'},
                     {'tag_uid': 'AB', 'item': '1'}, {'tag_uid': 'AB', 'item': True}, {'tag_uid': 'AB', 'item': 999999}):
            r = self.assign(body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid'), body)
        self.assertFalse(TagAssignment.objects.exists())

    def test_wrong_methods(self):
        self.assertEqual(self.client.get('/api/assign/', **self.key).status_code, 405)
        self.assertEqual(self.client.post('/api/assignable-items/', **self.key).status_code, 405)

    def test_assigned_tag_then_scans(self):
        self.assign({'tag_uid': 'AB12', 'item': self.one.pk})
        r = self.client.post('/api/scan/', json.dumps({'tag_uid': 'AB12'}), content_type='application/json', **self.key)
        self.assertNotEqual(r.status_code, 404)


class CorrectEventTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mach = Process.objects.create(name='Mach', kind=K.MACHINING)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=self.now.date())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        for i, p in enumerate((self.mat, self.mach), 1):
            RouteStep.objects.create(item=self.item, sequence=i, process=p)
        self.start = self.now - timedelta(hours=10)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB12'), item=self.item, start_at=self.start)
        self.device = ScannerDevice.objects.create(name='D')
        self.event = Event.objects.create(device=self.device, tag_uid='AB12', event_type=E.ARRIVED, process=self.mat, occurred_at=self.start + timedelta(hours=1))
        self.user = make_user('planner', 'Planner')

    def code(self):
        return item_status(self.item).code

    def test_void_is_ignored_by_status(self):
        self.assertEqual(self.code(), WAITING)
        correction, new = correct_event(self.event, self.user, 'wrong scan')
        self.assertIsNone(new)
        self.assertEqual((correction.corrects, correction.created_by, correction.process), (self.event, self.user, None))
        self.assertEqual(self.code(), NOT_STARTED)

    def test_void_and_replace_keeps_original_time(self):
        _, new = correct_event(self.event, self.user, 'wrong type', {'process': self.mach, 'event_type': E.STARTED})
        self.assertEqual((new.occurred_at, new.is_manual, new.reason, new.created_by), (self.event.occurred_at, True, 'wrong type', self.user))
        self.assertEqual(self.code(), IN_PROGRESS)

    def test_reason_required(self):
        with self.assertRaises(ValidationError):
            correct_event(self.event, self.user, '  ')
        self.assertEqual(Event.objects.count(), 1)

    def test_manual_event_needs_reason(self):
        with self.assertRaises(ValidationError):
            Event(device=self.device, tag_uid='AB12', event_type=E.ARRIVED, process=self.mat, is_manual=True).full_clean()

    def test_double_void_refused(self):
        correct_event(self.event, self.user, 'x')
        with self.assertRaises(ValidationError):
            correct_event(self.event, self.user, 'again')
        self.assertEqual(Event.objects.count(), 2)

    def test_correction_cannot_be_corrected(self):
        correction, _ = correct_event(self.event, self.user, 'x')
        with self.assertRaises(ValidationError):
            correct_event(correction, self.user, 'y')

    def test_user_without_permission_refused(self):
        other = make_user('op', 'Operator')
        with self.assertRaises(PermissionDenied):
            correct_event(self.event, other, 'x')
        self.assertEqual(Event.objects.count(), 1)

    def test_superuser_allowed(self):
        root = User.objects.create_superuser('root', password='x')
        correct_event(self.event, root, 'x')

    def test_original_row_unchanged(self):
        before = Event.objects.filter(pk=self.event.pk).values().get()
        correct_event(self.event, self.user, 'x', {'process': self.mat, 'event_type': E.ARRIVED})
        self.assertEqual(Event.objects.filter(pk=self.event.pk).values().get(), before)

    def test_correct_page_flow(self):
        self.client.force_login(self.user)
        url = f'/events/{self.event.pk}/correct/'
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'reason': '', 'mode': 'void'})
        self.assertEqual((r.status_code, Event.objects.count()), (200, 1))
        r = self.client.post(url, {'reason': 'oops', 'mode': 'replace', 'replacement': f'{self.mach.pk}:STARTED'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Event.objects.count(), 3)

    def test_correct_page_forbidden_without_permission(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get(f'/events/{self.event.pk}/correct/').status_code, 403)

    def test_detail_shows_voided_and_manual(self):
        correct_event(self.event, self.user, 'bad scan', {'process': self.mat, 'event_type': E.ARRIVED})
        self.client.force_login(self.user)
        html = self.client.get(f'/orders/{self.item.order.job_number}/').content.decode()
        self.assertIn('event-voided', html)
        self.assertIn('bad scan', html)
        self.assertIn('Manual', html)
        self.assertIn('Correct', html)


class ProgressMatrixTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.vendor = Vendor.objects.create(code='00131', name='Demo Plating', kind=Vendor.Kind.VENDOR)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plate', kind=K.OUTSOURCED, default_vendor=self.vendor)
        self.device = ScannerDevice.objects.create(name='D')
        self.acme, self.beta = Customer.objects.create(name='Acme'), Customer.objects.create(name='Beta')
        self.n = 0

    def make(self, customer=None, days=10, route=None, part=None):
        """An item due in `days` days, with a tag assigned; returns (item, uid)."""
        self.n += 1
        order = Order.objects.create(customer=customer or self.acme, due_date=timezone.localdate() + timedelta(days=days))
        item = OrderItem.objects.create(order=order, part_name=part or f'Part{self.n}', quantity=1)
        for i, proc in enumerate(route or [self.mat, self.mill, self.plate], 1):
            RouteStep.objects.create(item=item, sequence=i, process=proc, vendor=proc.default_vendor)
        uid = f'M{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item,
                                     start_at=timezone.now() - timedelta(days=1))
        return item, uid

    def scan(self, uid, proc, etype):
        record_event(self.device, uid, proc, etype)

    def rows(self, **kw):
        return build_matrix(**kw)['rows']

    def test_needs_operator_login(self):
        self.client.logout()
        self.assertRedirects(self.client.get('/'), '/login/?next=/')
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.get('/').status_code, 403)
        self.client.force_login(make_user('op'))
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_unfinished_items_by_due_date_and_complete_ones_left_out(self):
        later, _ = self.make(days=9)
        sooner, _ = self.make(days=2)
        done, uid = self.make(route=[self.mat])
        self.scan(uid, self.mat, 'ARRIVED')
        self.assertEqual([r['item'] for r in self.rows()], [sooner, later])

    def test_finished_leading_steps_collapse_into_one_cell(self):
        item, uid = self.make(route=[self.mat, self.mill, self.plate])
        self.scan(uid, self.mat, 'ARRIVED')
        self.scan(uid, self.mill, 'STARTED')
        self.scan(uid, self.mill, 'FINISHED')
        self.scan(uid, self.plate, 'SENT_OUT')
        cells = self.rows()[0]['cells']
        self.assertEqual([(c.get('run'), c['state']) for c in cells], [(2, 'done'), (None, 'at_vendor')])
        self.assertEqual((cells[1]['process'], cells[1]['vendor'], cells[1]['current']), (self.plate, 'Demo Plating', True))
        page = self.client.get('/')
        self.assertContains(page, 'colspan="2"')
        self.assertContains(page, 'Demo Plating')
        self.assertContains(page, f'data-href="/orders/{item.order.job_number}/"')

    def test_short_routes_are_padded_to_the_widest(self):
        self.make(days=1, route=[self.mat])
        self.make(days=2, route=[self.mat, self.mill, self.plate])
        data = build_matrix()
        self.assertEqual((len(data['columns']), [len(r['pad']) for r in data['rows']]), (3, [2, 0]))

    def test_start_at_current_step_drops_finished_steps(self):
        item, uid = self.make()
        self.scan(uid, self.mat, 'ARRIVED')
        self.scan(uid, self.mill, 'STARTED')
        row = self.rows(from_current=True)[0]
        self.assertEqual([(c['process'], c['current']) for c in row['cells']], [(self.mill, True), (self.plate, False)])
        self.assertEqual(row['width'], 2)
        page = self.client.get('/', {'current': '1'})
        self.assertContains(page, '>Now<')
        self.assertContains(page, '>+1<')

    def test_filters(self):
        a, uid_a = self.make(customer=self.acme, days=-2)
        b, _ = self.make(customer=self.beta, days=5)
        c, uid_c = self.make(customer=self.beta, days=20)
        self.scan(uid_a, self.mat, 'ARRIVED')
        self.scan(uid_c, self.mill, 'STARTED')
        items = lambda **kw: [r['item'] for r in self.rows(**kw)]
        self.assertEqual(items(status=NOT_STARTED), [b])
        self.assertEqual(items(status=IN_PROGRESS), [c])
        self.assertEqual(items(customer=self.beta.pk), [b, c])
        self.assertEqual(items(late=True), [a])
        self.assertEqual(items(due_from=timezone.localdate() + timedelta(days=3), due_to=timezone.localdate() + timedelta(days=10)), [b])
        page = self.client.get('/', {'status': 'IN_PROGRESS', 'customer': self.beta.pk})
        self.assertContains(page, c.item_code)
        self.assertNotContains(page, b.item_code)
        self.assertEqual(self.client.get('/', {'status': 'bogus', 'customer': 'x', 'due_from': 'zz'}).status_code, 200)

    def test_show_more_pages_through_the_rows(self):
        items = [self.make(days=i)[0] for i in range(3)]
        data = build_matrix(limit=2)
        self.assertEqual(([r['item'] for r in data['rows']], data['more']), (items[:2], True))
        self.assertFalse(build_matrix(limit=3)['more'])
        with mock.patch('core.views.MATRIX_PAGE', 2):
            first = self.client.get('/', {'status': 'NOT_STARTED'})
            self.assertContains(first, 'Show more')
            self.assertContains(first, 'n=4')
            self.assertContains(first, 'status=NOT_STARTED')
            self.assertNotContains(first, items[2].item_code)
            self.assertNotContains(self.client.get('/', {'n': 4}), 'Show more')

    def test_old_matrix_url_redirects_home_and_keeps_the_filters(self):
        self.assertRedirects(self.client.get('/matrix/', {'status': 'PAUSED'}), '/?status=PAUSED')
        self.assertNotContains(self.client.get('/'), 'href="/matrix/"')

    def test_home_lists_the_most_urgent_first_in_a_scroll_panel(self):
        items = [self.make(days=i)[0] for i in range(12)]
        r = self.client.get('/')
        self.assertEqual([x['item'] for x in r.context['matrix']['rows']], items)
        self.assertContains(r, 'class="scroll-panel scroll-panel-first"')

    def test_every_state_has_its_own_icon_and_the_legend_is_shown(self):
        item, uid = self.make(route=[self.mat, self.mill, self.plate, self.mill, self.plate])
        self.scan(uid, self.mat, 'ARRIVED')
        html = self.client.get('/').content.decode()
        self.assertIn('class="matrix-legend"', html)
        for state in ('pending', 'in_progress', 'at_vendor', 'paused', 'done'):
            self.assertIn(f'matrix-chip matrix-{state}', html)
        icons = {}
        for state in ('pending', 'in_progress', 'at_vendor', 'paused', 'done'):
            icons[state] = render_to_string('includes/state_icon.html', {'state': state})
        self.assertEqual(len(set(icons.values())), 5)

    def test_empty_matrix(self):
        self.assertContains(self.client.get('/'), 'No unfinished items match.')


class ResultTests(TestCase):
    def setUp(self):
        self.operator = make_user('op', 'Operator')
        self.client.force_login(self.operator)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', name_ja='フライス', kind=K.MACHINING)
        self.device = ScannerDevice.objects.create(name='D')
        self.worker = Worker.objects.create(name='Sato Keiko', name_ja='佐藤 恵子')
        self.machine = Machine.objects.create(name='Mill 1')
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=9))
        self.item = OrderItem.objects.create(order=order, part_name='Shaft', quantity=10, box_count=2)
        for i, p in enumerate((self.mat, self.mill), 1):
            RouteStep.objects.create(item=self.item, sequence=i, process=p)
        self.a = TagAssignment.objects.create(tag=Tag.objects.create(uid='RA'), item=self.item,
                                              start_at=timezone.now() - timedelta(days=2))
        self.url = f'/items/{self.item.pk}/result/'
        self.detail = f'/orders/{order.job_number}/'

    def post(self, **kw):
        data = {'result%d-box' % self.item.pk: self.a.pk, 'result%d-process' % self.item.pk: self.mill.pk,
                'result%d-quantity' % self.item.pk: 10, 'result%d-defects' % self.item.pk: 1,
                'result%d-worker' % self.item.pk: self.worker.pk, 'result%d-machine' % self.item.pk: self.machine.pk}
        data.update({'result%d-%s' % (self.item.pk, k): v for k, v in kw.items()})
        return self.client.post(self.url, data)

    def results(self):
        return Event.objects.filter(event_type=E.RESULT)

    def test_result_event_rules(self):
        base = dict(device=self.device, tag_uid='ra', event_type=E.RESULT)
        Event(**base, process=self.mill, quantity=5).full_clean()
        for bad in (dict(quantity=5), dict(process=self.mill), dict(process=self.mill, quantity=0),
                    dict(process=self.mill, quantity=5, defects=6)):
            with self.assertRaises(ValidationError, msg=bad):
                Event(**base, **bad).full_clean()
        with self.assertRaises(ValidationError):
            Event(device=self.device, tag_uid='RA', event_type=E.STARTED, process=self.mill, worker=self.worker).full_clean()
        with self.assertRaises(ValidationError):
            Event(tag_uid='RA', event_type=E.RESULT, process=self.mill, quantity=1).full_clean()  # no device and no user
        e = Event(**base, process=self.mill, quantity=5)
        e.full_clean()
        self.assertEqual(e.defects, 0)

    def test_result_does_not_change_status_or_idle_time(self):
        record_event(self.device, 'RA', self.mat, 'ARRIVED', occurred_at=timezone.now() - timedelta(hours=30))
        before = item_status(self.item)
        self.assertEqual(self.post().status_code, 302)
        after = item_status(self.item)
        self.assertEqual((before.code, before.last_event_at, [r.state for r in before.rows]), (after.code, after.last_event_at, [r.state for r in after.rows]))
        self.assertFalse(events_for_item(self.item).filter(event_type=E.RESULT).exists())

    def test_operator_records_a_result_and_it_shows_in_the_history(self):
        r = self.post()
        self.assertEqual((r.status_code, r['Location']), (302, self.detail))
        e = self.results().get()
        self.assertEqual((e.tag_uid, e.process, e.quantity, e.defects, e.worker, e.machine, e.created_by, e.device),
                         ('RA', self.mill, 10, 1, self.worker, self.machine, self.operator, None))
        page = self.client.get(self.detail)
        self.assertContains(page, 'Result recorded.')
        self.assertContains(page, '10 done, 1 defects')
        self.assertContains(page, 'Sato Keiko')
        self.assertContains(page, 'Record result')

    def test_worker_and_machine_are_optional_and_bad_input_records_nothing(self):
        self.assertEqual(self.post(worker='', machine='', defects='').status_code, 302)
        e = self.results().get()
        self.assertEqual((e.worker, e.machine, e.defects), (None, None, 0))
        for bad in (dict(defects=11), dict(quantity=0), dict(quantity=''), dict(process=9999), dict(box=9999)):
            self.post(**bad)
        self.assertEqual(self.results().count(), 1)

    def test_error_message_for_defects_above_quantity(self):
        r = self.client.post(self.url, {'result%d-box' % self.item.pk: self.a.pk, 'result%d-process' % self.item.pk: self.mill.pk,
                                        'result%d-quantity' % self.item.pk: 3, 'result%d-defects' % self.item.pk: 5}, follow=True)
        self.assertContains(r, 'Defects cannot be more than the quantity done.')
        self.assertFalse(self.results().exists())

    def test_second_box_uses_its_own_tag(self):
        b = TagAssignment.objects.create(tag=Tag.objects.create(uid='RB'), item=self.item)
        self.post(box=b.pk)
        self.assertEqual(self.results().get().tag_uid, 'RB')
        self.assertContains(self.client.get(self.detail), 'Tag RB')

    def test_no_form_without_a_linked_box_and_roles(self):
        self.a.release()
        self.assertContains(self.client.get(self.detail), 'Link a tag to a box of this item first.')
        self.assertNotContains(self.client.get(self.detail), self.url)
        self.post()
        self.assertFalse(self.results().exists())
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(self.client.get('/analysis/?tab=results').status_code, 403)

    def test_planner_can_void_a_result_but_not_replace_it(self):
        self.post()
        e = self.results().get()
        self.client.force_login(make_user('pl', 'Planner'))
        url = f'/events/{e.pk}/correct/'
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Void and replace')
        with self.assertRaises(ValidationError):
            correct_event(e, make_user('pl'), 'why', {'process': self.mill, 'event_type': E.STARTED})
        self.assertRedirects(self.client.post(url, {'reason': 'typo', 'mode': 'void'}), '/orders/')
        self.assertTrue(Event.objects.filter(event_type=E.CORRECTION, corrects=e).exists())
        self.assertContains(self.client.get('/analysis/?tab=results'), 'No results recorded yet.')
        self.assertContains(self.client.get(self.detail), 'event-voided')

    def test_summary_totals_and_defect_rate_skip_voided_results(self):
        self.post(quantity=10, defects=1)
        self.post(quantity=30, defects=3)
        record_result = __import__('tracking.services', fromlist=['record_result']).record_result
        gone = record_result(self.operator, self.a, self.mat, 100, 50)
        correct_event(gone, make_user('pl', 'Planner'), 'oops')
        r = self.client.get('/analysis/?tab=results')
        row, = r.context['rows']
        self.assertEqual({k: row[k] for k in ('process', 'records', 'quantity', 'defects', 'rate')}, {'process': 'Mill', 'records': 2, 'quantity': 40, 'defects': 4, 'rate': 10.0})
        self.assertEqual((r.context['total']['quantity'], r.context['total']['rate']), (40, 10.0))
        self.assertContains(r, '10.0%')
        self.client.cookies['django_language'] = 'ja'
        self.assertEqual(self.client.get('/analysis/?tab=results').context['rows'][0]['process'], 'フライス')

    def test_scanner_api_does_not_accept_results_yet(self):
        r = self.client.post('/api/scan/', json.dumps({'tag_uid': 'RA', 'process': self.mill.pk, 'event_type': 'RESULT'}),
                             content_type='application/json', HTTP_X_API_KEY=self.device.api_key)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.results().exists())


class PlanVsActualTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plate', kind=K.OUTSOURCED)
        self.device = ScannerDevice.objects.create(name='D')
        self.today = timezone.localdate()
        self.now = timezone.now()
        self.n = 0

    def make(self, mill_due, plate_due=None, arrived=10, mill_done=6, plate_done=None):
        """Material arrived `arrived` days ago, milling finished `mill_done` days ago, plating optional; due dates are days from today."""
        self.n += 1
        order = Order.objects.create(customer=Customer.objects.create(name=f'C{self.n}'), due_date=self.today + timedelta(days=30))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=1)
        day = lambda n: self.today + timedelta(days=n) if n is not None else None
        for i, (p, due) in enumerate(((self.mat, None), (self.mill, day(mill_due)), (self.plate, day(plate_due))), 1):
            RouteStep.objects.create(item=item, sequence=i, process=p, due_date=due)
        uid = f'PV{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item,
                                     start_at=self.now - timedelta(days=20))
        ago = lambda d: self.now - timedelta(days=d)
        record_event(self.device, uid, self.mat, 'ARRIVED', occurred_at=ago(arrived))
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=ago(mill_done + 1))
        record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=ago(mill_done))
        if plate_done is not None:
            record_event(self.device, uid, self.plate, 'SENT_OUT', occurred_at=ago(plate_done + 2))
            record_event(self.device, uid, self.plate, 'RETURNED', occurred_at=ago(plate_done))
        return item

    def plan(self, item):
        return step_plan(item_status(item).rows)

    def test_planned_and_actual_days_count_from_the_previous_finish(self):
        item = self.make(mill_due=-7)  # arrived day -10, due day -7, finished day -6
        first, second, third = self.plan(item)
        self.assertEqual((first['planned'], first['actual']), (None, None))
        self.assertEqual(second, {'planned': 3, 'actual': 4, 'diff': 1})
        self.assertEqual(third, {'planned': None, 'actual': None, 'diff': None})

    def test_unfinished_and_undated_steps_have_no_actual_or_plan(self):
        item = self.make(mill_due=None)
        self.assertEqual(self.plan(item)[1], {'planned': None, 'actual': 4, 'diff': None})
        item = self.make(mill_due=-7, plate_due=-1)  # plating not done yet: plan only
        self.assertEqual(self.plan(item)[2], {'planned': 5, 'actual': None, 'diff': None})

    def test_third_step_counts_from_the_second_finish(self):
        item = self.make(mill_due=-7, plate_due=-2, plate_done=3)  # milling finished day -6, plating day -3
        self.assertEqual(self.plan(item)[2], {'planned': 4, 'actual': 3, 'diff': -1})

    def test_order_page_shows_plan_and_actual_with_the_overrun(self):
        item = self.make(mill_due=-7)
        page = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(page, 'Plan / actual (days)')
        self.assertContains(page, '3 / 4')
        self.assertContains(page, '+1')

    def test_average_page_per_process(self):
        self.make(mill_due=-7)                    # planned 3, actual 4
        self.make(mill_due=-8, mill_done=7)       # planned 2, actual 3
        self.make(mill_due=-5, mill_done=7)       # planned 5, actual 3
        self.make(mill_due=None)                  # no plan: not counted
        r = self.client.get('/analysis/?tab=plan')
        row, = r.context['rows']
        self.assertEqual((row['process'], row['n'], row['planned'], row['actual'], row['diff']), (self.mill, 3, 10 / 3, 10 / 3, 0))
        self.assertContains(r, '3.3')

    def test_empty_and_roles(self):
        self.assertContains(self.client.get('/analysis/?tab=plan'), 'No finished steps with a due date yet.')
        self.client.logout()
        self.assertEqual(self.client.get('/analysis/?tab=plan').status_code, 302)
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.get('/analysis/?tab=plan').status_code, 403)


class StepStateTests(TestCase):
    """Paused, resumed, partial and cancelled: planner actions that never come from the scanner."""

    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.plate = Process.objects.create(name='Plate', kind=K.OUTSOURCED, vendor_lead_days=2)
        self.device = ScannerDevice.objects.create(name='D')
        self.n = 0

    def make(self, route=None, days=10, order=None):
        self.n += 1
        order = order or Order.objects.create(customer=Customer.objects.create(name=f'C{self.n}'), due_date=timezone.localdate() + timedelta(days=days))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=3)
        for i, p in enumerate(route or [self.mat, self.mill, self.plate], 1):
            RouteStep.objects.create(item=item, sequence=i, process=p)
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid=f'ST{self.n}'), item=item,
                                         start_at=timezone.now() - timedelta(days=20))
        return item, a

    def scan(self, a, proc, etype, days=0, hours=0):
        record_event(self.device, a.tag.uid, proc, etype, occurred_at=timezone.now() - timedelta(days=days, hours=hours))

    def code(self, item):
        return item_status(item).code

    def running(self, days=1):
        item, a = self.make()
        self.scan(a, self.mat, 'ARRIVED', days=days + 1)
        self.scan(a, self.mill, 'STARTED', days=days)
        return item, a

    # ----- events -----
    def test_event_rules(self):
        item, a = self.running()
        ok = dict(created_by=self.planner, tag_uid=a.tag.uid)
        Event(**ok, event_type=E.PAUSED, process=self.mill).full_clean()
        with self.assertRaises(ValidationError):
            Event(**ok, event_type=E.PAUSED).full_clean()  # needs a process
        with self.assertRaises(ValidationError):
            Event(created_by=self.planner, event_type=E.PAUSED, process=self.mill).full_clean()  # needs a tag
        with self.assertRaises(ValidationError):
            Event(created_by=self.planner, event_type=E.CANCELLED, item=item).full_clean()  # needs a reason
        with self.assertRaises(ValidationError):
            Event(created_by=self.planner, event_type=E.CANCELLED, reason='x').full_clean()  # needs an item
        with self.assertRaises(ValidationError):
            Event(created_by=self.planner, event_type=E.CANCELLED, reason='x', item=item, process=self.mill).full_clean()
        with self.assertRaises(ValidationError):
            Event(**ok, event_type=E.PAUSED, process=self.mill, item=item).full_clean()  # only a cancellation names an item
        Event(created_by=self.planner, event_type=E.CANCELLED, reason='x', item=item).full_clean()

    # ----- paused / resumed -----
    def test_pause_and_resume_change_the_box_status(self):
        item, a = self.running()
        self.assertEqual(self.code(item), 'IN_PROGRESS')
        record_step_state(self.planner, a, E.PAUSED)
        st = item_status(item)
        self.assertEqual((st.code, st.label, st.boxes[0].code, st.rows[1].paused, st.rows[1].state), ('PAUSED', 'Paused', 'PAUSED', True, 'in_progress'))
        record_step_state(self.planner, a, E.RESUMED)
        st = item_status(item)
        self.assertEqual((st.code, st.rows[1].paused), ('IN_PROGRESS', False))

    def test_paused_boxes_are_not_stagnant_or_vendor_late(self):
        item, a = self.running(days=6)
        self.assertTrue(is_stagnant(item_status(item)))
        record_step_state(self.planner, a, E.PAUSED)
        self.assertFalse(is_stagnant(item_status(item)))
        out, b = self.make(route=[self.plate])
        self.scan(b, self.plate, 'SENT_OUT', days=10)
        late = lambda: [e['late_days'] for e in active_boxes() if e['item'] == out]
        self.assertGreater(late()[0], 0)
        record_step_state(self.planner, b, E.PAUSED)
        self.assertEqual(late(), [0])
        record_step_state(self.planner, b, E.RESUMED)
        self.assertGreater(late()[0], 0)

    def test_finishing_a_paused_step_completes_it(self):
        item, a = self.running()
        record_step_state(self.planner, a, E.PAUSED)
        self.scan(a, self.mill, 'FINISHED')
        row = item_status(item).rows[1]
        self.assertEqual((row.state, row.paused), ('done', False))

    def test_only_valid_actions_are_offered(self):
        item, a = self.make()
        box = lambda: item_status(item).boxes[0]
        self.assertEqual(manual_actions(box()), [])  # nothing started
        self.scan(a, self.mat, 'ARRIVED', days=2)
        self.assertEqual(manual_actions(box()), [])  # waiting
        self.scan(a, self.mill, 'STARTED', days=1)
        self.assertEqual([t for t, _p in manual_actions(box())], [E.PAUSED, E.PARTIAL])
        record_step_state(self.planner, a, E.PAUSED)
        self.assertEqual([t for t, _p in manual_actions(box())], [E.RESUMED])
        for bad in (E.PAUSED, E.PARTIAL):
            with self.assertRaises(ValidationError):
                record_step_state(self.planner, a, bad)
        with self.assertRaises(PermissionDenied):
            record_step_state(make_user('op', 'Operator'), a, E.RESUMED)

    def test_voiding_a_pause_undoes_it(self):
        item, a = self.running()
        event = record_step_state(self.planner, a, E.PAUSED)
        self.assertEqual(self.code(item), 'PAUSED')
        correct_event(event, self.planner, 'pressed by mistake')
        self.assertEqual(self.code(item), 'IN_PROGRESS')

    # ----- partial -----
    def test_partial_marks_the_step_until_it_finishes(self):
        item, a = self.running()
        record_step_state(self.planner, a, E.PARTIAL, 'half the batch')
        st = item_status(item)
        self.assertEqual((st.code, st.rows[1].partial, st.rows[1].state), ('IN_PROGRESS', True, 'in_progress'))
        self.assertEqual(Event.objects.get(event_type=E.PARTIAL).reason, 'half the batch')
        self.scan(a, self.mill, 'FINISHED')
        row = item_status(item).rows[1]
        self.assertEqual((row.state, row.partial), ('done', False))

    # ----- cancelled -----
    def test_cancel_rules(self):
        item, a = self.running()
        with self.assertRaises(ValidationError):
            cancel_item(self.planner, item, '  ')
        with self.assertRaises(PermissionDenied):
            cancel_item(make_user('op', 'Operator'), item, 'x')
        cancel_item(self.planner, item, 'customer withdrew')
        st = item_status(item)
        self.assertEqual((st.code, st.label, st.cancel_event.reason), ('CANCELLED', 'Cancelled', 'customer withdrew'))
        with self.assertRaises(ValidationError):
            cancel_item(self.planner, item, 'again')
        done, b = self.make(route=[self.mat])
        self.scan(b, self.mat, 'ARRIVED')
        with self.assertRaises(ValidationError):
            cancel_item(self.planner, done, 'too late')

    def test_cancelled_items_leave_every_list_and_counter(self):
        late_order = Order.objects.create(customer=Customer.objects.create(name='LateCo'), due_date=timezone.localdate() - timedelta(days=3))
        item, a = self.make(order=late_order)
        self.scan(a, self.mat, 'ARRIVED')
        other, _ = self.make()
        counters = lambda: self.client.get('/').context['counters']
        self.assertEqual((counters()['overdue'], counters()['waiting'], counters()['not_started']), (1, 1, 1))
        self.assertEqual({r['item'] for r in build_matrix()['rows']}, {item, other})
        cancel_item(self.planner, item, 'withdrawn')
        self.assertEqual((counters()['overdue'], counters()['waiting'], counters()['not_started']), (0, 0, 1))
        self.assertEqual([r['item'] for r in build_matrix()['rows']], [other])
        self.assertEqual({e['item'] for e in active_boxes()}, {other})
        self.assertNotIn(item.item_code, self.client.get('/priorities/').content.decode())
        rows = {r['order']: r for r in self.client.get('/orders/').context['rows']}
        self.assertEqual((rows[late_order]['code'], rows[late_order]['late']), ('CANCELLED', False))
        self.assertEqual([r['order'] for r in self.client.get('/orders/', {'late': '1'}).context['rows']], [])
        self.assertEqual([r['order'] for r in self.client.get('/orders/', {'status': 'CANCELLED'}).context['rows']], [late_order])

    def test_order_status_ignores_cancelled_items_until_all_are_cancelled(self):
        order = Order.objects.create(customer=Customer.objects.create(name='Two'), due_date=timezone.localdate() + timedelta(days=5))
        one, a = self.make(route=[self.mat], order=order)
        two, _ = self.make(order=order)
        self.scan(a, self.mat, 'ARRIVED')
        self.assertEqual(order_summary(order)[0], 'IN_PROGRESS')
        cancel_item(self.planner, two, 'drop')
        self.assertEqual(order_summary(order)[0], 'COMPLETE')  # the only live item is done
        three, _ = self.make(order=order)
        cancel_item(self.planner, three, 'drop')
        self.assertEqual(order_summary(order)[0], 'COMPLETE')
        solo = Order.objects.create(customer=order.customer, due_date=timezone.localdate())
        only, _ = self.make(order=solo)
        cancel_item(self.planner, only, 'drop')
        self.assertEqual(order_summary(solo), ('CANCELLED', 'Cancelled'))

    def test_undoing_a_cancellation(self):
        item, a = self.running()
        event = cancel_item(self.planner, item, 'oops')
        page = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(page, 'Cancelled on')
        self.assertContains(page, 'Undo cancellation')
        self.assertContains(page, f'/events/{event.pk}/correct/')
        url = f'/events/{event.pk}/correct/'
        self.assertNotContains(self.client.get(url), 'Void and replace')
        self.assertEqual(self.client.post(url, {'reason': 'mistake', 'mode': 'void'}).status_code, 302)
        self.assertEqual(self.code(item), 'IN_PROGRESS')
        cancel_item(self.planner, item, 'really this time')
        self.assertEqual(self.code(item), 'CANCELLED')

    # ----- views -----
    def test_buttons_post_and_roles(self):
        item, a = self.running()
        url = f'/items/{item.pk}/action/'
        detail = f'/orders/{item.order.job_number}/'
        page = self.client.get(detail)
        for label in ('Pause', 'Mark partial', 'Cancel item'):
            self.assertContains(page, label)
        self.assertNotContains(page, 'value="resume"')
        r = self.client.post(url, {'action': 'pause', 'box': a.pk})
        self.assertEqual((r.status_code, r['Location']), (302, detail))
        self.assertEqual(self.code(item), 'PAUSED')
        page = self.client.get(detail)
        self.assertContains(page, 'Step paused.')
        self.assertContains(page, 'badge-paused')
        self.assertContains(page, 'value="resume"')
        self.assertNotContains(page, 'value="pause"')
        self.client.post(url, {'action': 'resume', 'box': a.pk})
        self.client.post(url, {'action': 'partial', 'box': a.pk, 'reason': 'half'})
        self.assertContains(self.client.get(detail), 'badge-partial')
        self.client.post(url, {'action': 'cancel', 'reason': 'customer withdrew'})
        self.assertEqual(self.code(item), 'CANCELLED')
        self.assertContains(self.client.get(detail), 'badge-cancelled')

    def test_bad_requests(self):
        item, a = self.running()
        other, b = self.make()
        url = f'/items/{item.pk}/action/'
        self.assertEqual(self.client.post(url, {'action': 'explode'}).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'pause', 'box': b.pk}).status_code, 404)  # another item's box
        self.assertEqual(self.client.post(url, {'action': 'pause', 'box': 'x'}).status_code, 404)
        self.client.post(url, {'action': 'resume', 'box': a.pk})  # not paused: message, no event
        self.client.post(url, {'action': 'cancel', 'reason': ''})
        self.assertFalse(Event.objects.filter(event_type__in=[E.RESUMED, E.CANCELLED]).exists())
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_operators_cannot_use_the_buttons(self):
        item, a = self.running()
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.post(f'/items/{item.pk}/action/', {'action': 'pause', 'box': a.pk}).status_code, 403)
        page = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertNotContains(page, 'Cancel item')
        self.assertNotContains(page, 'value="pause"')
        self.client.logout()
        self.assertEqual(self.client.post(f'/items/{item.pk}/action/', {}).status_code, 302)

    def test_paused_shows_on_home_boxes_and_matrix(self):
        item, a = self.running()
        record_step_state(self.planner, a, E.PAUSED)
        record_step_state(self.planner, a, E.RESUMED)
        record_step_state(self.planner, a, E.PARTIAL)
        record_step_state(self.planner, a, E.PAUSED)
        self.assertEqual(self.client.get('/').context['counters']['paused'], 1)
        self.assertContains(self.client.get('/'), 'counter-paused')
        boxes = self.client.get('/boxes/', {'status': 'paused'})
        self.assertEqual([e['item'] for e in boxes.context['rows']], [item])
        self.assertContains(boxes, 'Paused')
        row = build_matrix(status='PAUSED')['rows'][0]
        self.assertEqual((row['cells'][1]['state'], row['cells'][1]['partial']), ('paused', True))
        page = self.client.get('/', {'status': 'PAUSED'})
        self.assertContains(page, 'matrix-paused')
        self.assertContains(page, 'Partial')

    def test_history_shows_the_new_events(self):
        item, a = self.running()
        record_step_state(self.planner, a, E.PARTIAL, 'half the batch')
        page = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(page, 'half the batch')
        self.assertContains(page, '>Partial<')


class ShippedTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.device = ScannerDevice.objects.create(name='D')
        self.n = 0

    def make(self, route=None, due=None, order=None, done=True):
        """An item with a tag; with done=True a material-only route is complete."""
        self.n += 1
        order = order or Order.objects.create(customer=Customer.objects.create(name=f'C{self.n}'), due_date=due or timezone.localdate() + timedelta(days=10))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=3)
        for i, p in enumerate(route or [self.mat], 1):
            RouteStep.objects.create(item=item, sequence=i, process=p)
        a = TagAssignment.objects.create(tag=Tag.objects.create(uid=f'SH{self.n}'), item=item,
                                         start_at=timezone.now() - timedelta(days=30))
        if done:
            record_event(self.device, a.tag.uid, self.mat, 'ARRIVED', occurred_at=timezone.now() - timedelta(days=2))
        return item, a

    def ship_at(self, item, when):
        event = Event(event_type=E.SHIPPED, item=item, created_by=self.planner, occurred_at=when)
        event.full_clean()
        event.save()
        return event

    def code(self, item):
        return item_status(item).code

    def test_only_a_complete_item_can_be_shipped_by_a_planner(self):
        item, a = self.make(route=[self.mat, self.mill])
        with self.assertRaises(ValidationError):
            ship_item(self.planner, item)  # still running
        record_event(self.device, a.tag.uid, self.mill, 'STARTED')
        record_step_state(self.planner, a, E.PAUSED)
        self.assertEqual(self.code(item), 'PAUSED')
        with self.assertRaises(ValidationError):
            ship_item(self.planner, item)  # paused
        gone, _a = self.make(done=False)
        cancel_item(self.planner, gone, 'withdrawn')
        with self.assertRaises(ValidationError):
            ship_item(self.planner, gone)  # cancelled
        done, _a = self.make()
        with self.assertRaises(PermissionDenied):
            ship_item(make_user('op', 'Operator'), done)
        ship_item(self.planner, done)
        st = item_status(done)
        self.assertEqual((st.code, st.label), ('SHIPPED', 'Shipped'))
        with self.assertRaises(ValidationError):
            ship_item(self.planner, done)  # already shipped
        with self.assertRaises(ValidationError):
            cancel_item(self.planner, done, 'too late')
        self.assertEqual(Event.objects.filter(event_type=E.SHIPPED).count(), 1)

    def test_event_rules(self):
        item, _a = self.make()
        ok = dict(created_by=self.planner, event_type=E.SHIPPED)
        Event(**ok, item=item).full_clean()
        with self.assertRaises(ValidationError):
            Event(**ok).full_clean()  # needs an item
        with self.assertRaises(ValidationError):
            Event(**ok, item=item, process=self.mat).full_clean()  # no process

    def test_shipping_before_complete_does_not_count(self):
        item, a = self.make(route=[self.mat, self.mill])
        record_event(self.device, a.tag.uid, self.mat, 'ARRIVED')
        self.ship_at(item, timezone.now())
        self.assertEqual(self.code(item), 'WAITING')

    def test_a_correction_returns_the_item_to_complete(self):
        item, _a = self.make()
        event = ship_item(self.planner, item)
        correct_event(event, self.planner, 'shipped by mistake')
        self.assertEqual(self.code(item), 'COMPLETE')
        ship_item(self.planner, item)
        self.assertEqual(self.code(item), 'SHIPPED')

    def test_ship_form_asks_for_confirmation(self):
        done, _a = self.make()
        html = self.client.get(f'/orders/{done.order.job_number}/').content.decode()
        self.assertIn(f'data-confirm="Mark item {done.item_code} as shipped?"', html)

    def test_button_only_on_complete_items_and_undo_link_on_shipped(self):
        done, _a = self.make()
        busy, _b = self.make(done=False)
        self.assertContains(self.client.get(f'/orders/{done.order.job_number}/'), 'Mark as shipped')
        self.assertNotContains(self.client.get(f'/orders/{busy.order.job_number}/'), 'Mark as shipped')
        self.client.force_login(make_user('op', 'Operator'))
        self.assertNotContains(self.client.get(f'/orders/{done.order.job_number}/'), 'Mark as shipped')
        self.client.force_login(self.planner)
        r = self.client.post(f'/items/{done.pk}/action/', {'action': 'ship'})
        self.assertEqual(r.status_code, 302)
        page = self.client.get(f'/orders/{done.order.job_number}/')
        self.assertContains(page, 'Undo shipment')
        self.assertNotContains(page, 'Mark as shipped')
        self.client.post(f'/items/{busy.pk}/action/', {'action': 'ship'})
        self.assertEqual(Event.objects.filter(event_type=E.SHIPPED).count(), 1)

    def test_order_is_shipped_when_all_items_are_shipped(self):
        order = Order.objects.create(customer=Customer.objects.create(name='Two'), due_date=timezone.localdate() + timedelta(days=5))
        one, _a = self.make(order=order)
        two, _b = self.make(order=order)
        self.assertEqual(order_summary(order)[0], 'COMPLETE')
        ship_item(self.planner, one)
        self.assertEqual(order_summary(order)[0], 'COMPLETE')
        ship_item(self.planner, two)
        self.assertEqual(order_summary(order), ('SHIPPED', 'Shipped'))
        rows = {r['order']: r for r in self.client.get('/orders/', {'status': 'SHIPPED'}).context['rows']}
        self.assertEqual(list(rows), [order])

    def test_shipped_items_leave_overdue_priority_and_matrix_lists(self):
        item, a = self.make(route=[self.mat, self.mill], due=timezone.localdate() - timedelta(days=3), done=False)
        late, _b = self.make(due=timezone.localdate() - timedelta(days=3))
        self.assertEqual([r['item'] for r in build_matrix()['rows']], [item])  # a complete item is already off the matrix
        ship_item(self.planner, late)
        self.assertEqual([r['item'] for r in build_matrix()['rows']], [item])
        self.assertNotIn(late.item_code, self.client.get('/priorities/').content.decode())
        rows = {r['order']: r for r in self.client.get('/orders/').context['rows']}
        self.assertEqual((rows[late.order]['code'], rows[late.order]['late']), ('SHIPPED', False))

    def test_shipping_page_lists_ready_items_and_on_time_rate(self):
        tz = timezone.get_current_timezone()
        due = date(2031, 3, 10)
        early, _a = self.make(due=due)
        late, _b = self.make(due=due)
        ready, _c = self.make(due=timezone.localdate() - timedelta(days=1))
        self.ship_at(early, timezone.make_aware(datetime(2031, 3, 9, 10, 0), tz))
        self.ship_at(late, timezone.make_aware(datetime(2031, 3, 11, 10, 0), tz))
        r = self.client.get('/shipping/')
        self.assertEqual([x['item'] for x in r.context['ready']], [ready])
        self.assertEqual(r.context['months'], [{'month': '2031-03', 'shipped': 2, 'on_time': 1, 'rate': 50}])
        self.assertContains(r, '50%')
        self.assertContains(r, ready.item_code)

    def test_shipping_page_is_for_planners_and_up(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/shipping/').status_code, 403)
        self.assertNotContains(self.client.get('/'), 'href="/shipping/"')
        self.client.logout()
        self.assertEqual(self.client.get('/shipping/').status_code, 302)
        self.client.force_login(self.planner)
        self.assertContains(self.client.get('/'), 'href="/shipping/"')


class DefectCauseTests(TestCase):
    """Main cause on results, and the cause / machine / worker tables of the Analysis results tab."""

    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.lathe = Process.objects.create(name='Lathe', kind=K.MACHINING)
        self.w1, self.w2 = Worker.objects.create(name='W1', name_ja='作業者1'), Worker.objects.create(name='W2')
        self.m1, self.m2 = Machine.objects.create(name='M1'), Machine.objects.create(name='M2')
        self.scratch, self.burr = DefectCause.objects.get(name='Scratch'), DefectCause.objects.get(name='Burr')
        self.customers = [Customer.objects.create(name='C1'), Customer.objects.create(name='C2')]
        self.n = 0

    def result(self, proc=None, qty=100, defects=0, worker=None, machine=None, cause=None, customer=0, days_ago=1):
        """A saved RESULT event on its own item/tag, so the customer filter has an owner to find."""
        self.n += 1
        order = Order.objects.create(customer=self.customers[customer], due_date=timezone.localdate() + timedelta(days=5))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=qty)
        proc = proc or self.mill
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        uid = f'DC{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item, start_at=timezone.now() - timedelta(days=30))
        e = Event(tag_uid=uid, process=proc, event_type=E.RESULT, quantity=qty, defects=defects, worker=worker, machine=machine,
                  defect_cause=cause, created_by=self.planner, occurred_at=timezone.now() - timedelta(days=days_ago))
        e.full_clean()
        e.save()
        return e

    def tables(self, **params):
        return self.client.get('/analysis/', {'tab': 'results', **params}).context

    def test_causes_are_seeded_with_japanese_names(self):
        self.assertEqual(DefectCause.objects.count(), 7)
        with translation.override('ja'):
            self.assertEqual(str(DefectCause.objects.get(name='Scratch')), 'キズ')
        self.assertEqual(str(DefectCause.objects.get(name='Burr')), 'Burr')

    def test_cause_is_only_for_results_with_defects(self):
        bad = Event(tag_uid='X', event_type=E.ARRIVED, process=Process.objects.create(name='Mat', kind=K.MATERIAL),
                    created_by=self.planner, defect_cause=self.scratch)
        with self.assertRaises(ValidationError):
            bad.full_clean()
        with self.assertRaises(ValidationError):
            self.result(defects=0, cause=self.scratch)
        self.assertEqual(self.result(defects=2, cause=self.scratch).defect_cause, self.scratch)
        self.assertIsNone(self.result(defects=0).defect_cause)

    def test_cause_round_trips_through_the_form(self):
        e = self.result()
        item = OrderItem.objects.get()
        a = item.tagassignment_set.get()
        data = {f'result{item.pk}-box': a.pk, f'result{item.pk}-process': self.mill.pk, f'result{item.pk}-quantity': 10,
                f'result{item.pk}-defects': 3, f'result{item.pk}-defect_cause': self.burr.pk}
        r = self.client.post(f'/items/{item.pk}/result/', data, follow=True)
        self.assertContains(r, 'Result recorded.')
        saved = Event.objects.filter(event_type=E.RESULT).latest('pk')
        self.assertEqual((saved.defects, saved.defect_cause), (3, self.burr))
        self.assertContains(self.client.get(f'/results/{saved.pk}/'), 'Burr')
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), 'Only when there are defects.')
        data[f'result{item.pk}-defects'] = 0
        before = Event.objects.count()
        r = self.client.post(f'/items/{item.pk}/result/', data, follow=True)
        self.assertContains(r, 'A main cause needs at least one defect.')
        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(e.defect_cause_id, None)

    def test_pareto_order_cumulative_ends_at_100_and_unrecorded_is_last(self):
        self.result(defects=2, cause=self.burr)
        self.result(defects=6, cause=self.scratch)
        self.result(defects=2)  # defects without a cause
        rows = self.tables()['cause_rows']
        self.assertEqual([(r['name'], r['defects']) for r in rows], [('Scratch', 6), ('Burr', 2), ('No cause recorded', 2)])
        self.assertEqual([round(r['share']) for r in rows], [60, 20, 20])
        self.assertEqual([round(r['cumulative']) for r in rows], [60, 80, 100])
        self.assertAlmostEqual(rows[-1]['cumulative'], 100)
        self.assertContains(self.client.get('/analysis/'), 'Defects by cause')

    def test_no_defects_gives_no_cause_rows(self):
        self.result()
        self.assertEqual(self.tables()['cause_rows'], [])
        self.assertContains(self.client.get('/analysis/'), 'No defects recorded.')

    def test_rate_tables_sort_highest_first_and_include_unrecorded(self):
        self.result(qty=100, defects=1, worker=self.w1, machine=self.m1)
        self.result(qty=100, defects=9, worker=self.w2, machine=self.m2)
        self.result(qty=100, defects=4)
        ctx = self.tables()
        self.assertEqual([(r['name'], r['rate']) for r in ctx['machine_rows']], [('M2', 9.0), ('No machine recorded', 4.0), ('M1', 1.0)])
        self.assertEqual([r['name'] for r in ctx['worker_rows']], ['W2', 'No worker recorded', 'W1'])
        self.assertFalse(ctx['worker_rows'][1]['known'])

    def test_few_data_below_twenty_pieces(self):
        self.result(qty=19, defects=19, worker=self.w1)
        self.result(qty=20, defects=1, worker=self.w2)
        rows = {r['name']: r for r in self.tables()['worker_rows']}
        self.assertEqual((rows['W1']['few'], rows['W2']['few']), (True, False))
        page = self.client.get('/analysis/')
        self.assertContains(page, 'class="num few-data"')
        self.assertContains(page, 'few data')
        self.assertContains(page, 'A higher rate is a reason to look, not a conclusion')

    def test_tables_follow_the_filters(self):
        self.result(self.mill, defects=5, worker=self.w1, machine=self.m1, cause=self.scratch, customer=0)
        self.result(self.lathe, defects=7, worker=self.w2, machine=self.m2, cause=self.burr, customer=1)
        self.result(self.mill, defects=3, worker=self.w1, machine=self.m1, cause=self.burr, customer=0, days_ago=60)
        names = lambda ctx, key: [r['name'] for r in ctx[key]]
        self.assertEqual(sorted(names(self.tables(), 'worker_rows')), ['W1', 'W2'])
        self.assertEqual(names(self.tables(process=self.lathe.pk), 'worker_rows'), ['W2'])
        self.assertEqual(names(self.tables(process=self.lathe.pk), 'machine_rows'), ['M2'])
        self.assertEqual([r['name'] for r in self.tables(process=self.mill.pk)['cause_rows']], ['Scratch', 'Burr'])
        recent = timezone.localdate() - timedelta(days=10)
        ctx = self.tables(date_from=recent.isoformat())
        self.assertEqual({r['name']: r['defects'] for r in ctx['cause_rows']}, {'Scratch': 5, 'Burr': 7})
        self.assertEqual(names(self.tables(customer=self.customers[1].pk), 'worker_rows'), ['W2'])

    def test_voided_results_are_left_out(self):
        e = self.result(defects=4, cause=self.scratch, worker=self.w1)
        Event(tag_uid=e.tag_uid, event_type=E.CORRECTION, corrects=e, created_by=self.planner, reason='wrong').save()
        ctx = self.tables()
        self.assertEqual((ctx['cause_rows'], ctx['worker_rows']), ([], []))

    def test_worker_table_is_hidden_from_operators(self):
        self.result(defects=2, worker=self.w1, machine=self.m1, cause=self.scratch)
        self.client.force_login(make_user('op', 'Operator'))
        ctx = self.tables()
        self.assertFalse(ctx['show_workers'])
        self.assertNotIn('worker_rows', ctx)
        page = self.client.get('/analysis/')
        self.assertContains(page, 'Defect rate by machine')
        self.assertContains(page, 'Defects by cause')
        self.assertNotContains(page, 'Defect rate by worker')
        self.assertNotContains(page, 'W1')
        self.client.force_login(self.planner)
        self.assertContains(self.client.get('/analysis/'), 'Defect rate by worker')

    def test_japanese_page_uses_cause_names_in_japanese(self):
        self.result(defects=2, cause=self.scratch)
        from django.conf import settings
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'ja'
        ctx = self.client.get('/analysis/', {'tab': 'results'}).context
        self.assertEqual(ctx['cause_rows'][0]['name'], 'キズ')

    def test_demo_data_has_causes_a_bad_worker_and_a_bad_machine(self):
        from io import StringIO
        from django.core.management import call_command
        from .analysis import Filters, defect_rate_rows, result_events
        with override_settings(DEBUG=True):
            call_command('seed_demo', stdout=StringIO())
        events = result_events(Filters())
        self.assertGreaterEqual(len(events), 30)
        self.assertGreater(len({e.worker_id for e in events}), 3)
        self.assertGreater(len({e.machine_id for e in events}), 3)
        self.assertGreater(len({e.defect_cause_id for e in events if e.defect_cause_id}), 3)
        milling = Process.objects.get(name='Milling')
        top_worker = [r for r in defect_rate_rows(result_events(Filters(process=milling.pk)), 'worker', 'none') if not r['few']][0]
        self.assertEqual(top_worker['name'], 'Suzuki Takumi')
        lathe = Process.objects.get(name='Lathe')
        top_machine = [r for r in defect_rate_rows(result_events(Filters(process=lathe.pk)), 'machine', 'none') if not r['few']][0]
        self.assertEqual(top_machine['name'], 'Lathe 2')


class AnalysisRecordsTests(TestCase):
    """Median, check-scan flags, the records panel (?sel=) and the vendor table of the Analysis page."""

    def setUp(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.device = ScannerDevice.objects.create(name='D')
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.cut = Process.objects.create(name='Cut', kind=K.MACHINING)
        self.customer = Customer.objects.create(name='C')
        self.vendor = Vendor.objects.create(code='00131', name='Slow Co', kind=Vendor.Kind.VENDOR)
        self.base = timezone.localtime(timezone.now() - timedelta(days=80)).replace(hour=12, minute=0, second=0, microsecond=0)
        self.n = 0

    def step(self, proc, wait_h=1, proc_h=24, planned=3, vendor=None, boxes=1):
        """One item (material, then `proc`) with a finished step: wait_h hours of wait, proc_h hours of processing."""
        self.n += 1
        order = Order.objects.create(customer=self.customer, due_date=timezone.localdate() + timedelta(days=5))
        item = OrderItem.objects.create(order=order, part_name=f'P{self.n}', quantity=1, box_count=boxes)
        RouteStep.objects.create(item=item, sequence=1, process=self.mat)
        RouteStep.objects.create(item=item, sequence=2, process=proc, vendor=vendor, due_date=self.base.date() + timedelta(days=planned))
        uid = f'AR{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item, start_at=self.base - timedelta(days=1))
        hours = lambda h: self.base + timedelta(hours=h)
        record_event(self.device, uid, self.mat, 'ARRIVED', occurred_at=self.base)
        first, last = ('SENT_OUT', 'RETURNED') if proc.kind == K.OUTSOURCED else ('STARTED', 'FINISHED')
        record_event(self.device, uid, proc, first, occurred_at=hours(wait_h))
        record_event(self.device, uid, proc, last, occurred_at=hours(wait_h + proc_h))
        return item

    def get(self, tab='results', **params):
        return self.client.get('/analysis/', {'tab': tab, **params})

    def test_median_beside_the_mean_with_an_outlier(self):
        from .analysis import _median
        self.assertEqual((_median([1, 1, 2, 50]), sum([1, 1, 2, 50]) / 4), (1.5, 13.5))
        self.assertEqual(_median([timedelta(hours=1), timedelta(hours=3)]), timedelta(hours=2))
        self.assertIsNone(_median([None]))
        for days in (1, 1, 2, 50):
            self.step(self.mill, proc_h=24 * days)
        row, = self.get('plan').context['rows']
        self.assertEqual((row['n'], row['actual'], row['median_actual']), (4, 13.5, 1.5))
        self.assertContains(self.get('plan'), 'Median actual (days)')

    def test_results_show_median_and_step_count(self):
        for h in (2, 2, 4, 40):
            self.step(self.mill, wait_h=h, proc_h=h)
        row, = [r for r in self.get().context['rows'] if r['process'] == 'Mill']
        self.assertEqual((row['n'], row['processing_median'], row['wait_median']), (4, timedelta(hours=3), timedelta(hours=3)))
        self.assertEqual(row['processing'], timedelta(hours=12))
        page = self.get()
        self.assertContains(page, 'Median processing time')
        self.assertContains(page, 'nights and weekends included')
        self.assertEqual([r['process'] for r in self.get(process=self.cut.pk).context['rows']], [])

    def flags(self, **params):
        return {r['item'].part_name: [f['key'] for f in r['flags']] for r in self.get(sel=self.mill.pk, **params).context['records']}

    def test_check_scan_needs_five_steps_and_more_than_three_times_the_median(self):
        for _i in range(3):
            self.step(self.mill, wait_h=1, proc_h=2)
        self.step(self.mill, wait_h=1, proc_h=20)  # only 4 finished steps: no flag
        self.assertFalse(any(self.flags().values()))
        self.step(self.mill, wait_h=1, proc_h=2)  # 5 steps, median 2h: 20h is flagged, 6h (3x) would not be
        self.step(self.mill, wait_h=1, proc_h=6)
        flagged = {k: v for k, v in self.flags().items() if v}
        self.assertEqual(flagged, {'P4': ['processing']})
        page = self.get(sel=self.mill.pk)
        self.assertContains(page, 'Check scan')
        self.assertContains(page, 'Processing time 20h 0m is more than 3 times the process median 2h 0m')

    def test_sel_filters_the_records(self):
        self.step(self.mill)
        self.step(self.cut)
        self.assertNotIn('records', self.get().context)
        ctx = self.get(sel=self.cut.pk).context
        self.assertEqual([r['process'] for r in ctx['records']], ['Cut'])
        self.assertEqual(ctx['records_total'], 1)
        both = self.client.get('/analysis/?tab=results&sel=%d&sel=%d' % (self.mill.pk, self.cut.pk)).context
        self.assertEqual(sorted(r['process'] for r in both['records']), ['Cut', 'Mill'])
        self.assertEqual(self.get(sel='x').context['sel'], [])

    def test_record_columns_cause_and_box(self):
        self.step(self.mill, wait_h=30, proc_h=5)
        self.step(self.mill, wait_h=1, proc_h=5, boxes=2)
        by_part = {r['item'].part_name: r for r in self.get(sel=self.mill.pk).context['records']}
        self.assertEqual((by_part['P1']['cause'], by_part['P1']['total']), ('Waited before the step', timedelta(hours=35)))
        self.assertEqual((by_part['P2']['cause'], by_part['P2']['box']), ('Processing', 'Box 1 of 2'))
        self.assertEqual(by_part['P1']['box'], '')
        page = self.get(sel=self.mill.pk)
        self.assertContains(page, f'href="/orders/{by_part["P1"]["job"]}/"')
        self.assertContains(page, 'Box 1 of 2')
        self.assertNotContains(page, 'Box 1 of 1')

    def test_outsourced_step_cause_is_at_vendor(self):
        out = Process.objects.create(name='Plate', kind=K.OUTSOURCED)
        self.step(out, wait_h=1, proc_h=48)
        self.assertEqual(self.get(sel=out.pk).context['records'][0]['cause'], 'At vendor')

    def test_default_and_whitelisted_order(self):
        for h in (5, 50, 20):
            self.step(self.mill, wait_h=1, proc_h=h)
        totals = lambda **p: [r['total'] for r in self.get(sel=self.mill.pk, **p).context['records']]
        longest_first = [timedelta(hours=51), timedelta(hours=21), timedelta(hours=6)]
        self.assertEqual(totals(), longest_first)
        self.assertEqual(totals(order='bogus'), longest_first)
        self.assertEqual(totals(order='<script>'), longest_first)
        self.assertEqual([r['item'].part_name for r in self.get(sel=self.mill.pk, order='item').context['records']], ['P1', 'P2', 'P3'])
        ctx = self.get(sel=self.mill.pk, order='processing').context
        self.assertEqual(ctx['order'], 'processing')
        self.assertIn('order=wait', ctx['order_urls']['wait'])
        self.assertIn(f'sel={self.mill.pk}', ctx['order_urls']['wait'])
        plan = self.get('plan', sel=self.mill.pk, order='wait').context  # a results-only key falls back on the plan default
        self.assertEqual(plan['order'], 'overrun')

    def test_cap_and_show_more(self):
        from . import views
        self.assertEqual((views.RECORD_LIMIT, views.RECORD_CAP), (200, 2000))
        for _i in range(8):
            self.step(self.mill)
        with mock.patch.object(views, 'RECORD_LIMIT', 3), mock.patch.object(views, 'RECORD_CAP', 6):
            r = self.get(sel=self.mill.pk)
            self.assertEqual((len(r.context['records']), r.context['records_total']), (3, 8))
            self.assertContains(r, 'Showing 3 of 8')
            self.assertIn('limit=6', r.context['more_url'])
            self.assertContains(r, 'Show more')
            r = self.get(sel=self.mill.pk, limit='6')
            self.assertEqual(len(r.context['records']), 6)
            self.assertEqual(r.context['more_url'], '')  # at the cap
            self.assertEqual(len(self.get(sel=self.mill.pk, limit='999999').context['records']), 6)

    def test_ticked_rows_summary_data(self):
        self.step(self.mill, wait_h=2, proc_h=4)
        page = self.get(sel=self.mill.pk)
        self.assertContains(page, 'data-records-summary data-unit="seconds"')
        self.assertContains(page, f'data-value="{6 * 3600}"')
        self.assertContains(page, 'src="/static/js/records.js"')
        self.assertContains(page, 'name="sel" value="%d" checked' % self.mill.pk)
        plan = self.get('plan', sel=self.mill.pk)
        self.assertContains(plan, 'data-unit="days"')
        self.assertContains(plan, 'data-value="0"')  # finished the same JST day it arrived

    def test_plan_records_overrun_first(self):
        self.step(self.mill, proc_h=24, planned=3)   # actual 1, planned 3
        self.step(self.mill, proc_h=24 * 6, planned=3)  # actual 6, planned 3: +100%
        rows = self.get('plan', sel=self.mill.pk).context['records']
        self.assertEqual([(r['actual'], r['planned'], r['diff'], r['pct']) for r in rows], [(6, 3, 3, 100.0), (1, 3, -2, -200 / 3)])
        self.assertContains(self.get('plan', sel=self.mill.pk), 'Planned days')

    def test_vendor_table(self):
        fast = Vendor.objects.create(code='00132', name='Fast Co', kind=Vendor.Kind.VENDOR)
        for days in (6, 8):
            self.step(self.mill, proc_h=24 * days, vendor=self.vendor)
        self.step(self.mill, proc_h=24, vendor=fast)
        rows = self.get('plan').context['vendor_rows']
        self.assertEqual([(r['vendor'], r['n'], r['actual'], r['median_actual'], r['planned']) for r in rows],
                         [(self.vendor, 2, 7, 7, 3), (fast, 1, 1, 1, 3)])
        self.assertEqual(rows[0]['pct'], (7 - 3) * 100 / 3)
        page = self.get('plan')
        self.assertContains(page, 'By vendor')
        self.assertContains(page, 'Slow Co')
        self.assertEqual(self.get('plan', vendor=fast.pk).context['vendor_rows'][0]['vendor'], fast)

    def test_operator_can_open_both_tabs_but_anonymous_cannot(self):
        self.step(self.mill)
        for tab in ('results', 'plan'):
            self.assertEqual(self.get(tab, sel=self.mill.pk).status_code, 200)
        self.client.logout()
        self.assertEqual(self.get(sel=self.mill.pk).status_code, 302)

    def test_demo_data_has_enough_steps_a_forgotten_scan_and_a_slow_vendor(self):
        from io import StringIO
        from django.core.management import call_command
        from .analysis import Filters, step_facts
        with override_settings(DEBUG=True):
            call_command('seed_demo', stdout=StringIO())
        facts = step_facts(Filters())
        per_process = Counter(x['process'].name for x in facts)
        self.assertTrue(per_process and all(n >= 6 for n in per_process.values()), per_process)
        self.assertTrue(any(x['flags'] for x in facts))
        rows = {r['vendor'].code: r for r in vendor_rows(facts)}
        others = [r['pct'] for code, r in rows.items() if code != '00135' and r['pct'] is not None]
        self.assertGreater(rows['00135']['pct'], max(others))


class AnalysisPageTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('op', 'Operator'))

    def test_old_urls_redirect_to_their_tab_keeping_filters(self):
        self.assertRedirects(self.client.get('/results/'), '/analysis/?tab=results')
        self.assertRedirects(self.client.get('/plan-vs-actual/', {'customer': '3'}), '/analysis/?customer=3&tab=plan')

    def test_tabs_nav_and_scroll_panel(self):
        html = self.client.get('/analysis/?tab=plan').content.decode()
        self.assertIn('class="tab is-active" href="/analysis/?tab=plan" aria-current="page"', html)
        self.assertIn('href="/analysis/?tab=results"', html)
        self.assertIn('class="scroll-panel"', html)
        nav = html[html.index('id="site-nav"'):html.index('</nav>')]
        self.assertIn('href="/analysis/"', nav)
        self.assertNotIn('/results/', nav)
        self.assertNotIn('/plan-vs-actual/', nav)
        self.assertEqual(self.client.get('/analysis/?tab=bogus').context['tab'], 'results')

    def test_tabs_keep_the_filters(self):
        self.assertContains(self.client.get('/analysis/', {'tab': 'plan', 'customer': '5'}), 'href="/analysis/?tab=results&amp;customer=5"')

    def test_severity_bands(self):
        from .analysis import severity
        self.assertEqual([severity(x) for x in (None, 0, 10, 11, 31, 61)], ['', '', '', 'minor', 'major', 'severe'])

    def test_roles(self):
        self.client.logout()
        self.assertEqual(self.client.get('/analysis/').status_code, 302)
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.get('/analysis/').status_code, 403)


class ShippingFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def test_page_has_two_scroll_panels_and_both_filter_forms(self):
        html = self.client.get('/shipping/').content.decode()
        self.assertEqual(html.count('class="scroll-panel"'), 2)
        for name in ('r_q', 'r_customer', 'due_from', 'due_to', 'm_q', 'm_customer', 'month_from', 'month_to'):
            self.assertIn(f'name="{name}"', html)

    def test_each_form_keeps_the_other_forms_values(self):
        html = self.client.get('/shipping/', {'r_q': 'gear', 'month_from': '2026-01'}).content.decode()
        self.assertIn('type="hidden" name="r_q" value="gear"', html)
        self.assertIn('type="hidden" name="month_from" value="2026-01"', html)

    def test_bad_month_text_is_ignored(self):
        self.assertEqual(self.client.get('/shipping/', {'month_from': 'abc'}).status_code, 200)


class BoxCaptionTests(TestCase):
    """'Box k of n' shows only for items with more than one box; k follows start time, then pk."""

    def setUp(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        self.single = OrderItem.objects.create(order=order, part_name='One', quantity=1)
        self.multi = OrderItem.objects.create(order=order, part_name='Many', quantity=1, box_count=3)
        self.client.force_login(make_user('s', 'Admin'))

    def test_caption_helper(self):
        self.assertEqual(box_caption(2, 3), 'Box 2 of 3')
        self.assertEqual((box_caption(1, 1), box_caption(None, 3)), ('', ''))

    def test_hidden_for_a_single_box_item(self):
        a = assign_tag(self.single, 'ONE')
        self.assertEqual(box_captions([a]), {a.pk: ''})
        self.assertEqual(item_status(self.single).boxes[0].caption, '')
        r = self.client.get('/tags/', {'q': 'one'})
        self.assertContains(r, 'ONE')
        self.assertNotContains(r, 'Box 1 of 1')
        self.assertNotContains(r, ' of 1<')

    def test_shown_for_several_boxes_in_start_order(self):
        a, b = assign_tag(self.multi, 'M1'), assign_tag(self.multi, 'M2')
        self.assertEqual(box_captions([b, a]), {a.pk: 'Box 1 of 3', b.pk: 'Box 2 of 3'})
        self.assertEqual([x.caption for x in item_status(self.multi).boxes], ['Box 1 of 3', 'Box 2 of 3', ''])  # unlinked third: none
        self.assertContains(self.client.get('/tags/', {'q': 'm2'}), 'Box 2 of 3')
        self.assertContains(self.client.get('/tags/history/'), 'Box 1 of 3')
        self.assertContains(self.client.get(f'/orders/{self.multi.order.job_number}/'), 'Box 2 of 3')

    def test_replaced_tag_keeps_its_box_number(self):
        a, b = assign_tag(self.multi, 'M1'), assign_tag(self.multi, 'M2')
        new = replace_tag(a, 'M3')
        self.assertEqual(box_captions([a, b, new]), {a.pk: 'Box 1 of 3', b.pk: 'Box 2 of 3', new.pk: 'Box 1 of 3'})

    def test_second_assignment_of_the_same_tag_is_refused(self):
        assign_tag(self.single, 'SAME')
        with self.assertRaisesMessage(ValidationError, 'already in use'):
            assign_tag(self.multi, 'same')
        self.assertEqual(TagAssignment.objects.count(), 1)


class RemoveBoxMigrationTests(TransactionTestCase):
    """History (tag, item, start, end) survives dropping Box; same-box assignments become a replacement chain."""
    serialized_rollback = True

    def test_history_survives(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('tracking', '0007_event_tag_time_index')])
        old = executor.loader.project_state([('tracking', '0007_event_tag_time_index')]).apps
        Box, OldTag, OldAssignment = (old.get_model('tracking', n) for n in ('Box', 'Tag', 'TagAssignment'))
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate())
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        other = OrderItem.objects.create(order=order, part_name='Q', quantity=1)
        now = timezone.now()
        b1, b2 = Box.objects.create(label='B1'), Box.objects.create(label='B2')
        t = {u: OldTag.objects.create(uid=u) for u in ('A', 'B', 'C', 'D')}
        mk = lambda tag, box, it, s, e=None: OldAssignment.objects.create(tag=t[tag], box=box, item_id=it.pk, start_at=now - timedelta(days=s), end_at=e and now - timedelta(days=e)).pk
        a, b, c, d = mk('A', b1, item, 9, 5), mk('B', b1, item, 5), mk('C', b2, item, 8), mk('D', b1, other, 7, 3)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())  # forward again, to the latest migration
        rows = {x.pk: x for x in TagAssignment.objects.select_related('tag')}
        self.assertEqual({k: (v.tag.uid, v.item_id, v.replaces_id) for k, v in rows.items()},
                         {a: ('A', item.pk, None), b: ('B', item.pk, a), c: ('C', item.pk, None), d: ('D', other.pk, None)})
        self.assertEqual((rows[a].start_at, rows[a].end_at, rows[b].end_at), (now - timedelta(days=9), now - timedelta(days=5), None))
        self.assertEqual(len(item_status(item).boxes), 2)


def image_file(fmt='JPEG', size=(40, 30), name='x.jpg', orientation=None, mode='RGB'):
    """An uploaded image of that format; orientation adds a JPEG EXIF orientation (and a camera tag)."""
    buf, kw = BytesIO(), {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112], exif[0x010F] = orientation, 'Camera'
        kw['exif'] = exif
    Image.new(mode, size, 'red').save(buf, fmt, **kw)
    return SimpleUploadedFile(name, buf.getvalue())


class InvestigationBase(TestCase):
    """Results with their own item / tag, photos stored in a temporary MEDIA_ROOT."""

    def setUp(self):
        self.media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=self.media))
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.mill = Process.objects.create(name='Mill', kind=K.MACHINING)
        self.lathe = Process.objects.create(name='Lathe', kind=K.MACHINING)
        self.w1, self.w2 = Worker.objects.create(name='Worker Alpha'), Worker.objects.create(name='Worker Beta')
        self.m1, self.m2 = Machine.objects.create(name='M1'), Machine.objects.create(name='M2')
        self.scratch, self.burr = DefectCause.objects.get(name='Scratch'), DefectCause.objects.get(name='Burr')
        self.customers = [Customer.objects.create(name='C1'), Customer.objects.create(name='C2')]
        self.n = 0

    def result(self, part='PA', proc=None, qty=100, defects=0, worker=None, machine=None, cause=None, customer=0, days_ago=1, note='', photo=None):
        self.n += 1
        order = Order.objects.create(customer=self.customers[customer], due_date=timezone.localdate() + timedelta(days=5))
        item = OrderItem.objects.create(order=order, part_name=f'Name {part}', part_number=part, quantity=qty)
        proc = proc or self.mill
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        uid = f'IV{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item, start_at=timezone.now() - timedelta(days=30))
        e = Event(tag_uid=uid, process=proc, event_type=E.RESULT, quantity=qty, defects=defects, worker=worker, machine=machine, defect_cause=cause,
                  created_by=self.planner, occurred_at=timezone.now() - timedelta(days=days_ago), note=note)
        if photo:
            e.photo = photo
        e.full_clean()
        e.save()
        return e

    def page(self, **params):
        return self.client.get('/analysis/', {'tab': 'results', **params})


class ResultPhotoHelperTests(TestCase):
    def setUp(self):
        self.media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=self.media))

    def test_accepts_jpeg_png_webp_and_returns_a_jpeg(self):
        for fmt in ('JPEG', 'PNG', 'WEBP'):
            out = Image.open(BytesIO(process_photo(image_file(fmt)).read()))
            self.assertEqual((out.format, out.mode), ('JPEG', 'RGB'))

    def test_transparent_png_becomes_rgb_on_white(self):
        up = image_file('PNG', mode='RGBA')
        out = Image.open(BytesIO(process_photo(up).read()))
        self.assertEqual(out.mode, 'RGB')

    def test_wrong_type_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'Upload a JPEG, PNG or WEBP image.'):
            process_photo(image_file('GIF', name='x.jpg'))

    def test_fake_extension_is_rejected_and_the_extension_is_not_trusted(self):
        with self.assertRaises(ValidationError):
            process_photo(SimpleUploadedFile('evil.jpg', b'<?php echo 1; ?>'))
        process_photo(image_file('PNG', name='picture.exe'))  # a real PNG passes whatever it is called

    def test_too_large_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'File too large (maximum 8 MB).'):
            process_photo(SimpleUploadedFile('big.jpg', b'0' * (8 * 1024 * 1024 + 1)))

    def test_exif_orientation_is_applied_exif_dropped_and_large_images_shrink(self):
        out = Image.open(BytesIO(process_photo(image_file(size=(2000, 1000), orientation=6)).read()))
        self.assertEqual(out.size, (800, 1600))  # rotated upright, long side 1600
        self.assertEqual(len(out.getexif()), 0)
        small = Image.open(BytesIO(process_photo(image_file(size=(100, 50))).read()))
        self.assertEqual(small.size, (100, 50))  # never enlarged

    def test_saved_name_is_random_under_year_and_month(self):
        names = []
        for _i in range(2):
            e = Event(event_type=E.RESULT, tag_uid='N', created_by=make_user('np', 'Planner'), process=Process.objects.create(name=f'P{_i}', kind=K.MACHINING), quantity=1)
            e.photo = process_photo(image_file(name='my holiday.jpg'))
            e.full_clean()
            e.save()
            names.append(e.photo.name)
        self.assertNotEqual(*names)
        for name in names:
            self.assertRegex(name, r'^defects/\d{4}/\d{2}/[0-9a-f]{32}\.jpg$')
            self.assertNotIn('holiday', name)
            self.assertTrue((self.media / name).exists())


class ResultNotePhotoTests(InvestigationBase):
    def test_note_and_photo_only_on_results(self):
        mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        for extra in ({'note': 'hello'}, {'photo': process_photo(image_file())}):
            with self.assertRaises(ValidationError):
                Event(tag_uid='X', event_type=E.ARRIVED, process=mat, created_by=self.planner, **extra).full_clean()
        self.assertEqual(self.result(note='ok').note, 'ok')

    def test_note_is_limited_to_1000_characters(self):
        with self.assertRaises(ValidationError):
            self.result(note='x' * 1001)

    def post(self, item, **extra):
        a = item.tagassignment_set.get()
        data = {'box': a.pk, 'process': self.mill.pk, 'quantity': 10, 'defects': 2, **extra}
        return self.client.post(f'/items/{item.pk}/result/', {f'result{item.pk}-{k}': v for k, v in data.items()}, follow=True)

    def test_form_round_trip_with_multipart(self):
        self.result()
        item = OrderItem.objects.get()
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), 'enctype="multipart/form-data"')
        r = self.post(item, note='Edge chipped\nsecond line', photo=image_file(size=(2400, 1200), orientation=3))
        self.assertContains(r, 'Result recorded.')
        saved = Event.objects.filter(event_type=E.RESULT).latest('pk')
        self.assertEqual(saved.note, 'Edge chipped\nsecond line')
        self.assertTrue((self.media / saved.photo.name).exists())
        self.assertContains(r, 'cannot be edited later')
        self.assertContains(r, 'accept="image/*"')

    def test_bad_upload_or_long_note_saves_nothing(self):
        self.result()
        item = OrderItem.objects.get()
        before = Event.objects.count()
        self.assertContains(self.post(item, photo=SimpleUploadedFile('a.jpg', b'not an image')), 'Upload a JPEG, PNG or WEBP image.')
        self.assertContains(self.post(item, note='x' * 1001), 'at most 1000 characters')
        self.assertEqual(Event.objects.count(), before)
        self.assertEqual(list((self.media).rglob('*.jpg')), [])

    def test_photo_view_needs_login_and_role_and_a_photo(self):
        with_photo = self.result(photo=process_photo(image_file()))
        without = self.result()
        url = lambda e: f'/results/{e.pk}/photo/'
        self.client.logout()
        self.assertEqual(self.client.get(url(with_photo)).status_code, 302)
        self.client.force_login(make_user('norole'))
        self.assertEqual(self.client.get(url(with_photo)).status_code, 403)
        self.client.force_login(make_user('op', 'Operator'))
        r = self.client.get(url(with_photo))
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'image/jpeg'))
        self.assertIn('private', r['Cache-Control'])
        self.assertTrue(b''.join(r.streaming_content).startswith(b'\xff\xd8'))
        self.assertEqual(self.client.get(url(without)).status_code, 404)
        arrived = Event.objects.create(tag_uid='Z', event_type=E.ARRIVED, process=Process.objects.create(name='Mat', kind=K.MATERIAL), created_by=self.planner)
        self.assertEqual(self.client.get(url(arrived)).status_code, 404)

    def test_missing_file_is_a_404_not_a_crash(self):
        e = self.result(photo=process_photo(image_file()))
        (self.media / e.photo.name).unlink()
        self.assertEqual(self.client.get(f'/results/{e.pk}/photo/').status_code, 404)

    def test_detail_page_worker_row_is_for_planners_only(self):
        e = self.result(defects=3, worker=self.w1, machine=self.m1, cause=self.scratch, note='Line one\n<b>two</b>', photo=process_photo(image_file()))
        page = self.client.get(f'/results/{e.pk}/')
        for text in ('Worker Alpha', 'M1', 'Scratch', '3.0%', 'Line one<br>&lt;b&gt;two&lt;/b&gt;', 'Name PA', 'C1', 'target="_blank"', 'alt="Photo of the defects'):
            self.assertContains(page, text)
        self.assertContains(page, f'href="/results/{e.pk}/photo/"')
        self.assertNotContains(page, '/media/')
        self.client.force_login(make_user('op', 'Operator'))
        page = self.client.get(f'/results/{e.pk}/')
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Worker Alpha')
        self.assertNotContains(page, '<dt>Worker</dt>')
        self.assertContains(page, 'Scratch')

    def test_detail_page_roles_404_and_voided_banner(self):
        e = self.result(defects=1)
        self.client.logout()
        self.assertEqual(self.client.get(f'/results/{e.pk}/').status_code, 302)
        self.client.force_login(self.planner)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'  # logout cleared the cookies
        self.assertNotContains(self.client.get(f'/results/{e.pk}/'), 'Voided on')
        Event(tag_uid=e.tag_uid, event_type=E.CORRECTION, corrects=e, created_by=self.planner, reason='Typed wrong quantity').save()
        self.assertContains(self.client.get(f'/results/{e.pk}/'), 'Voided on')
        self.assertContains(self.client.get(f'/results/{e.pk}/'), 'Typed wrong quantity')
        arrived = Event.objects.create(tag_uid='Z', event_type=E.ARRIVED, process=Process.objects.create(name='Mat', kind=K.MATERIAL), created_by=self.planner)
        self.assertEqual(self.client.get(f'/results/{arrived.pk}/').status_code, 404)

    def test_event_history_shows_a_summary_and_details_link_only(self):
        e = self.result(qty=5, defects=2, worker=self.w1, cause=self.scratch, note='Secret note text', photo=process_photo(image_file()))
        item = OrderItem.objects.get()
        page = self.client.get(f'/orders/{item.order.job_number}/')
        self.assertContains(page, '5 done, 2 defects')
        self.assertContains(page, f'href="/results/{e.pk}/"')
        self.assertContains(page, '<span class="badge">Note</span>')
        self.assertContains(page, '<span class="badge">Photo</span>')
        for text in ('Secret note text', '/media/', e.photo.name, '&middot; Worker Alpha', 'Scratch</td>'):
            self.assertNotContains(page, text)


class DefectInvestigationTests(InvestigationBase):
    def setUp(self):
        super().setUp()
        self.e1 = self.result('PA', defects=10, worker=self.w1, machine=self.m1, cause=self.scratch, note='n1')
        self.e2 = self.result('PA', defects=2, worker=self.w2, machine=self.m2, cause=self.burr)
        self.e3 = self.result('PA', defects=0, worker=self.w2)
        self.e4 = self.result('PB', qty=50, defects=0, worker=self.w1, machine=self.m1)
        self.e5 = self.result('PB', qty=10, defects=1, worker=self.w1, machine=self.m2)
        self.e6 = self.result('PA', proc=self.lathe, defects=5, worker=self.w1, machine=self.m1, cause=self.scratch)
        self.e7 = self.result('PB', defects=3, worker=self.w2, machine=self.m2)  # defects without a cause

    def drill(self, **params):
        return self.page(**params).context.get('drill')

    def pks(self, drill):
        return [r['event'].pk for r in drill['records']]

    def test_each_parameter_filters_the_records_and_the_by_part_table(self):
        d = self.drill(d_machine=self.m1.pk)
        self.assertEqual(self.pks(d), [self.e1.pk, self.e6.pk])
        self.assertEqual([(p['part'], p['process'], p['defects']) for p in d['parts']], [('PA', 'Mill', 10), ('PA', 'Lathe', 5), ('PB', 'Mill', 0)])
        d = self.drill(d_worker=self.w2.pk)
        self.assertEqual(sorted(self.pks(d)), sorted([self.e2.pk, self.e7.pk]))
        self.assertEqual({(p['part'], p['defects']) for p in d['parts']}, {('PA', 2), ('PB', 3)})
        d = self.drill(d_cause=self.scratch.pk)
        self.assertEqual(sorted(self.pks(d)), sorted([self.e1.pk, self.e6.pk]))
        self.assertFalse(d['baseline'])
        d = self.drill(d_cause='none')
        self.assertEqual(sorted(self.pks(d)), sorted([self.e5.pk, self.e7.pk]))  # only defects without a cause
        d = self.drill(d_machine='none')
        self.assertEqual(self.pks(d), [])  # the only result without a machine has no defects
        self.assertEqual([(p['part'], p['quantity']) for p in d['parts']], [('PA', 100)])

    def test_baseline_leaves_out_the_chosen_machine_or_worker(self):
        by = {(p['part'], p['process']): p for p in self.drill(d_machine=self.m1.pk)['parts']}
        self.assertEqual(by[('PA', 'Mill')]['rate'], 10.0)
        self.assertEqual(by[('PA', 'Mill')]['base_rate'], 1.0)  # e2 + e3: 2 of 200
        self.assertEqual(by[('PB', 'Mill')]['base_rate'], (1 + 3) * 100 / 110)  # e5 + e7
        self.assertIsNone(by[('PA', 'Lathe')]['base_rate'])  # nobody else on that part and process
        by = {(p['part'], p['process']): p for p in self.drill(d_worker=self.w1.pk)['parts']}
        self.assertEqual(by[('PA', 'Mill')]['base_rate'], 1.0)  # e2 + e3 are worker Beta's
        self.assertContains(self.page(d_machine=self.m1.pk), 'Rate for everyone else on the same part and process')
        self.assertNotContains(self.page(d_cause=self.scratch.pk), 'Rate for everyone else')

    def test_unrecorded_machine_baseline_is_everyone_with_a_machine(self):
        by = {(p['part'], p['process']): p for p in self.drill(d_machine='none')['parts']}
        self.assertEqual(by[('PA', 'Mill')]['base_rate'], 12 * 100 / 200)  # e1 + e2

    def test_only_results_with_defects_is_on_by_default(self):
        d = self.drill(d_machine=self.m1.pk)
        self.assertTrue(d['only'])
        d = self.drill(d_machine=self.m1.pk, d_only='0')
        self.assertFalse(d['only'])
        self.assertIn(self.e4.pk, self.pks(d))
        self.assertTrue(self.drill(d_machine=self.m1.pk, d_only=['0', '1'])['only'])  # the checkbox sends the last value
        self.assertContains(self.page(d_machine=self.m1.pk), 'Only results with defects')

    def test_few_data_rows_are_greyed(self):
        self.result('PE', qty=10, defects=5, machine=self.m2)
        page = self.page(d_machine=self.m2.pk)
        by = {(p['part'], p['process']): p for p in page.context['drill']['parts']}
        self.assertEqual((by[('PE', 'Mill')]['few'], by[('PA', 'Mill')]['few']), (True, False))
        self.assertContains(page, 'class="num few-data"')
        self.assertContains(page, 'A rate from fewer than 20 pieces')

    def test_filters_apply_and_the_d_parameters_are_not_filters(self):
        self.assertEqual(self.pks(self.drill(d_machine=self.m1.pk, process=self.lathe.pk)), [self.e6.pk])
        self.assertEqual(self.pks(self.drill(d_machine=self.m1.pk, date_from=(timezone.localdate() + timedelta(days=1)).isoformat())), [])
        self.result('PA', defects=4, machine=self.m1, customer=1)
        self.assertEqual(len(self.pks(self.drill(d_machine=self.m1.pk, customer=self.customers[1].pk))), 1)
        ctx = self.page(d_machine=self.m1.pk, process=self.mill.pk).context
        self.assertEqual(ctx['values']['process'], str(self.mill.pk))
        self.assertTrue(all('d_machine' not in t['url'] for t in ctx['tabs']))
        self.assertFalse(hasattr(Filters, 'd_machine'))

    def test_records_links_keep_the_other_filters(self):
        ctx = self.page(process=self.mill.pk).context
        row = next(r for r in ctx['machine_rows'] if r['id'] == self.m1.pk)
        self.assertIn(f'd_machine={self.m1.pk}', row['url'])
        self.assertIn(f'process={self.mill.pk}', row['url'])
        self.assertTrue(row['url'].endswith('#defect-records'))
        self.assertTrue(any(r['id'] == 'none' for r in self.page().context['cause_rows']))
        page = self.page(d_machine=self.m1.pk)
        self.assertContains(page, 'id="defect-records"')
        self.assertContains(page, f'href="/results/{self.e1.pk}/"')
        self.assertContains(page, 'Clear')
        self.assertNotContains(self.page(), 'id="defect-records"')

    def test_worker_names_and_d_worker_are_for_planners_only(self):
        self.assertContains(self.page(d_worker=self.w1.pk), 'Worker Alpha')
        self.client.force_login(make_user('op', 'Operator'))
        page = self.page(d_worker=self.w1.pk)
        self.assertIsNone(page.context.get('drill'))
        self.assertNotContains(page, 'Worker Alpha')
        page = self.page(d_machine=self.m1.pk)
        self.assertIsNotNone(page.context['drill'])
        self.assertNotContains(page, 'Worker Alpha')
        self.assertNotContains(page, '<th>Worker</th>')
        self.assertFalse(page.context['drill']['planner'])
        self.assertTrue(all('worker' not in r for r in page.context['drill']['records']))
        both = self.page(d_worker=self.w1.pk, d_machine=self.m1.pk)
        self.assertEqual(self.pks(both.context['drill']), [self.e1.pk, self.e6.pk])  # d_worker ignored, d_machine used

    def test_sorting_uses_a_whitelist(self):
        d = self.drill(d_machine=self.m2.pk, d_only='0')
        self.assertEqual(d['order'], 'defects')
        self.assertEqual([r['event'].defects for r in d['records']], [3, 2, 1])
        self.assertEqual(self.drill(d_machine=self.m2.pk, d_only='0', dorder='rate')['order'], 'rate')
        self.assertEqual(self.drill(d_machine=self.m2.pk, dorder='nonsense')['order'], 'defects')
        old = self.result('PA', defects=1, machine=self.m2, days_ago=20)
        d = self.drill(d_machine=self.m2.pk, dorder='date')
        self.assertEqual(self.pks(d)[-1], old.pk)

    def test_records_are_capped_at_200_with_show_more(self):
        e = self.e1
        a = TagAssignment.objects.get(tag__uid=e.tag_uid)
        Event.objects.bulk_create([
            Event(tag_uid=e.tag_uid, process=self.mill, event_type=E.RESULT, quantity=10, defects=1, machine=self.m1, created_by=self.planner,
                  occurred_at=timezone.now() - timedelta(minutes=i + 1)) for i in range(204)])
        page = self.page(d_machine=self.m1.pk)
        d = page.context['drill']
        self.assertEqual((len(d['records']), d['total']), (200, 206))
        self.assertContains(page, 'Showing 200 of 206')
        self.assertIn('dlimit=400', d['more_url'])
        d = self.drill(d_machine=self.m1.pk, dlimit=400)
        self.assertEqual((len(d['records']), d['more_url']), (206, ''))
        self.assertEqual(len(self.drill(d_machine=self.m1.pk, dlimit=5)['records']), 200)  # never below 200
        self.assertEqual(a.tag.uid, e.tag_uid)

    def test_query_count_does_not_grow_with_the_rows(self):
        def count():
            with CaptureQueriesContext(connection) as q:
                defect_detail(result_events(Filters()), 'machine', self.m1.pk, True, 'defects', True)
            return len(q)
        before = count()
        for i in range(15):
            self.result('PC', defects=1, worker=self.w1, machine=self.m1)
        self.assertEqual(count(), before)
        with CaptureQueriesContext(connection) as small:
            self.page(d_machine=self.m1.pk)
        for i in range(15):
            self.result('PD', defects=1, worker=self.w1, machine=self.m1)
        with CaptureQueriesContext(connection) as big:
            self.page(d_machine=self.m1.pk)
        self.assertEqual(len(big), len(small))


class DefectDemoDataTests(TestCase):
    def setUp(self):
        self.media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=self.media, DEBUG=True))

    def test_demo_has_notes_photos_and_the_two_patterns(self):
        from django.core.management import call_command
        call_command('seed_demo', stdout=StringIO())
        events = result_events(Filters())
        photos = [e for e in events if e.photo]
        self.assertTrue(6 <= len(photos) <= 8, len(photos))
        for e in photos:
            self.assertTrue((self.media / e.photo.name).exists())
            self.assertRegex(e.photo.name, r'^defects/demo/demo-\d\d\.jpg$')
        self.assertGreaterEqual(len([e for e in events if e.note]), 6)
        self.assertGreaterEqual(len({e.worker_id for e in events}), 5)
        machine = Machine.objects.get(name='Machining centre 2')
        parts, _records = defect_detail(events, 'machine', machine.pk, True, 'defects', True)
        top = next(p for p in parts if not p['few'])
        self.assertEqual(top['part'], 'SPC-220')
        self.assertGreater(top['rate'], 2 * top['base_rate'])
        worker = Worker.objects.get(name='Nakamura Mio')
        parts, _records = defect_detail(events, 'worker', worker.pk, True, 'defects', True)
        top = next(p for p in parts if not p['few'])
        self.assertEqual(top['part'], 'ARM-510')
        self.assertGreater(top['rate'], 2 * top['base_rate'])

    def test_reset_removes_and_recreates_the_demo_photos(self):
        from django.core.management import call_command
        call_command('seed_demo', stdout=StringIO())
        folder = self.media / 'defects' / 'demo'
        names = sorted(p.name for p in folder.iterdir())
        (folder / 'stale.jpg').write_bytes(b'x')
        call_command('seed_demo', reset=True, stdout=StringIO())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), names)  # deterministic names, stale file gone
