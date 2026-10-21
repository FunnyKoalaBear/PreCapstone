import json
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from types import SimpleNamespace

from django.test import override_settings
from django.utils import timezone

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep

from .forms import AssignTagForm
from .models import Box, Event, ScannerDevice, Tag, TagAssignment
from .overview import active_boxes, process_board
from .priority import priority
from .services import assign_tag, correct_event, record_event, release_assignment, replace_tag
from .status import gantt, AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, BoxStatus, ItemStatus, _roll_up, events_for_item, days_late, next_action, due_back, idle_for, is_stagnant, item_status, timeline

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
            tag=Tag.objects.create(uid='AB12'), box=Box.objects.create(label='B'), item=self.item, start_at=self.start,
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
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), box=Box.objects.create(label='B'), item=self.item)

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

    def link(self, uid, box, start_days, end_days=None):
        """An assignment that began start_days ago and ended end_days ago (open when None)."""
        return TagAssignment.objects.create(
            tag=Tag.objects.get_or_create(uid=uid)[0], box=Box.objects.get_or_create(label=box)[0], item=self.item,
            start_at=self.now - timedelta(days=start_days), end_at=None if end_days is None else self.now - timedelta(days=end_days))

    def history(self, **params):
        r = self.client.get('/tags/history/', params)
        self.assertEqual(r.status_code, 200)
        return [a.tag.uid for a in r.context['rows']], r

    def test_in_use_shows_count_without_rows_until_searched(self):
        self.link('AA11', 'BX1', 5)
        self.link('BB22', 'BX2', 6)
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
        a = self.link('AA11', 'BX1', 5)
        r = self.client.post('/tags/', {'action': 'release', 'assignment': a.pk, 'q': 'aa'})
        self.assertRedirects(r, '/tags/?q=aa')
        self.assertRedirects(self.client.post('/tags/', {'action': 'release', 'assignment': self.link('CC33', 'BX3', 1).pk}), '/tags/')

    def test_history_page_is_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get('/tags/history/').status_code, 302)

    def test_history_ranges_overlap_rule(self):
        self.link('D10', 'B10', 20, 10)      # inside every range
        self.link('D100', 'B100', 120, 100)  # 3-6 months ago
        self.link('D200', 'B200', 250, 200)  # 6-12 months ago
        self.link('D400', 'B400', 500, 400)  # older than a year
        self.link('OPEN', 'BOP', 400)        # open since long ago: overlaps everything
        self.link('NEW', 'BNW', 2)           # open, started recently
        expect = {'1m': {'D10', 'OPEN', 'NEW'}, '3m': {'D10', 'OPEN', 'NEW'}, '6m': {'D10', 'D100', 'OPEN', 'NEW'},
                  '1y': {'D10', 'D100', 'D200', 'OPEN', 'NEW'}}
        for key, uids in expect.items():
            self.assertEqual(set(self.history(range=key)[0]), uids, key)
        self.assertEqual(set(self.history()[0]), expect['3m'])
        self.assertEqual(set(self.history(range='bogus')[0]), expect['3m'])

    def test_history_is_newest_first_and_shows_in_use(self):
        self.link('OLD1', 'B1', 30, 20)
        self.link('NEW1', 'B2', 3)
        uids, r = self.history()
        self.assertEqual(uids, ['NEW1', 'OLD1'])
        self.assertContains(r, 'In use')
        self.assertContains(r, f'href="/orders/{self.item.order.job_number}/"')

    def test_history_custom_range_and_fallbacks(self):
        self.link('D10', 'B10', 20, 10)
        self.link('D100', 'B100', 120, 100)
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

    def test_history_search_by_tag_and_box(self):
        self.link('AB12', 'SHELF-1', 5, 2)
        self.link('CD34', 'SHELF-2', 5, 2)
        self.assertEqual(self.history(q='ab1')[0], ['AB12'])
        self.assertEqual(self.history(q='shelf-2')[0], ['CD34'])
        self.assertEqual(sorted(self.history(q='SHELF')[0]), ['AB12', 'CD34'])
        self.assertEqual(self.history(q='nothing')[0], [])

    def test_history_cap(self):
        for i in range(201):
            self.link(f'T{i:03d}', f'X{i:03d}', 10, 5)
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

    def test_assign_creates_box_tag_and_open_assignment(self):
        a = assign_tag(self.item1, ' B1 ', 'ab12')
        self.assertEqual((a.box.label, a.tag.uid, a.end_at), ('B1', 'AB12', None))

    def test_uid_is_uppercased(self):
        assign_tag(self.item1, 'B1', ' ab12 ')
        self.assertTrue(Tag.objects.filter(uid='AB12').exists())

    def test_tag_in_use_is_rejected(self):
        assign_tag(self.item1, 'B1', 'AB')
        with self.assertRaises(ValidationError):
            assign_tag(self.item2, 'B2', 'ab')
        self.assertFalse(Box.objects.filter(label='B2').exists())

    def test_item_with_assignment_is_rejected(self):
        assign_tag(self.item1, 'B1', 'AB')
        with self.assertRaises(ValidationError):
            assign_tag(self.item1, 'B2', 'CD')

    def test_release_then_reuse_and_events_count_for_new_item(self):
        old = assign_tag(self.item1, 'B1', 'AB')
        release_assignment(old)
        old.refresh_from_db()
        self.assertIsNotNone(old.end_at)
        assign_tag(self.item2, 'B1', 'AB')
        record_event(ScannerDevice.objects.create(name='D'), 'AB', self.proc, E.ARRIVED)
        self.assertEqual(item_status(self.item2).code, COMPLETE)
        self.assertEqual(item_status(self.item1).code, NOT_STARTED)

    def test_page_is_staff_only_and_assign_release_work(self):
        self.assertEqual(self.client.get('/tags/').status_code, 302)
        self.client.force_login(make_user('s', 'Admin'))
        self.assertEqual(self.client.get('/tags/').status_code, 200)
        r = self.client.post('/tags/', {'action': 'assign', 'item': self.item1.pk, 'box_label': 'B1', 'tag_uid': 'ab'})
        self.assertRedirects(r, '/tags/')
        a = TagAssignment.objects.get()
        self.client.post('/tags/', {'action': 'release', 'assignment': a.pk})
        a.refresh_from_db()
        self.assertIsNotNone(a.end_at)
        self.assertEqual(self.client.post('/tags/', {'action': 'release', 'assignment': a.pk}).status_code, 404)


    def test_assign_dropdown_only_offers_items_with_a_free_box_slot(self):
        self.item2.box_count = 2
        self.item2.save()
        assign_tag(self.item1, 'B1', 'AB')
        assign_tag(self.item2, 'B2', 'CD')
        field = AssignTagForm().fields['item']
        self.assertEqual(list(field.queryset), [self.item2])
        label = field.label_from_instance(field.queryset.get())
        self.assertIn('(1 of 2 boxes linked)', label)
        self.assertTrue(label.startswith(self.item2.item_code + ' - '))

    def test_tags_page_labels_each_open_box_of_an_item(self):
        self.item1.box_count = 2
        self.item1.save()
        assign_tag(self.item1, 'B1', 'AB')
        assign_tag(self.item1, 'B2', 'CD')
        self.client.force_login(make_user('s', 'Admin'))
        r = self.client.get('/tags/', {'q': 'b'})
        self.assertContains(r, 'Box B1 (1 of 2)')
        self.assertContains(r, 'Box B2 (2 of 2)')


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
        TagAssignment.objects.create(tag=tag, box=Box.objects.create(label=f'B{self.n}'), item=item, start_at=timezone.now() - timedelta(days=30))
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
        self.assertContains(r, '<option value="waiting" selected>')
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
        TagAssignment.objects.create(tag=Tag.objects.create(uid='T2B'), box=Box.objects.create(label='B2b'), item=item,
                                     start_at=timezone.now() - timedelta(days=5))
        for etype in (E.STARTED, E.FINISHED):  # box 1 finishes step 1
            Event.objects.create(device=self.device, tag_uid=a1.tag.uid, event_type=etype, process=mach)
        self.client.force_login(make_user('s', 'Admin'))
        rows = self.client.get('/priorities/').context['rows']
        self.assertEqual([(r['box_label'], r['tier'], r['multi']) for r in rows],
                         [('B2b', 'CRITICAL', True), (a1.box.label, 'WATCH', True)])  # unlinked third box left out


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
            tag=Tag.objects.get_or_create(uid=uid)[0], box=Box.objects.create(label=f'B{uid}{Box.objects.count()}'), item=item or self.item,
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
        box = lambda c: BoxStatus(None, '', c, c)
        cases = [
            ([COMPLETE, COMPLETE], COMPLETE), ([NOT_STARTED, NOT_STARTED], NOT_STARTED),
            ([IN_PROGRESS, WAITING], IN_PROGRESS), ([WAITING, AT_VENDOR], WAITING),
            ([AT_VENDOR, COMPLETE], AT_VENDOR), ([AT_VENDOR, NOT_STARTED], AT_VENDOR),
            ([COMPLETE, NOT_STARTED], WAITING), ([IN_PROGRESS, COMPLETE, NOT_STARTED], IN_PROGRESS),
        ]
        for codes, expected in cases:
            self.assertEqual(_roll_up([box(c) for c in codes]).code, expected, codes)

    def test_assign_tag_respects_box_count(self):
        assign_tag(self.item, 'L1', 'U1')
        assign_tag(self.item, 'L2', 'U2')
        with self.assertRaisesMessage(ValidationError, 'already has all 2 boxes linked'):
            assign_tag(self.item, 'L3', 'U3')
        self.item.box_count = 3
        self.item.save()
        assign_tag(self.item, 'L3', 'U3')
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
                tag=Tag.objects.create(uid=uid), box=Box.objects.create(label=f'L{uid}'), item=item,
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
        rows = {(e['item'].part_name, e['box_label']): e for e in active_boxes(self.now)}
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows['P1', 'LA']['code'], IN_PROGRESS)
        self.assertEqual(rows['P1', 'LA']['process'], self.cut)
        b = rows['P2', 'LB']
        self.assertEqual((b['code'], b['process'], b['stagnant'], b['late']), (WAITING, self.weld, True, True))
        self.assertEqual(b['idle'], timedelta(hours=30))
        c = rows['P3', 'LC']
        self.assertEqual((c['code'], c['late_days'], c['vendor']), (AT_VENDOR, 2, 'PV'))
        self.assertEqual(c['due_back'], due_back(c['started_at'], 3))
        self.assertEqual(rows['P4', 'LD']['code'], NOT_STARTED)
        self.assertIn(('P5', None), rows)  # placeholder
        self.assertEqual(rows['P5', None]['code'], NOT_STARTED)
        self.assertIn(('P6', None), rows)
        self.assertNotIn(('P6', 'LF'), rows)  # released finished box is done
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
            key = lambda rows: [(r['item'].pk, r['box_label']) for r in rows]
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
        self.assertEqual([b['box_label'] for b in cut['running']], ['LR1', 'LR2'])  # longest first
        self.assertEqual(cut['running'][0]['running_for'], timedelta(hours=5))
        self.assertEqual((cut['waiting'], cut['queue']), ([], False))
        self.assertEqual(weld['running'], [])
        self.assertEqual([b['box_label'] for b in weld['waiting']], ['LW1', 'LW3', 'LW2'])  # longest idle first
        self.assertTrue(weld['queue'])
        self.assertEqual(([b['box_label'] for b in paint['running']], paint['outsourced']), (['LV1'], True))


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

    def test_replacement_keeps_history_and_box(self):
        new = replace_tag(self.old, ' new ')
        self.old.refresh_from_db()
        self.assertEqual((new.tag.uid, new.box, new.item, new.end_at), ('NEW', self.old.box, self.item1, None))
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
            assign_tag(self.item([self.cut]), 'BX', 'OLD')
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
        self.assertEqual(uids('LOTHER'), ['OTHER'])  # box label
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
        self.assertContains(r, f'OLD - {self.item1.item_code} - box LOLD - {self.item1.part_name}')
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


class ScanApiTests(TestCase):
    def setUp(self):
        self.device = ScannerDevice.objects.create(name='D')
        self.mat = Process.objects.create(name='Mat', kind=K.MATERIAL)
        self.mach = Process.objects.create(name='Mach', kind=K.MACHINING)
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.now().date())
        self.item = OrderItem.objects.create(order=order, part_name='P', quantity=1)
        RouteStep.objects.create(item=self.item, sequence=1, process=self.mat)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB12'), box=Box.objects.create(label='B1'), item=self.item)

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
        self.assertEqual((j['ok'], j['tag_uid'], j['box'], j['item']), (True, 'AB12', 'B1', self.item.item_code))
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
        TagAssignment.objects.create(tag=Tag.objects.create(uid='AB12'), box=Box.objects.create(label='B'), item=self.item, start_at=self.start)
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
