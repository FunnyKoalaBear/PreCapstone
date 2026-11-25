from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from core.navigation import back_target
from core.testing import make_user

from .models import Event, TagAssignment
from .services import cancel_item, correct_event
from .status import item_status
from .tests_completed import CompletedBase, at

JST = ZoneInfo('Asia/Tokyo')
E = Event.Type


class NavBase(CompletedBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.planner)

    def complete(self, due=date(2026, 6, 30)):
        """A complete item (finished in March 2026)."""
        return self.done(at(date(2026, 3, 2)), due=due)

    def ship_at(self, item, when, user=None):
        return Event.objects.create(event_type=E.SHIPPED, item=item, occurred_at=when, created_by=user or self.planner)


class BackMarkerTests(NavBase):
    def page(self, item, marker):
        return self.client.get(f'/orders/{item.order.job_number}/', {'from': marker} if marker is not None else {})

    def test_valid_markers_give_a_back_link_with_their_filters(self):
        item, _uids = self.make()
        for marker, label in [('/shipping/?r_q=abc', 'Shipping'), ('/?done=1m', 'Home'), ('/boxes/?status=waiting', 'Waiting'),
                              ('/completed/', 'Completed'), ('/priorities/', 'Priorities'), ('/orders/', 'Orders'), ('/tags/', 'Tags')]:
            r = self.page(item, marker)
            self.assertContains(r, f'href="{marker}">&larr; Back to {label}', msg_prefix=marker)

    def test_bad_markers_are_ignored(self):
        item, _uids = self.make()
        for marker in ['//evil.example', 'https://evil.example', '/admin/', '/nowhere/', '/\\evil.example', '', 'javascript:alert(1)']:
            r = self.page(item, marker)
            self.assertContains(r, 'Back to Orders', msg_prefix=marker)
            self.assertNotContains(r, 'href="//evil', msg_prefix=marker)
            self.assertNotContains(r, 'href="https://evil', msg_prefix=marker)
        self.assertIsNone(back_target('/matrix/'))

    def test_marker_survives_a_cancel_action(self):
        item, _uids = self.make()
        r = self.client.post(f'/items/{item.pk}/action/', {'action': 'cancel', 'reason': 'x', 'from': '/shipping/?r_q=abc'}, follow=True)
        self.assertContains(r, 'href="/shipping/?r_q=abc">&larr; Back to Shipping')
        self.assertContains(r, 'cancelled')

    def test_listing_links_carry_the_marker(self):
        self.complete()
        self.assertContains(self.client.get('/shipping/'), 'from=%2Fshipping%2F')
        self.assertContains(self.client.get('/completed/'), 'from=%2Fcompleted%2F')

    def test_marker_is_kept_on_the_edit_link(self):
        item, _uids = self.make()
        r = self.page(item, '/shipping/')
        self.assertContains(r, f'/orders/{item.order.job_number}/edit/?from=%2Fshipping%2F')


class ShippingRowTests(NavBase):
    def test_planner_sees_the_button_and_operator_has_no_access(self):
        item = self.complete()
        self.assertContains(self.client.get('/shipping/'), 'Mark as shipped')
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get('/shipping/').status_code, 403)

    def test_ship_returns_to_shipping_with_the_filter(self):
        item = self.complete()
        url = '/shipping/?r_q=' + item.part_name
        r = self.client.post(f'/items/{item.pk}/action/', {'action': 'ship', 'next': url})
        self.assertRedirects(r, url, fetch_redirect_response=False)
        r = self.client.get(url)
        self.assertContains(r, 'marked as shipped')
        self.assertNotContains(r, 'Mark as shipped')  # gone from Ready to ship
        self.assertContains(r, item.item_code)  # listed under shipped items for the current month

    def test_unsafe_next_falls_back_to_the_order_page(self):
        item = self.complete()
        for nxt in ['https://evil.example/', '//evil.example', '/admin/', '/nowhere/']:
            Event.objects.filter(event_type=E.SHIPPED).delete()
            r = self.client.post(f'/items/{item.pk}/action/', {'action': 'ship', 'next': nxt})
            self.assertRedirects(r, f'/orders/{item.order.job_number}/', fetch_redirect_response=False)


