from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order, OrderItem, Process, RouteStep

from .models import Box, Event, ScannerDevice, Tag, TagAssignment
from .services import record_event
from .status import AT_VENDOR, COMPLETE, IN_PROGRESS, NOT_STARTED, WAITING, item_status

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
