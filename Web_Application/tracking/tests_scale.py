import random
import time
from collections import Counter
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.permissions import OPERATOR
from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Event, ScannerDevice, Tag, TagAssignment

from .planning import process_averages
from .status import (
    AT_VENDOR, CANCELLED, CLOSED, COMPLETE, IN_PROGRESS, NOT_STARTED, PAUSED, SHIPPED, WAITING, ItemStatusError, current_row, days_late,
    is_stagnant, item_status, item_statuses, order_summary, step_due_back,
)
from .tests_reference import first_difference, reference_item_status, status_diff

E, K = Event.Type, Process.Kind
FULL = [(0, E.ARRIVED), (1, E.STARTED), (1, E.FINISHED), (2, E.STARTED), (2, E.FINISHED), (3, E.SENT_OUT), (3, E.RETURNED)]


class World:
    """Builds items, tags and events straight into the database (no validation, so odd histories are possible)."""

    def __init__(self):
        self.now = timezone.now()
        self.base = self.now - timedelta(hours=300)
        kinds = (K.MATERIAL, K.MACHINING, K.MACHINING, K.OUTSOURCED)
        self.procs = [Process.objects.create(name=f'P{i}', kind=k, vendor_lead_days=2 if k == K.OUTSOURCED else None) for i, k in enumerate(kinds)]
        self.customer = Customer.objects.create(name='C')
        self.device = ScannerDevice.objects.create(name='D')
        self.user = User.objects.create_user('worker', password='x')

    def at(self, hours):
        return self.base + timedelta(hours=hours)

    def item(self, steps=None, box_count=1, due_days=5):
        order = Order.objects.create(customer=self.customer, due_date=timezone.localdate(self.now) + timedelta(days=due_days))
        item = OrderItem.objects.create(order=order, part_name='P', quantity=1, box_count=box_count)
        for seq, p in enumerate(self.procs if steps is None else steps, 1):
            RouteStep.objects.create(item=item, sequence=seq, process=p)
        return item

    def assign(self, item, uid, start, end=None, replaces=None, active=True):
        tag = Tag.objects.get_or_create(uid=uid, defaults={'active': active})[0]
        return TagAssignment.objects.create(tag=tag, item=item, start_at=self.at(start), end_at=None if end is None else self.at(end), replaces=replaces)

    def ev(self, uid, proc, etype, hours):
        process = None if proc is None else self.procs[proc]
        return Event.objects.create(device=self.device, tag_uid=uid, process=process, event_type=etype, occurred_at=self.at(hours))

    def run(self, uid, n, start=1, step=2):
        """The first n events of the full route on a tag, `step` hours apart."""
        return [self.ev(uid, p, t, start + i * step) for i, (p, t) in enumerate(FULL[:n])]

    def mark(self, item, etype, hours=250):
        return Event.objects.create(created_by=self.user, item=item, event_type=etype, occurred_at=self.at(hours), reason='x')

    def void(self, event):
        return Event.objects.create(device=self.device, tag_uid=event.tag_uid, event_type=E.CORRECTION, corrects=event, occurred_at=self.now, created_by=self.user, reason='x')


def standard(w):
    """One item per state worth checking; returns {name: item}."""
    items = {}
    items['not_started'] = w.item(); w.assign(items['not_started'], 'NS', 0)
    items['unassigned'] = w.item()
    items['no_steps'] = w.item(steps=[]); w.assign(items['no_steps'], 'NR', 0); w.ev('NR', 0, E.ARRIVED, 1)
    items['waiting'] = w.item(); w.assign(items['waiting'], 'WA', 0); w.run('WA', 3)
    items['in_progress'] = w.item(); w.assign(items['in_progress'], 'IP', 0); w.run('IP', 2)
    items['at_vendor'] = w.item(); w.assign(items['at_vendor'], 'AV', 0); w.run('AV', 6)
    items['paused'] = w.item(); w.assign(items['paused'], 'PA', 0); w.run('PA', 2); w.ev('PA', 1, E.PAUSED, 20)
    items['complete'] = w.item(); w.assign(items['complete'], 'CO', 0); w.run('CO', 7)
    items['shipped'] = w.item(); w.assign(items['shipped'], 'SH', 0); w.run('SH', 7); w.mark(items['shipped'], E.SHIPPED)
    items['cancelled'] = w.item(); w.assign(items['cancelled'], 'CA', 0); w.run('CA', 2); w.mark(items['cancelled'], E.CANCELLED)
    items['multi_box'] = w.item(box_count=3)
    w.assign(items['multi_box'], 'M1', 0); w.run('M1', 7); w.assign(items['multi_box'], 'M2', 0); w.run('M2', 2)
    items['replaced_tag'] = w.item()
    r1 = w.assign(items['replaced_tag'], 'R1', 0, 10); w.assign(items['replaced_tag'], 'R2', 10, None, replaces=r1)
    w.run('R1', 3, 1, 2); w.ev('R2', 2, E.STARTED, 12)
    items['voided'] = w.item(); w.assign(items['voided'], 'VO', 0)
    w.run('VO', 2); w.void(w.ev('VO', 1, E.FINISHED, 30))
    items['voided_cancel'] = w.item(); w.assign(items['voided_cancel'], 'VC', 0); w.run('VC', 2); w.void(w.mark(items['voided_cancel'], E.CANCELLED))
    return items


