import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from core.testing import TestCase, make_user
from tracking.models import Event, ScannerDevice, Tag, TagAssignment
from tracking.services import record_event, ship_item

from . import importer
from .models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from .runs import previous_runs

JST = ZoneInfo('Asia/Tokyo')


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=JST)


class Base(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        vendor = Vendor.objects.create(code='00011', name='Milling shop', kind=Vendor.Kind.INTERNAL)
        self.mill = Process.objects.create(name='Mill', kind=Process.Kind.MACHINING, default_vendor=vendor)
        self.device = ScannerDevice.objects.create(name='D')
        self.acme = Customer.objects.create(name='Acme')
        self.n = 0

    def order(self, customer=None, due=date(2026, 6, 30)):
        return Order.objects.create(customer=customer or self.acme, due_date=due)

    def item(self, order, finished=None, part='Shaft', part_number=''):
        """An item with a one-step route; finished = JST datetime of its only FINISHED event (None: started only)."""
        self.n += 1
        item = OrderItem.objects.create(order=order, part_name=part, quantity=2, part_number=part_number)
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        uid = f'OC{self.n}'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item, start_at=at(date(2026, 1, 1)))
        start = (finished or at(date(2026, 3, 1))) - timedelta(hours=2)
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=start)
        if finished:
            record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=finished)
        return item

    def cancel(self, item):
        Event(event_type=Event.Type.CANCELLED, item=item, created_by=self.planner, reason='x').save()


class OrdersListTests(Base):
    def setUp(self):
        super().setUp()
        self.old = self.order()
        self.item(self.old, at(date(2026, 3, 2), 9))
        self.new = self.order()
        self.item(self.new, at(date(2026, 3, 9), 23))
        self.partly = self.order()
        self.item(self.partly, at(date(2026, 3, 5)))
        self.item(self.partly)  # still running
        self.shipped = self.order()
        ship_item(self.planner, self.item(self.shipped, at(date(2026, 3, 4))))
        self.cancelled = self.order()
        self.cancel(self.item(self.cancelled, at(date(2026, 3, 6))))
        self.open = self.order()
        self.item(self.open)

    def rows(self, **params):
        return {r['order'].pk: r for r in self.client.get('/orders/', params).context['rows']}

    def test_completed_column_is_filled_only_for_complete_or_shipped_orders(self):
        rows = self.rows()
        self.assertEqual({k: v['completed'] for k, v in rows.items()}, {
            self.old.pk: date(2026, 3, 2), self.new.pk: date(2026, 3, 9), self.shipped.pk: date(2026, 3, 4),
            self.partly.pk: None, self.cancelled.pk: None, self.open.pk: None,
        })

    def test_the_date_is_a_jst_date(self):
        self.assertEqual(self.rows()[self.new.pk]['completed'], date(2026, 3, 9))  # 23:00 JST is still the 9th

    def test_html_column_and_dates(self):
        html = self.client.get('/orders/').content.decode()
        self.assertIn('<th class="tnum">Completed</th>', html)
        self.assertEqual(html.count('data-label="Completed">2026-'), 3)
        self.assertEqual(html.count('data-label="Completed"></td>'), 3)

    def test_sort_by_recently_completed_puts_blanks_last(self):
        response = self.client.get('/orders/', {'sort': 'completed'})
        order = [r['order'].pk for r in response.context['rows']]
        self.assertEqual(order[:3], [self.new.pk, self.shipped.pk, self.old.pk])
        self.assertTrue(all(r['completed'] is None for r in response.context['rows'][3:]))
        self.assertEqual(order[3:], sorted(order[3:], key=lambda pk: -Order.objects.get(pk=pk).pk))  # blanks keep newest job number first
        html = response.content.decode()
        self.assertIn('<option value="completed" data-dir="desc" selected>Recently completed</option>', html)

    def test_sort_works_together_with_the_status_filter(self):
        rows = self.client.get('/orders/', {'sort': 'completed', 'status': 'COMPLETE'}).context['rows']
        self.assertEqual([r['order'].pk for r in rows], [self.new.pk, self.old.pk])

    def test_unknown_sort_still_falls_back_to_due(self):
        self.assertEqual(self.client.get('/orders/', {'sort': 'nope'}).context['sort'], 'due')


class OrderPageTests(Base):
    def test_complete_order_shows_the_date_next_to_the_status_and_on_the_item(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2)))
        self.item(order, at(date(2026, 3, 7)), part='Gear')
        html = self.client.get(f'/orders/{order.job_number}/').content.decode()
        self.assertEqual(html.count('Completed: 2026-03-07'), 2)  # the order (latest item) and Gear
        self.assertEqual(html.count('Completed: 2026-03-02'), 1)

    def test_partly_complete_order_has_no_order_date_but_its_finished_item_has_one(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2)))
        self.item(order, part='Gear')
        response = self.client.get(f'/orders/{order.job_number}/')
        self.assertIsNone(response.context['completed'])
        self.assertEqual([e['completed'] for e in response.context['items']], [date(2026, 3, 2), None])
        self.assertEqual(response.content.decode().count('Completed: '), 1)

    def test_cancelled_items_do_not_count_for_the_order(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2)))
        self.cancel(self.item(order, at(date(2026, 3, 9)), part='Gear'))
        response = self.client.get(f'/orders/{order.job_number}/')
        self.assertEqual(response.context['completed'], date(2026, 3, 2))
        self.assertEqual([e['completed'] for e in response.context['items']], [date(2026, 3, 2), None])

    def test_operator_sees_it_too(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2)))
        self.client.force_login(make_user('op', 'Operator'))
        self.assertContains(self.client.get(f'/orders/{order.job_number}/'), 'Completed: 2026-03-02')


