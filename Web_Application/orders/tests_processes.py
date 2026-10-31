from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.utils import timezone

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep, Vendor
from tracking.models import Event

K = Process.Kind
URL = '/orders/?tab=processes'


class ProcessTabTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def save(self, pk='', **data):
        body = {'action': 'save', 'name': 'Press', 'name_ja': '', 'kind': K.MACHINING, **data}
        return self.client.post(URL, {**body, **({'process': pk} if pk else {})}, follow=True)

    def used(self, name='Used'):
        proc = Process.objects.create(name=name, kind=K.MACHINING)
        item = OrderItem.objects.create(
            order=Order.objects.create(customer=Customer.objects.get_or_create(name='C')[0], due_date=timezone.localdate()), part_name='P', quantity=1)
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        return proc, item

    def test_operator_gets_403_and_tab_is_hidden(self):
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.post(URL, {'action': 'save', 'name': 'X', 'kind': K.MACHINING}).status_code, 403)
        self.assertNotContains(self.client.get('/orders/'), 'tab=processes')

    def test_add_trims_and_logs(self):
        r = self.save(name='  Press  ', name_ja='プレス', stagnation_hours='12', vendor_lead_days='3')
        p = Process.objects.get(name='Press')
        self.assertEqual((p.name_ja, p.stagnation_hours, p.vendor_lead_days, p.active), ('プレス', 12, 3, True))
        self.assertContains(r, 'Added')
        self.assertTrue(LogEntry.objects.filter(object_id=str(p.pk), action_flag=ADDITION).exists())

    def test_duplicate_name_is_rejected_case_insensitively(self):
        Process.objects.create(name='Press', kind=K.MACHINING)
        self.assertContains(self.save(name=' press '), 'already exists')
        other = Process.objects.create(name='Saw', kind=K.MACHINING)
        self.assertContains(self.save(pk=other.pk, name='PRESS'), 'already exists')
        self.assertEqual(Process.objects.count(), 2)
        self.save(pk=other.pk, name='saw')  # its own name in another case is fine
        other.refresh_from_db()
        self.assertEqual(other.name, 'saw')

    def test_edit_changes_fields_and_logs(self):
        v = Vendor.objects.create(code='00131', name='Plating Co', kind=Vendor.Kind.VENDOR)
        p = Process.objects.create(name='Plating', kind=K.OUTSOURCED)
        self.save(pk=p.pk, name='Plating', kind=K.OUTSOURCED, default_vendor=v.pk, vendor_lead_days='7')
        p.refresh_from_db()
        self.assertEqual((p.default_vendor, p.vendor_lead_days), (v, 7))
        self.assertTrue(LogEntry.objects.filter(object_id=str(p.pk), action_flag=CHANGE).exists())

    def test_kind_is_locked_once_used_by_a_route_or_an_event(self):
        p, _item = self.used()
        self.save(pk=p.pk, name='Used', kind=K.OUTSOURCED)
        p.refresh_from_db()
        self.assertEqual(p.kind, K.MACHINING)
        self.assertContains(self.client.get(f'{URL}&edit={p.pk}'), 'cannot be changed')
        free = Process.objects.create(name='Free', kind=K.MACHINING)
        self.save(pk=free.pk, name='Free', kind=K.OUTSOURCED)
        free.refresh_from_db()
        self.assertEqual(free.kind, K.OUTSOURCED)
        scanned = Process.objects.create(name='Scanned', kind=K.MACHINING)
        Event.objects.create(tag_uid='X1', process=scanned, event_type=Event.Type.ARRIVED)
        self.save(pk=scanned.pk, name='Scanned', kind=K.MATERIAL)
        scanned.refresh_from_db()
        self.assertEqual(scanned.kind, K.MACHINING)

    def test_deactivate_keeps_old_routes_working_and_reactivate(self):
        p, item = self.used()
        self.client.post(URL, {'action': 'deactivate', 'process': p.pk})
        p.refresh_from_db()
        self.assertFalse(p.active)
        self.assertEqual(item.steps.get().process, p)  # the old route still points at it
        self.assertContains(self.client.get(f'/orders/{item.order.job_number}/'), 'Used')
        self.client.post(URL, {'action': 'reactivate', 'process': p.pk})
        p.refresh_from_db()
        self.assertTrue(p.active)

    def test_delete_only_when_unused_by_routes_and_events(self):
        used, _item = self.used()
        scanned = Process.objects.create(name='Scanned', kind=K.MACHINING)
        Event.objects.create(tag_uid='X1', process=scanned, event_type=Event.Type.ARRIVED)
        spare = Process.objects.create(name='Spare', kind=K.MACHINING)
        page = self.client.get(URL).content.decode()
        self.assertEqual(page.count('value="delete"'), 1)  # only the spare row
        for p in (used, scanned):
            self.assertContains(self.client.post(URL, {'action': 'delete', 'process': p.pk}, follow=True), 'cannot be deleted')
            self.assertTrue(Process.objects.filter(pk=p.pk).exists())
        self.client.post(URL, {'action': 'delete', 'process': spare.pk})
        self.assertFalse(Process.objects.filter(pk=spare.pk).exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(spare.pk), action_flag=DELETION).exists())

    def test_list_shows_route_count_and_searches(self):
        self.used()
        Process.objects.create(name='Other', name_ja='別', kind=K.MACHINING)
        rows = self.client.get(URL).context['processes']
        self.assertEqual({r.name: r.routes for r in rows}, {'Used': 1, 'Other': 0})
        self.assertEqual([r.name for r in self.client.get(URL + '&q=別').context['processes']], ['Other'])
