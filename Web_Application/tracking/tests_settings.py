from datetime import timedelta

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.utils import timezone

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.forms import ResultForm
from tracking.models import DefectCause, Event, Machine, Tag, TagAssignment, Worker

K, E = Process.Kind, Event.Type
MODELS = {'workers': Worker, 'machines': Machine, 'causes': DefectCause}


class SettingsPageTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)

    def post(self, tab, **data):
        return self.client.post(f'/settings/?tab={tab}', data, follow=True)

    def used_worker(self, name='Sato'):
        """A worker with one saved result, plus its order item."""
        worker = Worker.objects.create(name=name, name_ja='佐藤')
        proc = Process.objects.get_or_create(name='Mill', kind=K.MACHINING)[0]
        item = OrderItem.objects.create(
            order=Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=5)),
            part_name='P', quantity=5)
        RouteStep.objects.create(item=item, sequence=1, process=proc)
        TagAssignment.objects.create(tag=Tag.objects.create(uid='ST1'), item=item, start_at=timezone.now() - timedelta(days=9))
        event = Event(tag_uid='ST1', process=proc, event_type=E.RESULT, quantity=5, worker=worker, created_by=self.planner,
                      occurred_at=timezone.now() - timedelta(days=1))
        event.full_clean()
        event.save()
        return worker, event, item

    def test_operator_gets_403_and_anonymous_is_redirected(self):
        self.client.force_login(make_user('op', 'Operator'))
        for tab in MODELS:
            self.assertEqual(self.client.get(f'/settings/?tab={tab}').status_code, 403)
        self.assertEqual(self.client.post('/settings/', {'action': 'add', 'name': 'X'}).status_code, 403)
        self.assertFalse(Worker.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.get('/settings/').status_code, 302)

    def test_each_tab_adds_trimmed_names_and_logs(self):
        for tab, model in MODELS.items():
            r = self.post(tab, action='add', name='  Alpha  ', name_ja='アルファ')
            obj = model.objects.get(name='Alpha')
            self.assertEqual(obj.name_ja, 'アルファ')
            self.assertContains(r, 'Added')
            self.assertTrue(LogEntry.objects.filter(object_id=str(obj.pk), action_flag=ADDITION).exists())

    def test_japanese_name_is_optional_and_name_is_required(self):
        self.post('machines', action='add', name='Press', name_ja='')
        self.assertEqual(Machine.objects.get(name='Press').name_ja, '')
        self.post('machines', action='add', name='   ')
        self.assertEqual(Machine.objects.count(), 1)

    def test_duplicate_is_rejected_case_insensitively_with_a_friendly_message(self):
        Worker.objects.create(name='Sato')
        r = self.post('workers', action='add', name=' sato ')
        self.assertContains(r, 'already exists')
        self.assertEqual(Worker.objects.count(), 1)
        other = Worker.objects.create(name='Ito')
        self.assertContains(self.post('workers', action='rename', pk=other.pk, name='SATO', name_ja=''), 'already exists')
        other.refresh_from_db()
        self.assertEqual(other.name, 'Ito')

    def test_the_same_name_may_exist_in_another_tab(self):
        Worker.objects.create(name='Shared')
        self.post('machines', action='add', name='Shared')
        self.assertTrue(Machine.objects.filter(name='Shared').exists())

    def test_rename_keeps_history_and_keeps_own_name_valid(self):
        worker, event, item = self.used_worker()
        self.post('workers', action='rename', pk=worker.pk, name='Sato Keiko', name_ja='佐藤 恵子')
        event.refresh_from_db()
        self.assertEqual(event.worker.name, 'Sato Keiko')
        self.assertContains(self.client.get(f'/results/{event.pk}/'), 'Sato Keiko')
        self.post('workers', action='rename', pk=worker.pk, name='sato keiko', name_ja='')  # only the case changes
        worker.refresh_from_db()
        self.assertEqual(worker.name, 'sato keiko')
        self.assertTrue(LogEntry.objects.filter(object_id=str(worker.pk), action_flag=CHANGE).exists())

    def test_deactivate_hides_from_result_form_but_old_results_stay_readable(self):
        worker, event, item = self.used_worker()
        self.assertIn(worker, ResultForm(item).fields['worker'].queryset)
        self.post('workers', action='toggle', pk=worker.pk)
        worker.refresh_from_db()
        self.assertFalse(worker.active)
        self.assertNotIn(worker, ResultForm(item).fields['worker'].queryset)
        self.assertContains(self.client.get(f'/results/{event.pk}/'), 'Sato')
        self.assertContains(self.client.get('/settings/?tab=workers'), 'Inactive')
        self.post('workers', action='toggle', pk=worker.pk)
        worker.refresh_from_db()
        self.assertTrue(worker.active)

    def test_delete_only_when_unused(self):
        worker, _event, _item = self.used_worker()
        spare = Worker.objects.create(name='Spare')
        page = self.client.get('/settings/?tab=workers').content.decode()
        self.assertEqual(page.count('value="delete"'), 1)  # only the unused row has the button
        self.assertContains(self.post('workers', action='delete', pk=worker.pk), 'cannot be deleted')  # forced POST is refused too
        self.assertTrue(Worker.objects.filter(pk=worker.pk).exists())
        self.post('workers', action='delete', pk=spare.pk)
        self.assertFalse(Worker.objects.filter(pk=spare.pk).exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(spare.pk), action_flag=DELETION).exists())

    def test_used_in_counts_events(self):
        self.used_worker()
        rows = self.client.get('/settings/?tab=workers').context['rows']
        self.assertEqual([(r.name, r.used) for r in rows], [('Sato', 1)])

    def test_search_filters_by_either_name(self):
        Worker.objects.create(name='Sato', name_ja='佐藤')
        Worker.objects.create(name='Ito', name_ja='伊藤')
        names = lambda q: [r.name for r in self.client.get('/settings/', {'tab': 'workers', 'q': q}).context['rows']]
        self.assertEqual(names('sat'), ['Sato'])
        self.assertEqual(names('伊'), ['Ito'])

    def test_paste_a_list_adds_new_and_reports_skipped(self):
        Machine.objects.create(name='Lathe 1')
        r = self.post('machines', action='paste', lines='Lathe 1\nPress A, プレスA\n\nlathe 1\nPress A\nSaw，丸のこ\n')
        self.assertEqual(sorted(Machine.objects.values_list('name', 'name_ja')), [('Lathe 1', ''), ('Press A', 'プレスA'), ('Saw', '丸のこ')])
        self.assertContains(r, 'Added 2, skipped 3')
        self.assertEqual(LogEntry.objects.filter(action_flag=ADDITION).count(), 2)

    def test_unknown_tab_shows_workers_and_nav_link_for_planners(self):
        self.assertEqual(self.client.get('/settings/?tab=nope').context['tab'], 'workers')
        self.assertContains(self.client.get('/'), 'href="/settings/"')
        self.client.force_login(make_user('op', 'Operator'))
        self.assertNotContains(self.client.get('/'), 'href="/settings/"')