class CustomerPageTests(Base):
    def test_orders_table_has_a_completed_column(self):
        done, open_ = self.order(), self.order()
        self.item(done, at(date(2026, 3, 2)))
        self.item(open_)
        response = self.client.get(f'/customers/{self.acme.pk}/')
        self.assertEqual({r['order'].pk: r['completed'] for r in response.context['rows']}, {done.pk: date(2026, 3, 2), open_.pk: None})
        html = response.content.decode()
        self.assertIn('<th>Completed</th>', html)
        self.assertIn('<td class="tnum">2026-03-02</td>', html)


class CsvCompletedTests(Base):
    def rows(self):
        raw = b''.join(self.client.get('/orders/export/').streaming_content).decode('utf-8-sig')
        return raw, list(csv.DictReader(io.StringIO(raw)))

    def test_completed_date_follows_status_and_is_blank_without_one(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2), 23), part='Done')
        self.item(order, part='Running')
        self.cancel(self.item(order, at(date(2026, 3, 3)), part='Gone'))
        shipped = self.item(order, at(date(2026, 3, 4)), part='Sent')
        ship_item(self.planner, shipped)
        raw, rows = self.rows()
        header = next(csv.reader(io.StringIO(raw)))
        self.assertEqual(header[-2:], ['status', 'completed_date'])
        got = {r['part_name']: (r['status'], r['completed_date']) for r in rows}
        self.assertEqual(got, {'Done': ('Complete', '2026-03-02'), 'Running': ('In progress', ''), 'Gone': ('Cancelled', ''), 'Sent': ('Shipped', '2026-03-04')})

    def test_an_exported_file_imports_again(self):
        order = self.order()
        self.item(order, at(date(2026, 3, 2)), part='Done')
        self.item(order, part='Running')
        raw, rows = self.rows()
        self.assertEqual(len(rows), 2)
        preview = importer.parse(raw)
        self.assertEqual((preview.file_errors, [r.errors for r in preview.rows]), ([], [[], []]))
        self.assertTrue(preview.ok)
        before = Order.objects.count()
        orders, items = importer.run_import(preview)
        self.assertEqual((orders, items, Order.objects.count()), (2, 2, before + 2))  # no customer order number: one order per row
        self.assertEqual(OrderItem.objects.filter(part_name='Done').count(), 2)


class PreviousRunsTotalDaysTests(Base):
    def run_of(self, current_part='P-1'):
        """The previous runs listed for a fresh item with the same part number."""
        current = self.item(self.order(), part_number=current_part)
        return previous_runs(current)

    def test_a_completed_run_counts_from_its_first_start_to_its_completion(self):
        run = self.item(self.order(), at(date(2026, 3, 4), 15), part_number='P-1')  # started 13:00 on the 4th
        uid = TagAssignment.objects.get(item=run).tag.uid
        # a later scan after completion (a stray repeat) must not stretch the run
        record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=at(date(2026, 3, 10)))
        runs = self.run_of()
        self.assertEqual(runs[0]['finished'], date(2026, 3, 4))
        self.assertEqual(runs[0]['total_days'], 0)

    def test_completion_minus_first_start_across_days(self):
        self.n += 1
        item = OrderItem.objects.create(order=self.order(), part_name='Long', quantity=1, part_number='P-1')
        RouteStep.objects.create(item=item, sequence=1, process=self.mill)
        uid = 'LONG1'
        TagAssignment.objects.create(tag=Tag.objects.create(uid=uid), item=item, start_at=at(date(2026, 1, 1)))
        record_event(self.device, uid, self.mill, 'STARTED', occurred_at=at(date(2026, 3, 1), 9))
        record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=at(date(2026, 3, 4), 17))
        record_event(self.device, uid, self.mill, 'FINISHED', occurred_at=at(date(2026, 3, 9), 9))  # repeat scan, 5 days later
        run = self.run_of()[0]
        self.assertEqual((run['finished'], run['total_days']), (date(2026, 3, 4), 3))

    def test_an_unfinished_run_keeps_the_old_behaviour(self):
        item = self.item(self.order(), part_number='P-1')
        uid = TagAssignment.objects.get(item=item).tag.uid
        record_event(self.device, uid, self.mill, 'PAUSED', occurred_at=at(date(2026, 3, 5)))
        run = self.run_of()[0]
        self.assertIsNone(run['finished'])
        self.assertEqual(run['total_days'], 4)  # first start (1 March 10:00) to the last event (5 March)