class ShippedItemsTests(NavBase):
    def get(self, **params):
        return self.client.get('/shipping/', params)

    def shipped(self, when, due=date(2026, 6, 30)):
        item = self.complete(due=due)
        self.ship_at(item, when)
        return item

    def test_year_month_and_all_months_filters(self):
        mar = self.shipped(datetime(2026, 3, 10, 12, tzinfo=JST))
        apr = self.shipped(datetime(2026, 4, 10, 12, tzinfo=JST))
        old = self.shipped(datetime(2025, 3, 10, 12, tzinfo=JST))
        r = self.get(s_year=2026, s_month=3)
        self.assertEqual([x['item'] for x in r.context['shipped']], [mar])
        r = self.get(s_year=2026, s_month=0)
        self.assertEqual([x['item'] for x in r.context['shipped']], [apr, mar])  # newest first
        r = self.get(s_year=2025, s_month=3)
        self.assertEqual([x['item'] for x in r.context['shipped']], [old])
        self.assertEqual(r.context['years'], sorted({timezone.localdate().year, 2026, 2025}, reverse=True))

    def test_jst_month_boundary(self):
        late_march = self.shipped(datetime(2026, 3, 31, 23, 30, tzinfo=JST))  # 14:30 UTC, still March in both
        early_april = self.shipped(datetime(2026, 4, 1, 0, 30, tzinfo=JST))  # 15:30 UTC on 31 March, but April in JST
        self.assertEqual([x['item'] for x in self.get(s_year=2026, s_month=3).context['shipped']], [late_march])
        self.assertEqual([x['item'] for x in self.get(s_year=2026, s_month=4).context['shipped']], [early_april])

    def test_undone_shipment_is_excluded(self):
        item = self.complete()
        event = self.ship_at(item, datetime(2026, 3, 10, 12, tzinfo=JST))
        correct_event(event, self.planner, 'oops')
        self.assertEqual(self.get(s_year=2026, s_month=3).context['shipped'], [])
        self.assertContains(self.get(s_year=2026, s_month=3), 'Nothing was shipped in this period.')

    def test_years_include_the_current_year_without_shipments(self):
        self.assertEqual(self.get().context['years'], [timezone.localdate().year])

    def test_invalid_params_fall_back_to_the_current_month(self):
        today = timezone.localdate()
        for params in [{'s_year': 'x', 's_month': 'y'}, {'s_year': 99999, 's_month': 13}, {}]:
            ctx = self.get(**params).context
            self.assertEqual((ctx['s_year'], ctx['s_month']), (today.year, today.month), params)

    def test_summary_and_on_time_badge(self):
        on_time = self.shipped(datetime(2026, 3, 10, 12, tzinfo=JST), due=date(2026, 3, 10))  # due day itself is on time
        late = self.shipped(datetime(2026, 3, 12, 12, tzinfo=JST), due=date(2026, 3, 10))
        r = self.get(s_year=2026, s_month=3)
        rows = {x['item']: x for x in r.context['shipped']}
        self.assertEqual((rows[on_time]['on_time'], rows[late]['on_time'], rows[late]['late_days']), (True, False, 2))
        self.assertContains(r, '2 items shipped, 1 on time (50%)')
        self.assertContains(r, 'Late by 2 days')

    def test_monthly_table_link_selects_that_month(self):
        self.shipped(datetime(2026, 3, 10, 12, tzinfo=JST))
        r = self.get()
        self.assertEqual(r.context['months'][0]['url'], '?s_year=2026&s_month=3#shipped-items')

    def test_other_filters_are_kept_by_the_shipped_form(self):
        r = self.get(r_q='abc', s_year=2026)
        self.assertIn(('r_q', 'abc'), r.context['shipped_hidden'])
        self.assertIn(('s_year', '2026'), r.context['ready_hidden'])


class ReleaseSelectedTests(NavBase):
    def linked(self):
        item, _u = self.make()
        TagAssignment.objects.filter(item=item).update(start_at=timezone.now() - timedelta(days=2))
        return item, TagAssignment.objects.get(item=item)

    def post(self, ids, **extra):
        return self.client.post('/tags/', {'action': 'release_selected', 'assignment': ids, **extra}, follow=True)

    def test_releases_only_the_ticked_rows_and_skips_unknown_or_ended(self):
        (_i1, a1), (_i2, a2), (_i3, a3) = self.linked(), self.linked(), self.linked()
        TagAssignment.objects.filter(pk=a3.pk).update(end_at=timezone.now())
        r = self.post([a1.pk, a3.pk, 999999])
        self.assertContains(r, 'Released 1 tags; 2 skipped')
        self.assertIsNotNone(TagAssignment.objects.get(pk=a1.pk).end_at)
        self.assertIsNone(TagAssignment.objects.get(pk=a2.pk).end_at)
        self.assertContains(self.post([a2.pk]), 'Released 1 tags.')

    def test_operator_cannot_release(self):
        _i, a = self.linked()
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.post('/tags/', {'action': 'release_selected', 'assignment': [a.pk]}).status_code, 403)
        self.assertIsNone(TagAssignment.objects.get(pk=a.pk).end_at)

    def test_filters_list_the_right_rows(self):
        working, aw = self.linked()
        done = self.done(timezone.now() - timedelta(hours=1))
        TagAssignment.objects.filter(item=done).update(start_at=timezone.now() - timedelta(days=2))
        cancelled, ac = self.linked()
        cancel_item(self.planner, cancelled, 'x')
        TagAssignment.objects.filter(pk=ac.pk).update(end_at=None)  # old data: still linked
        def rows(show):
            return {a.item for a in self.client.get('/tags/', {'show': show}).context['open']}
        self.assertEqual(rows('all'), {working, done, cancelled})
        self.assertEqual(rows('closed'), {cancelled})
        self.assertEqual(rows('complete'), {done})
        self.assertEqual(self.client.get('/tags/').context['open'], [])

    def test_working_rows_are_marked_for_the_warning_line(self):
        working, aw = self.linked()
        r = self.client.get('/tags/', {'show': 'all'})
        self.assertContains(r, 'data-working')
        self.assertContains(r, 'Release selected')
        self.assertContains(r, 'of them are still being worked on')

    def test_release_closed_is_gone_and_a_complete_item_stays_complete(self):
        done = self.done(timezone.now() - timedelta(hours=1))
        TagAssignment.objects.filter(item=done).update(start_at=timezone.now() - timedelta(days=2))
        a = TagAssignment.objects.get(item=done)
        self.client.post('/tags/', {'action': 'release_closed'})
        self.assertIsNone(TagAssignment.objects.get(pk=a.pk).end_at)
        self.post([a.pk])
        self.assertEqual(item_status(done).code, 'COMPLETE')
