from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep

from .completed import completed_rows, period_range
from .models import Event, ScannerDevice, Tag, TagAssignment
from .services import correct_event, record_event, ship_item
from .shipping import shipping_overview
from .status import BoxStatus, ItemStatus, item_status, item_statuses, order_completed_at

JST = ZoneInfo('Asia/Tokyo')
TODAY = date(2026, 3, 11)  # a Wednesday: Monday is the 9th, Sunday the 8th, the month starts on the 1st
E = Event.Type


def at(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)


def fixed_today(day=TODAY):
    """Make 'today' (JST) a fixed date; other localdate calls keep working."""
    real = timezone.localdate
    return mock.patch('django.utils.timezone.localdate', lambda value=None, tz=None: day if value is None else real(value, tz))


class CompletedBase(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.mill = Process.objects.create(name='Mill', kind=Process.Kind.MACHINING)
        self.device = ScannerDevice.objects.create(name='D')
        self.n = 0

    def make(self, boxes=1, due=TODAY + timedelta(days=5), order=None, customer=None, part=None):
        """A one-step item with `boxes` tagged boxes; returns (item, [uids])."""
        self.n += 1
        order = order or Order.objects.create(customer=customer or Customer.objects.create(name=f'C{self.n}'), due_date=due)
        item = OrderItem.objects.create(order=order, part_name=part or f'P{self.n}', quantity=3, box_count=boxes)
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        uids = []
        for b in range(boxes):
            uids.append(f'CP{self.n}-{b}')
            TagAssignment.objects.create(tag=Tag.objects.create(uid=uids[-1]), item=item, start_at=at(date(2026, 1, 1)))
        return item, uids

    def finish(self, uid, when):
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=when - timedelta(hours=1))
        return record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=when)[0]

    def done(self, finished, **kw):
        """A complete single-box item finished at `finished`."""
        item, (uid,) = self.make(**kw)
        self.finish(uid, finished)
        return item


class CompletedAtTests(CompletedBase):
    def test_single_box_takes_the_last_finish(self):
        item, (uid,) = self.make()
        self.assertIsNone(item_status(item).completed_at)
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=at(TODAY, 8))
        self.assertIsNone(item_status(item).completed_at)  # still running
        self.finish(uid, at(TODAY, 10))
        st = item_status(item)
        self.assertEqual((st.code, st.completed_at, st.boxes[0].completed_at), ('COMPLETE', at(TODAY, 10), at(TODAY, 10)))

    def test_multi_box_waits_for_the_last_box_and_the_last_one_wins(self):
        item, (a, b) = self.make(boxes=2)
        self.finish(a, at(TODAY, 15))
        self.assertIsNone(item_status(item).completed_at)  # one box still open
        self.finish(b, at(TODAY - timedelta(days=2), 9))  # the second box finished earlier in time
        st = item_status(item)
        self.assertEqual((st.code, st.completed_at), ('COMPLETE', at(TODAY, 15)))
        self.assertEqual({x.completed_at for x in st.boxes}, {at(TODAY, 15), at(TODAY - timedelta(days=2), 9)})

    def test_voiding_the_final_finished_removes_the_date(self):
        item, (uid,) = self.make()
        last = self.finish(uid, at(TODAY, 10))
        correct_event(last, self.planner, 'wrong scan')
        st = item_status(item)
        self.assertEqual((st.code, st.completed_at), ('IN_PROGRESS', None))

    def test_voiding_a_finished_of_a_two_box_item_removes_the_date(self):
        item, (a, b) = self.make(boxes=2)
        self.finish(a, at(TODAY, 9))
        last = self.finish(b, at(TODAY, 10))
        self.assertEqual(item_status(item).completed_at, at(TODAY, 10))
        correct_event(last, self.planner, 'wrong scan')
        self.assertIsNone(item_status(item).completed_at)

    def test_voiding_the_first_finished_moves_the_date_to_the_next(self):
        item, (uid,) = self.make()
        first = self.finish(uid, at(TODAY, 10))
        record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=at(TODAY, 11))  # a repeat scan; does not move the date
        self.assertEqual(item_status(item).completed_at, at(TODAY, 10))
        correct_event(first, self.planner, 'wrong scan')
        self.assertEqual(item_status(item).completed_at, at(TODAY, 11))

    def test_a_shipped_item_keeps_its_date(self):
        item, (uid,) = self.make()
        self.finish(uid, at(TODAY, 10))
        ship_item(self.planner, item)
        st = item_status(item)
        self.assertEqual((st.code, st.completed_at), ('SHIPPED', at(TODAY, 10)))

    def test_a_cancelled_item_has_none(self):
        item, (uid,) = self.make()
        self.finish(uid, at(TODAY, 10))
        Event(event_type=E.CANCELLED, item=item, created_by=self.planner, reason='x').save()
        st = item_status(item)
        self.assertEqual((st.code, st.completed_at), ('CANCELLED', None))

    def test_dataclass_defaults(self):
        self.assertIsNone(BoxStatus(None, 1, 'X', 'X').completed_at)
        self.assertIsNone(ItemStatus('X', 'X').completed_at)

    def test_order_completed_at(self):
        order = Order.objects.create(customer=Customer.objects.create(name='O'), due_date=TODAY)
        a, b, gone = (self.make(order=order)[0] for _ in range(3))
        for item, hour in ((a, 9), (b, 13)):
            self.finish(TagAssignment.objects.get(item=item).tag.uid, at(TODAY, hour))
        order = Order.objects.get(pk=order.pk)
        statuses = lambda: item_statuses(order.items.all())
        self.assertIsNone(order_completed_at(order, statuses()))  # the third item is still open
        Event(event_type=E.CANCELLED, item=gone, created_by=self.planner, reason='x').save()
        self.assertEqual(order_completed_at(order, statuses()), at(TODAY, 13))  # cancelled items do not count
        ship_item(self.planner, a)
        ship_item(self.planner, b)
        self.assertEqual(order_completed_at(order, statuses()), at(TODAY, 13))  # all shipped: still dated
        self.assertIsNone(order_completed_at(Order.objects.create(customer=order.customer, due_date=TODAY), {}))  # no items


