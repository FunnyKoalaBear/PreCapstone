from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from orders.models import PrintJob

from .models import Event, Tag, TagAssignment
from .services import assign_tag, cancel_item, correct_event, record_event, release_assignment, ship_item
from .status import COMPLETE, NOT_STARTED, item_status
from .tests_completed import CompletedBase

E = Event.Type


class ReleaseBase(CompletedBase):
    def setUp(self):
        super().setUp()
        self.item, (self.uid,) = self.make()
        self.assignment = TagAssignment.objects.get(item=self.item)
        # scans happen "now", so move the fixed January assignment to the recent past
        TagAssignment.objects.filter(pk=self.assignment.pk).update(start_at=timezone.now() - timedelta(days=2))
        self.assignment.refresh_from_db()

    def scan(self, uid, event_type, hours_ago):
        return record_event(self.device, uid, self.mill, event_type, occurred_at=timezone.now() - timedelta(hours=hours_ago))

    def relink(self, uid='NEW1'):
        return assign_tag(self.item, uid)

    def done(self, hours_ago=1):
        self.scan(self.uid, E.STARTED, hours_ago + 1)
        self.scan(self.uid, E.FINISHED, hours_ago)


class ReleasedBoxTests(ReleaseBase):
    def test_unfinished_released_box_is_history_and_does_not_count(self):
        self.scan(self.uid, E.STARTED, 5)
        release_assignment(self.assignment)
        st = item_status(self.item)
        self.assertEqual((st.code, len(st.boxes), len(st.released)), (NOT_STARTED, 1, 1))
        self.assertIsNone(st.boxes[0].assignment)  # the padding box, no tag yet

    def test_relinked_item_counts_one_box_and_keeps_the_old_card(self):
        self.scan(self.uid, E.STARTED, 5)
        release_assignment(self.assignment)
        new = self.relink()
        st = item_status(self.item)
        self.assertEqual(len(st.boxes), 1)
        self.assertEqual(st.boxes[0].assignment.pk, new.pk)
        self.assertEqual([b.assignment.pk for b in st.released], [self.assignment.pk])
        self.assertEqual(st.code, NOT_STARTED)

    def test_finished_released_box_still_counts(self):
        self.done()
        release_assignment(self.assignment)
        st = item_status(self.item)
        self.assertEqual((st.code, len(st.boxes), st.released), (COMPLETE, 1, []))

    def test_released_box_does_not_shift_the_numbering(self):
        item, (a, b) = self.make(boxes=2)
        TagAssignment.objects.filter(item=item).update(start_at=timezone.now() - timedelta(days=2))
        release_assignment(TagAssignment.objects.get(item=item, tag__uid=a))
        st = item_status(item)
        self.assertEqual([x.caption for x in st.boxes], ['Box 1 of 2', ''])  # tag b, then the empty slot


class CloseFreesTagsTests(ReleaseBase):
    def test_cancel_ends_open_assignments_at_the_event_time(self):
        event = cancel_item(self.planner, self.item, 'x')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.end_at, event.occurred_at)
        self.assertTrue(Tag.objects.get(uid=self.uid).active)

    def test_ship_ends_open_assignments_and_the_item_stays_shipped(self):
        self.done()
        event = ship_item(self.planner, self.item)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.end_at, event.occurred_at)
        self.assertEqual(item_status(self.item).code, 'SHIPPED')

    def test_undo_cancel_relinks(self):
        event = cancel_item(self.planner, self.item, 'x')
        correct_event(event, self.planner, 'oops')
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.end_at)

    def test_undo_does_not_relink_a_tag_that_is_in_use_again(self):
        event = cancel_item(self.planner, self.item, 'x')
        other, _uids = self.make()
        TagAssignment.objects.filter(item=other).delete()
        assign_tag(other, self.uid)
        correct_event(event, self.planner, 'oops')
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.end_at)


class ClosedItemScanTests(ReleaseBase):
    def setUp(self):
        super().setUp()
        self.key = {'HTTP_X_API_KEY': self.device.api_key}

    def old_link(self):
        """Cancel, then put the link back as old data had it."""
        cancel_item(self.planner, self.item, 'x')
        TagAssignment.objects.filter(pk=self.assignment.pk).update(end_at=None)

    def test_scan_and_preview_report_cancelled_and_save_nothing(self):
        self.old_link()
        events = Event.objects.count()
        bodies = [{'tag_uid': self.uid}, {'tag_uid': self.uid, 'process': self.mill.pk, 'event_type': 'STARTED'}]
        for body in bodies:
            r = self.client.post('/api/scan/', body, content_type='application/json', **self.key)
            self.assertEqual((r.status_code, r.json()['state']), (200, 'cancelled'))
        r = self.client.get('/api/scan-preview/', {'tag_uid': self.uid}, **self.key)
        self.assertEqual(r.json()['state'], 'cancelled')
        self.assertEqual(Event.objects.count(), events)

    def test_shipped_state(self):
        self.done()
        ship_item(self.planner, self.item)
        TagAssignment.objects.filter(pk=self.assignment.pk).update(end_at=None)
        r = self.client.post('/api/scan/', {'tag_uid': self.uid}, content_type='application/json', **self.key)
        self.assertEqual(r.json()['state'], 'shipped')

    def test_freed_tag_is_unassigned(self):
        cancel_item(self.planner, self.item, 'x')
        r = self.client.post('/api/scan/', {'tag_uid': self.uid}, content_type='application/json', **self.key)
        self.assertEqual((r.status_code, r.json()['error']), (404, 'unassigned_tag'))


class AssignClosedItemTests(ReleaseBase):
    def test_assign_tag_refuses_complete_shipped_and_cancelled(self):
        self.done()
        TagAssignment.objects.filter(item=self.item).update(end_at=timezone.now())
        shipped, (uid2,) = self.make()
        TagAssignment.objects.filter(item=shipped).update(start_at=timezone.now() - timedelta(days=2))
        self.scan(uid2, E.STARTED, 3)
        self.scan(uid2, E.FINISHED, 2)
        ship_item(self.planner, shipped)
        cancelled, _uids = self.make()
        cancel_item(self.planner, cancelled, 'x')
        for item in (self.item, shipped, cancelled):
            with self.assertRaises(ValidationError):
                assign_tag(item, 'FREE1')

    def test_setter_api_gives_409_item_closed_and_no_print(self):
        cancel_item(self.planner, self.item, 'x')
        r = self.client.post('/api/assign/', {'tag_uid': 'FREE1', 'item': self.item.pk}, content_type='application/json',
                             HTTP_X_API_KEY=self.device.api_key)
        self.assertEqual((r.status_code, r.json()['reason']), (409, 'item_closed'))
        self.assertEqual(PrintJob.objects.count(), 0)

    def test_tags_page_shows_the_message(self):
        cancel_item(self.planner, self.item, 'x')
        self.client.force_login(self.planner)
        r = self.client.post('/tags/', {'action': 'assign', 'item': self.item.pk, 'tag_uid': 'FREE1'})
        self.assertContains(r, 'so no tag can be linked to it')
