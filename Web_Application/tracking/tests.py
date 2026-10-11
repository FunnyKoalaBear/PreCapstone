from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep

from .models import Box, Event, ScannerDevice, Tag, TagAssignment
from .services import assign_tag, record_event, release_assignment
from .status import AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, days_late, due_back, item_status, timeline

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
        user = User.objects.create_user('staff', password='x', is_staff=True)
        self.client.force_login(user)
        r = self.client.post('/simulate/', {'tag_uid': 'zz', 'device': self.device.pk, 'action': f'{self.proc.pk}:ARRIVED'})
        self.assertRedirects(r, '/simulate/')
        self.assertEqual(Event.objects.get().tag_uid, 'ZZ')
        self.assertEqual(self.client.get('/simulate/').status_code, 200)


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
        self.client.force_login(User.objects.create_user('s', password='x', is_staff=True))
        self.assertEqual(self.client.get('/tags/').status_code, 200)
        r = self.client.post('/tags/', {'action': 'assign', 'item': self.item1.pk, 'box_label': 'B1', 'tag_uid': 'ab'})
        self.assertRedirects(r, '/tags/')
        a = TagAssignment.objects.get()
        self.client.post('/tags/', {'action': 'release', 'assignment': a.pk})
        a.refresh_from_db()
        self.assertIsNotNone(a.end_at)
        self.assertEqual(self.client.post('/tags/', {'action': 'release', 'assignment': a.pk}).status_code, 404)



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
        self.assertTrue(t['running'])

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