def build_random(w, rng, n_items, prefix=''):
    """Random items with random, overlapping, reused tags and a messy event history (integer hours, so ties and boundary hits are common)."""
    tags, boxes = [f'{prefix}T{i:02d}' for i in range(12)], [f'{prefix}X{i:02d}' for i in range(8)]
    open_tags, open_boxes, items = set(), set(), []
    for _ in range(n_items):
        item = w.item(rng.sample(w.procs, rng.randint(0, len(w.procs))), rng.randint(1, 3), rng.randint(-10, 10))
        picks, last = [], {}
        for _ in range(rng.randint(0, 4)):
            uid, box, start = rng.choice(tags), rng.choice(boxes), rng.randint(0, 150)
            end = rng.choice([None, start + rng.randint(1, 80)])
            if end is None and (uid in open_tags or box in open_boxes):
                end = start + rng.randint(1, 80)
            if end is None:
                open_tags.add(uid)
                open_boxes.add(box)
            picks.append((start, uid, box, end, rng.random() > 0.2))
        for start, uid, box, end, active in sorted(picks, key=lambda x: x[0]):  # a box chains its assignments in start order
            last[box] = w.assign(item, uid, start, end, last.get(box), active=active)
        items.append(item)
    kinds = [E.ARRIVED, E.STARTED, E.FINISHED, E.PAUSED, E.RESUMED, E.PARTIAL, E.SENT_OUT, E.RETURNED, E.RESULT]
    events = Event.objects.bulk_create([
        Event(device=w.device, tag_uid=rng.choice(tags + tags + ['', 'ZZ99']), process=rng.choice(w.procs if kind == E.RESULT else w.procs + [None]), event_type=kind,
              occurred_at=w.at(rng.randint(0, 250)), quantity=1 if kind == E.RESULT else None, defects=0 if kind == E.RESULT else None)
        for kind in (rng.choice(kinds) for _ in range(n_items * 25))])
    for a in TagAssignment.objects.filter(item__in=items):  # a plausible run inside some windows
        if rng.random() < 0.6:
            w.run(a.tag.uid, rng.randint(1, 7), int((a.start_at - w.base).total_seconds() // 3600) + 1, rng.randint(1, 3))
    for item in items:
        if rng.random() < 0.15:
            events.append(w.mark(item, E.CANCELLED, rng.randint(0, 250)))
        if rng.random() < 0.3:
            events.append(w.mark(item, E.SHIPPED, rng.randint(0, 250)))
    for e in rng.sample(events, len(events) // 10):
        w.void(e)
    return items


class ParityTests(TestCase):
    def setUp(self):
        self.w = World()

    def assertSame(self, items, chunk=None, msg=''):
        statuses = item_statuses(items) if chunk is None else item_statuses(items, chunk)
        self.assertEqual(len(statuses), len({i.pk for i in items}), msg)
        bad = first_difference(items, statuses)
        self.assertIsNone(bad, f'{msg} item {bad and bad[0].pk} differs in {bad and bad[1]}')
        return statuses

    def test_every_state_matches_the_reference(self):
        items = standard(self.w)
        statuses = self.assertSame(list(items.values()))
        want = {'not_started': NOT_STARTED, 'unassigned': NOT_STARTED, 'waiting': WAITING, 'in_progress': IN_PROGRESS, 'at_vendor': AT_VENDOR,
                'paused': PAUSED, 'complete': COMPLETE, 'shipped': SHIPPED, 'cancelled': CANCELLED, 'voided_cancel': IN_PROGRESS}
        for name, code in want.items():
            self.assertEqual(statuses[items[name].pk].code, code, name)
        self.assertEqual(len(statuses[items['multi_box'].pk].boxes), 3)
        self.assertEqual(len(statuses[items['replaced_tag'].pk].boxes), 1)
        self.assertEqual(len(statuses[items['replaced_tag'].pk].boxes[0].assignments), 2)

    def test_three_items_equal_the_reference_one_by_one(self):
        items = standard(self.w)
        a, b, c = items['waiting'], items['multi_box'], items['shipped']
        got = item_statuses([a, b, c])
        for item in (a, b, c):
            self.assertIsNone(status_diff(got[item.pk], reference_item_status(item)))

    def test_item_status_is_the_batch_of_one(self):
        item = standard(self.w)['at_vendor']
        self.assertIsNone(status_diff(item_status(item), reference_item_status(item)))

    def test_randomized_histories_match_the_reference(self):
        for seed in range(30):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                items = build_random(self.w, rng, 25, prefix=f'S{seed}')
                self.assertSame(items, chunk=rng.choice([1, 2, 5, 7, 1000]), msg=f'seed={seed}')


class EdgeCaseTests(TestCase):
    def setUp(self):
        self.w = World()

    def same(self, items, chunk=None):
        statuses = item_statuses(items) if chunk is None else item_statuses(items, chunk)
        self.assertIsNone(first_difference(items, statuses))
        return statuses

    def test_no_items(self):
        self.assertEqual(item_statuses([]), {})
        self.assertEqual(item_statuses(OrderItem.objects.none()), {})

    def test_item_with_no_route_steps(self):
        item = self.w.item(steps=[])
        self.w.assign(item, 'A', 0)
        self.w.ev('A', 0, E.ARRIVED, 1)
        self.assertEqual(self.same([item])[item.pk].code, NOT_STARTED)

    def test_item_with_no_assignments(self):
        one, three = self.w.item(), self.w.item(box_count=3)
        statuses = self.same([one, three])
        self.assertEqual([len(statuses[i.pk].boxes) for i in (one, three)], [1, 3])

    def test_box_count_greater_than_assignments(self):
        item = self.w.item(box_count=4)
        self.w.assign(item, 'A', 0)
        self.w.run('A', 7)
        statuses = self.same([item])
        self.assertEqual(len(statuses[item.pk].boxes), 4)
        self.assertEqual(statuses[item.pk].code, WAITING)

    def test_tag_reused_across_items_at_different_times(self):
        first, second = self.w.item(), self.w.item()
        self.w.assign(first, 'A', 0, 20)
        self.w.assign(second, 'A', 20, None)
        self.w.run('A', 7, 1, 2)
        self.w.run('A', 2, 22, 2)
        statuses = self.same([first, second])
        self.assertEqual((statuses[first.pk].code, statuses[second.pk].code), (COMPLETE, IN_PROGRESS))

    def test_overlapping_and_back_to_back_windows(self):
        item, other = self.w.item(box_count=2), self.w.item()
        first = self.w.assign(item, 'A', 0, 10)
        self.w.assign(item, 'B', 10, 30, replaces=first)  # back to back, same box
        self.w.assign(item, 'A', 5, 25)  # overlaps the first window on the same tag
        self.w.assign(other, 'A', 8, 12)  # another item's overlap
        for hours in (1, 5, 9, 10, 11, 25):
            self.w.ev('A', 1, E.STARTED, hours)
            self.w.ev('B', 1, E.FINISHED, hours + 1)
        self.same([item, other])

    def test_blank_tag_uid_events(self):
        item = self.w.item()
        self.w.assign(item, 'A', 0)
        self.w.ev('', 0, E.ARRIVED, 1)
        self.w.ev('A', 0, E.ARRIVED, 2)
        self.same([item])
        Tag.objects.create(uid='')  # a blank-UID tag assigned to an item picks up the blank events
        blank = self.w.item()
        self.w.assign(blank, '', 0)
        self.same([item, blank])

    def test_events_with_an_item_but_no_tag(self):
        item = self.w.item()
        self.w.assign(item, 'A', 0)
        self.w.run('A', 3)
        Event.objects.create(device=self.w.device, tag_uid='', item=item, process=self.w.procs[1], event_type=E.STARTED, occurred_at=self.w.at(30))
        Event.objects.create(device=self.w.device, tag_uid='A', item=item, process=self.w.procs[1], event_type=E.STARTED, occurred_at=self.w.at(31))
        self.same([item])

    def test_void_whose_target_is_outside_the_chunk(self):
        a, b = self.w.item(), self.w.item()
        self.w.assign(a, 'A', 0)
        self.w.assign(b, 'B', 0)
        target = self.w.ev('A', 0, E.ARRIVED, 1)
        Event.objects.create(device=self.w.device, tag_uid='B', event_type=E.CORRECTION, corrects=target, occurred_at=self.w.now, created_by=self.w.user, reason='x')
        statuses = self.same([a, b], chunk=1)
        self.assertEqual(statuses[a.pk].code, NOT_STARTED)

    def test_correction_without_a_target_voids_nothing(self):
        item = self.w.item()
        self.w.assign(item, 'A', 0)
        self.w.ev('A', 0, E.ARRIVED, 1)
        Event.objects.bulk_create([Event(device=self.w.device, tag_uid='A', event_type=E.CORRECTION, occurred_at=self.w.now)])
        self.assertEqual(item_status(item).code, WAITING)

    def test_inactive_tag(self):
        item = self.w.item()
        self.w.assign(item, 'A', 0, active=False)
        self.w.run('A', 3)
        self.assertEqual(self.same([item])[item.pk].code, WAITING)

    def test_events_exactly_at_window_start_and_end(self):
        item = self.w.item()
        self.w.assign(item, 'A', 10, 20)
        self.w.ev('A', 0, E.ARRIVED, 10)  # start is inside
        self.w.ev('A', 1, E.STARTED, 20)  # end is outside
        status = self.same([item])[item.pk]
        self.assertEqual(status.code, WAITING)
        self.assertEqual(status.last_event_at, self.w.at(10))

    def test_two_events_at_the_same_time_keep_pk_order(self):
        item = self.w.item()
        self.w.assign(item, 'A', 0)
        self.w.ev('A', 0, E.ARRIVED, 5)
        self.w.ev('A', 1, E.STARTED, 5)
        self.w.ev('A', 1, E.FINISHED, 5)
        self.assertEqual(self.same([item])[item.pk].boxes[0].rows[1].state, 'done')

    def test_items_spanning_several_chunks(self):
        items = list(standard(self.w).values())
        self.same(items, chunk=3)
        with CaptureQueriesContext(connection) as one:
            item_statuses(items, len(items))
        with CaptureQueriesContext(connection) as many:
            item_statuses(items, 3)
        self.assertEqual(len(many), len(one) * -(-len(items) // 3))

    def test_results_cover_every_item_asked_for(self):
        items = list(standard(self.w).values())
        self.assertEqual(set(item_statuses(items + items[:2], 4)), {i.pk for i in items})

    def test_an_unsaved_item_is_an_error(self):
        with self.assertRaisesMessage(ItemStatusError, 'unsaved'):
            item_statuses([OrderItem(part_name='x')])

    def test_one_odd_item_raises_an_error_naming_it(self):
        good, bad = self.w.item(), self.w.item()
        real = __import__('tracking.status', fromlist=['_replay'])._replay

        def replay(steps, assignments, events):
            if steps and steps[0].item_id == bad.pk:
                raise ValueError('odd')
            return real(steps, assignments, events)

        with mock.patch('tracking.status._replay', replay), self.assertRaisesMessage(ItemStatusError, f'order item {bad.pk}'):
            item_statuses([good, bad])

    def test_order_summary_uses_given_statuses(self):
        items = standard(self.w)
        order = items['shipped'].order
        statuses = item_statuses(order.items.all())
        with self.assertNumQueries(1):
            self.assertEqual(order_summary(order, statuses)[0], SHIPPED)


def expected_counters(rows, now, today):
    """The home counters computed straight from (item, reference status) pairs, without active_boxes."""
    boxes = []
    for item, st in rows:
        if st.code == CANCELLED:
            continue
        for box in st.boxes:
            if box.code == COMPLETE:
                continue
            row = current_row(box)
            late_days = days_late(step_due_back(row.step, row.started_at), today) if box.code == AT_VENDOR else 0
            boxes.append((box.code, item.order_id, item.order.due_date < today, is_stagnant(box, now), late_days))
    codes = Counter(b[0] for b in boxes)
    return {
        'in_progress': codes[IN_PROGRESS], 'waiting': codes[WAITING], 'at_vendor': codes[AT_VENDOR], 'not_started': codes[NOT_STARTED],
        'paused': codes[PAUSED], 'overdue': len({b[1] for b in boxes if b[2]}), 'stagnant': sum(b[3] for b in boxes),
        'vendor_overdue': sum(1 for b in boxes if b[4]),
    }


class HomeCounterTests(TestCase):
    def test_counters_equal_totals_from_the_full_item_list(self):
        w = World()
        standard(w)
        build_random(w, random.Random(7), 40)
        self.client.force_login(make_user('op', OPERATOR))
        with mock.patch('django.utils.timezone.now', return_value=w.now):
            counters = self.client.get('/').context['counters']
            items = list(OrderItem.objects.select_related('order'))
            want = expected_counters([(i, reference_item_status(i)) for i in items], w.now, timezone.localdate(w.now))
        self.assertEqual(counters, want)
        self.assertTrue(all(want.values()), want)  # every counter is exercised


PAGES = ['planning', '/', '/?n=100', '/boxes/', '/priorities/', '/shipping/', '/analysis/', '/orders/', '/orders/export/', '/dashboard/']


class QueryCountTests(TestCase):
    def count(self, url):
        if url == 'planning':
            with CaptureQueriesContext(connection) as q:
                process_averages()
            return len(q)
        self.client.get(url)  # warm up (session, caches)
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(url)
            if r.streaming:
                b''.join(r.streaming_content)
        self.assertEqual(r.status_code, 200, url)
        return len(q)

    def test_query_count_does_not_grow_with_the_number_of_items(self):
        w = World()
        standard(w)
        self.client.force_login(make_user('admin', 'Admin'))
        small = {u: self.count(u) for u in PAGES}
        build_random(w, random.Random(3), 30)
        large = {u: self.count(u) for u in PAGES}
        self.assertEqual(small, large)


class ScaleTests(TestCase):
    def seed(self, n):
        call_command('seed_scale', items=n, events=n * 20, seed=n, stdout=StringIO())

    def test_one_thousand_items(self):
        self.client.force_login(make_user('op', OPERATOR))
        now = timezone.now()
        self.seed(20)
        with mock.patch('django.utils.timezone.now', return_value=now):
            small = {n: self.measure(f'/?n={n}') for n in (50, 500)}
            self.seed(980)
            self.assertEqual(OrderItem.objects.count(), 1000)
            big = {n: self.measure(f'/?n={n}') for n in (50, 500)}
            self.assertEqual({n: big[n][0] for n in big}, {n: small[n][0] for n in small})
            ordered = OrderItem.objects.select_related('order').order_by('order__due_date', 'order__job_number', 'item_no')
            ref = [(i, reference_item_status(i)) for i in ordered]
            open_ids = [i.pk for i, st in ref if st.code not in CLOSED]
            for n in (50, 500):
                resp = self.client.get(f'/?n={n}')
                self.assertEqual([r['item'].pk for r in resp.context['matrix']['rows']], open_ids[:n])
                self.assertEqual(resp.context['counters'], expected_counters(ref, now, timezone.localdate(now)))
        print(f'\n1000 items: home {big[50][0]} queries / {big[50][1]:.0f} ms, n=500 {big[500][0]} queries / {big[500][1]:.0f} ms '
              f'(20 items: {small[50][0]} queries)')
        for n in (50, 500):
            self.assertLess(big[n][1], 500)

    def measure(self, url):
        self.client.get(url)
        with CaptureQueriesContext(connection) as q:
            t = time.perf_counter()
            self.client.get(url)
            ms = (time.perf_counter() - t) * 1000
        return len(q), ms


class SeededDataTests(TestCase):
    def test_every_scale_item_matches_the_reference(self):
        call_command('seed_scale', items=150, events=3000, stdout=StringIO())
        items = list(OrderItem.objects.all())
        self.assertGreater(Event.objects.count(), 3000)
        bad = first_difference(items, item_statuses(items))
        self.assertIsNone(bad, f'item {bad and bad[0].pk} differs in {bad and bad[1]}')

    @override_settings(DEBUG=True)
    def test_every_demo_item_matches_the_reference(self):
        call_command('seed_demo', stdout=StringIO())
        items = list(OrderItem.objects.all())
        self.assertTrue(items)
        bad = first_difference(items, item_statuses(items))
        self.assertIsNone(bad, f'item {bad and bad[0].pk} differs in {bad and bad[1]}')