class PeriodTests(TestCase):
    def test_week_starts_on_monday(self):
        self.assertEqual(period_range('week', date(2026, 3, 9)), (date(2026, 3, 9), date(2026, 3, 9)))  # Monday
        self.assertEqual(period_range('week', date(2026, 3, 15)), (date(2026, 3, 9), date(2026, 3, 15)))  # Sunday
        self.assertEqual(period_range('week', date(2026, 3, 8)), (date(2026, 3, 2), date(2026, 3, 8)))  # the Sunday before

    def test_today_month_and_unknown(self):
        self.assertEqual(period_range('today', TODAY), (TODAY, TODAY))
        self.assertEqual(period_range('month', date(2026, 3, 31)), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertIsNone(period_range('year', TODAY))
        self.assertIsNone(period_range('', TODAY))


class HomeCompleteBoxTests(CompletedBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user('op', 'Operator'))
        # completion times around the JST midnights; 00:00 JST on the 11th is still the 10th in UTC
        for day, hour, minute in ((11, 0, 0), (10, 23, 59), (9, 0, 0), (8, 23, 59), (1, 0, 0)):
            self.done(at(date(2026, 3, day), hour, minute))
        self.done(at(date(2026, 2, 28), 23, 59))

    def count(self, today=TODAY, **params):
        with fixed_today(today):
            return self.client.get('/', params).context['counters']['complete']

    def test_counts_per_period_on_a_wednesday(self):
        self.assertEqual([self.count(done=p) for p in ('today', 'week', 'month')], [1, 3, 5])

    def test_default_period_is_today(self):
        self.assertEqual(self.count(), 1)

    def test_sunday_counts_the_whole_week_back_to_monday(self):
        self.assertEqual([self.count(date(2026, 3, 15), done=p) for p in ('today', 'week', 'month')], [0, 3, 5])

    def test_monday_starts_a_new_week(self):
        self.assertEqual([self.count(date(2026, 3, 9), done=p) for p in ('today', 'week', 'month')], [1, 1, 3])  # the 10th and 11th are still in the future

    def test_the_last_day_of_the_month_ends_at_jst_midnight(self):
        self.assertEqual(self.count(date(2026, 2, 28), done='today'), 1)
        self.assertEqual(self.count(date(2026, 3, 1), done='today'), 1)  # 00:00 JST on the 1st belongs to March

    def test_a_shipped_item_still_counts_and_a_cancelled_one_does_not(self):
        shipped = self.done(at(TODAY, 1))
        ship_item(self.planner, shipped)
        gone = self.done(at(TODAY, 2))
        Event(event_type=E.CANCELLED, item=gone, created_by=self.planner, reason='x').save()
        self.assertEqual(self.count(done='today'), 2)

    def test_the_period_is_remembered_in_the_session(self):
        self.assertEqual(self.count(done='week'), 3)
        self.assertEqual(self.client.session['done'], 'week')
        self.assertEqual(self.count(), 3)  # no parameter: the remembered one
        self.assertEqual(self.count(done='month'), 5)
        self.assertEqual(self.count(), 5)

    def test_an_invalid_period_falls_back_to_today_and_is_not_remembered(self):
        self.count(done='week')
        with fixed_today():
            ctx = self.client.get('/', {'done': 'year'}).context
        self.assertEqual((ctx['done_period'], ctx['counters']['complete']), ('today', 1))
        self.assertEqual(self.client.session['done'], 'week')
        session = self.client.session
        session['done'] = 'bogus'
        session.save()
        self.assertEqual(self.count(), 1)

    def test_the_card_has_a_select_and_links_to_the_completed_page(self):
        with fixed_today():
            html = self.client.get('/', {'done': 'week', 'status': 'waiting'}).content.decode()
        self.assertIn('href="/completed/?period=week"', html)
        self.assertIn('<select name="done"', html)
        self.assertIn('onchange="this.form.submit()"', html)
        self.assertIn('<option value="week" selected>This week</option>', html)
        for label in ('Today', 'This week', 'This month'):
            self.assertIn(f'>{label}</option>', html)
        self.assertIn('type="hidden" name="status" value="waiting"', html)  # the other filters stay
        self.assertNotIn('type="hidden" name="done"', html)
        self.assertNotIn('Apply</button>\n    </form>\n  </div>', html)

    def test_query_count_is_unchanged(self):
        # 14 queries were measured for this page before the Complete box existed
        self.client.get('/')
        for params in ({}, {'done': 'week'}, {'done': 'month'}):
            with CaptureQueriesContext(connection) as q:
                self.client.get('/', params)
            self.assertEqual(len(q), 14, params)


class CompletedPageTests(CompletedBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(make_user('op', 'Operator'))
        self.acme, self.beta = Customer.objects.create(name='Acme'), Customer.objects.create(name='Beta')
        self.old = self.done(at(date(2026, 3, 1), 9), customer=self.acme, part='Gear', due=date(2026, 3, 5))  # on time
        self.mid = self.done(at(date(2026, 3, 10), 9), customer=self.beta, part='Shaft', due=date(2026, 3, 9))  # a day late
        self.new = self.done(at(TODAY, 9), customer=self.acme, part='Bolt', due=TODAY)  # due the day it finished: on time
        self.open, (uid,) = self.make(customer=self.beta, part='Open')
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=at(TODAY, 8))
        self.gone = self.done(at(TODAY, 8), customer=self.beta, part='Gone')
        Event(event_type=E.CANCELLED, item=self.gone, created_by=self.planner, reason='x').save()

    def get(self, **params):
        with fixed_today():
            return self.client.get('/completed/', params)

    def parts(self, **params):
        return [r['item'].part_name for r in self.get(**params).context['rows']]

    def test_permissions(self):
        self.client.logout()
        self.assertRedirects(self.client.get('/completed/'), '/login/?next=/completed/')
        self.client.force_login(make_user('nobody'))
        self.assertEqual(self.client.get('/completed/').status_code, 403)
        self.client.force_login(make_user('op'))
        self.assertEqual(self.client.get('/completed/').status_code, 200)

    def test_lists_only_completed_items_newest_first(self):
        self.assertEqual(self.parts(), ['Bolt', 'Shaft', 'Gear'])

    def test_on_time_late_and_shipped_badges(self):
        ship_item(self.planner, self.mid)
        rows = {r['item'].part_name: r for r in self.get().context['rows']}
        self.assertEqual([(rows[p]['late'], rows[p]['shipped']) for p in ('Gear', 'Shaft', 'Bolt')], [(False, False), (True, True), (False, False)])
        html = self.get().content.decode()
        self.assertEqual((html.count('>Late</span>'), html.count('>On time</span>'), html.count('>Shipped</span>')), (1, 2, 1))
        self.assertIn('<td class="tnum" data-label="Completed">2026-03-10</td>', html)
        self.assertEqual(html.count('class="scroll-panel"'), 1)

    def test_completed_date_range(self):
        self.assertEqual(self.parts(done_from='2026-03-10'), ['Bolt', 'Shaft'])
        self.assertEqual(self.parts(done_to='2026-03-09'), ['Gear'])
        self.assertEqual(self.parts(done_from='2026-03-10', done_to='2026-03-10'), ['Shaft'])

    def test_customer_and_search(self):
        self.assertEqual(self.parts(customer=self.acme.pk), ['Bolt', 'Gear'])
        self.assertEqual(self.parts(q='sha'), ['Shaft'])
        self.assertEqual(self.parts(customer=self.acme.pk, q='shaft'), [])
        self.assertEqual(self.parts(customer='x'), ['Bolt', 'Shaft', 'Gear'])  # not a number: ignored

    def test_period_prefills_the_dates(self):
        for period, parts, first in (('today', ['Bolt'], '2026-03-11'), ('week', ['Bolt', 'Shaft'], '2026-03-09'), ('month', ['Bolt', 'Shaft', 'Gear'], '2026-03-01')):
            response = self.get(period=period)
            self.assertEqual([r['item'].part_name for r in response.context['rows']], parts, period)
            html = response.content.decode()
            self.assertIn(f'id="done_from" name="done_from" value="{first}"', html)
            self.assertIn('id="done_to" name="done_to" value="2026-03-11"', html)

    def test_typed_dates_beat_the_period_and_bad_input_is_ignored(self):
        self.assertEqual(self.parts(period='today', done_from='2026-03-01', done_to='2026-03-05'), ['Gear'])
        self.assertEqual(self.parts(period='year'), ['Bolt', 'Shaft', 'Gear'])
        self.assertEqual(self.parts(done_from='nope'), ['Bolt', 'Shaft', 'Gear'])

    def test_empty_result_has_a_message(self):
        self.assertIn('No completed items match.', self.get(q='zzz').content.decode())

    def test_filter_form_matches_the_shipping_style(self):
        html = self.get().content.decode()
        for name in ('q', 'customer', 'done_from', 'done_to'):
            self.assertIn(f'name="{name}"', html)
        self.assertIn('<form method="get" class="filters">', html)

    def test_home_stays_the_active_nav_item(self):
        html = self.get().content.decode()
        self.assertIn('href="/" title="Home" aria-label="Home" aria-current="page"', html)


class ShippingCompletedTests(CompletedBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.planner)
        self.acme = Customer.objects.create(name='Acme')
        self.a = self.done(at(date(2026, 3, 5), 9), customer=self.acme, part='Early', due=date(2026, 3, 20))
        self.b = self.done(at(date(2026, 3, 10), 9), part='Late', due=date(2026, 3, 12))
        self.c = self.done(at(date(2026, 3, 8), 9), part='Middle', due=date(2026, 3, 15))
        ship_item(self.planner, self.c)

    def ready(self, **kw):
        with fixed_today():
            return shipping_overview(**kw)[0]

    def test_rows_carry_the_completion_date_and_days_waiting(self):
        rows = {r['item'].part_name: r for r in self.ready()}
        self.assertEqual((rows['Early']['completed'], rows['Early']['waiting']), (date(2026, 3, 5), 6))
        self.assertEqual((rows['Late']['completed'], rows['Late']['waiting']), (date(2026, 3, 10), 1))
        self.assertNotIn('Middle', rows)  # shipped

    def test_order_stays_by_due_date(self):
        self.assertEqual([r['item'].part_name for r in self.ready()], ['Late', 'Early'])

    def test_completed_filters(self):
        parts = lambda **kw: [r['item'].part_name for r in self.ready(**kw)]
        self.assertEqual(parts(done_from=date(2026, 3, 6)), ['Late'])
        self.assertEqual(parts(done_to=date(2026, 3, 9)), ['Early'])
        self.assertEqual(parts(done_from=date(2026, 3, 5), done_to=date(2026, 3, 5)), ['Early'])
        self.assertEqual(parts(done_from=date(2026, 3, 11)), [])
        self.assertEqual(parts(done_from=date(2026, 3, 1), ready_customer=self.acme.pk), ['Early'])

    def test_page_has_the_columns_and_filters(self):
        with fixed_today():
            html = self.client.get('/shipping/', {'done_from': '2026-03-06'}).content.decode()
        for text in ('>Completed</th>', '>Waiting</th>', 'name="done_from"', 'name="done_to"', 'value="2026-03-06"', '<td class="num" data-label="Waiting">1</td>'):
            self.assertIn(text, html)
        self.assertIn('type="hidden" name="done_from" value="2026-03-06"', html)  # the month form keeps it
        self.assertNotIn('Early', html)

    def test_bad_completed_dates_are_ignored(self):
        with fixed_today():
            self.assertEqual(self.client.get('/shipping/', {'done_from': 'abc', 'done_to': '2026-13-45'}).status_code, 200)
