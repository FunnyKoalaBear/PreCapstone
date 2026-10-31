import re
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.utils import timezone

from core.testing import TestCase, make_user
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import DefectCause, Machine, ScannerDevice, Tag, TagAssignment, Worker

K = Process.Kind


def token_of(response):
    found = re.findall(r'action="/undo/([^/"]+)/"', response.content.decode())
    return found[-1] if found else None


class ToastTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def test_messages_render_with_level_classes_icons_and_the_live_region(self):
        self.client.post('/settings/?tab=workers', {'action': 'add', 'name': 'Sato'})
        r = self.client.post('/settings/?tab=workers', {'action': 'add', 'name': ''}, follow=True)
        html = r.content.decode()
        self.assertIn('class="message toast toast-error" data-level="error"', html)
        self.assertIn('class="toasts" id="toasts" aria-live="polite"', html)
        self.assertIn('class="toast-icon"', html)
        r = self.client.post('/settings/?tab=workers', {'action': 'add', 'name': 'Tanaka'}, follow=True)
        self.assertIn('class="message toast toast-success" data-level="success"', r.content.decode())

    def test_exactly_one_toast_container_and_the_scripts_are_loaded(self):
        for url in ('/', '/orders/', '/login/'):
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('id="toasts"'), 1, url)
            self.assertEqual(html.count('<dialog id="confirm-dialog"'), 1, url)
            self.assertIn('js/toast.js', html)
            self.assertIn('js/confirm.js', html)

    def test_toast_script_has_the_timings_limit_and_pause_rules(self):
        js = (settings.BASE_DIR / 'static' / 'js' / 'toast.js').read_text(encoding='utf-8')
        for part in ('success: 6', 'warning: 10', 'error: 0', 'LIMIT = 3', 'mouseenter', 'focusin', 'toast-close', 'FT.toast'):
            self.assertIn(part, js)


class UndoTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)

    def toggle_worker(self, worker):
        return self.client.post('/settings/?tab=workers', {'action': 'toggle', 'pk': worker.pk}, follow=True)

    def test_undo_reverses_a_worker_deactivation_and_writes_a_log_entry(self):
        worker = Worker.objects.create(name='Sato')
        r = self.toggle_worker(worker)
        token = token_of(r)
        self.assertTrue(token)
        self.assertContains(r, 'class="link-button">Undo</button>')
        worker.refresh_from_db()
        self.assertFalse(worker.active)
        r = self.client.post(f'/undo/{token}/', follow=True)
        worker.refresh_from_db()
        self.assertTrue(worker.active)
        self.assertContains(r, 'Reactivated &quot;Sato&quot;.')
        self.assertEqual(LogEntry.objects.filter(object_id=str(worker.pk)).order_by('-pk')[0].change_message, 'Undo: Reactivated.')
        self.assertEqual(self.client.post(f'/undo/{token}/', follow=True).status_code, 200)  # a second use does nothing
        worker.refresh_from_db()
        self.assertTrue(worker.active)

    def test_undo_works_for_machines_causes_processes_devices_and_users(self):
        machine, cause = Machine.objects.create(name='M1'), DefectCause.objects.create(name='Odd smell')
        process, device = Process.objects.create(name='Press', kind=K.MACHINING), ScannerDevice.objects.create(name='D1')
        victim = make_user('victim', 'Operator')
        cases = [
            (machine, lambda: self.client.post('/settings/?tab=machines', {'action': 'toggle', 'pk': machine.pk}, follow=True)),
            (cause, lambda: self.client.post('/settings/?tab=causes', {'action': 'toggle', 'pk': cause.pk}, follow=True)),
            (process, lambda: self.client.post('/orders/?tab=processes', {'action': 'deactivate', 'process': process.pk}, follow=True)),
            (device, lambda: self.client.post('/tags/devices/', {'action': 'toggle', 'device': device.pk}, follow=True)),
        ]
        for obj, act in cases:
            token = token_of(act())
            obj.refresh_from_db()
            self.assertFalse(obj.active, obj)
            self.client.post(f'/undo/{token}/')
            obj.refresh_from_db()
            self.assertTrue(obj.active, obj)
        r = self.client.post(f'/users/{victim.pk}/', {'name': '', 'role': 'Operator'}, follow=True)  # is_active left out = deactivate
        token = token_of(r)
        victim.refresh_from_db()
        self.assertFalse(victim.is_active)
        self.client.post(f'/undo/{token}/')
        victim.refresh_from_db()
        self.assertTrue(victim.is_active)
        self.assertIn('Undo: Changed: activated.', LogEntry.objects.filter(object_id=str(victim.pk)).order_by('-pk')[0].change_message)

    def test_undo_by_another_user_is_rejected(self):
        worker = Worker.objects.create(name='Sato')
        token = token_of(self.toggle_worker(worker))
        self.client.force_login(make_user('other', 'Planner'))
        r = self.client.post(f'/undo/{token}/', follow=True)
        self.assertContains(r, "This change can&#x27;t be undone any more.")
        worker.refresh_from_db()
        self.assertFalse(worker.active)
        # even a token copied into another session is refused
        self.client.force_login(self.planner)
        token = token_of(self.toggle_worker(Worker.objects.get(pk=worker.pk)))  # a fresh token after the new login
        session = self.client.session
        session['undo'][token]['user'] = 999999
        session.save()
        worker.refresh_from_db()
        before = worker.active
        self.client.post(f'/undo/{token}/')
        worker.refresh_from_db()
        self.assertEqual(worker.active, before)

    def test_undo_expires_after_sixty_seconds(self):
        worker = Worker.objects.create(name='Sato')
        token = token_of(self.toggle_worker(worker))
        with mock.patch('core.feedback.time.time', return_value=timezone.now().timestamp() + 61):
            r = self.client.post(f'/undo/{token}/', follow=True)
        self.assertContains(r, "can&#x27;t be undone any more")
        worker.refresh_from_db()
        self.assertFalse(worker.active)

    def test_undo_is_ignored_when_the_state_changed_again(self):
        worker = Worker.objects.create(name='Sato')
        token = token_of(self.toggle_worker(worker))
        self.toggle_worker(worker)  # reactivated by hand
        self.client.post(f'/undo/{token}/')
        worker.refresh_from_db()
        self.assertTrue(worker.active)  # the old Undo did not deactivate it again

    def test_operator_gets_403_on_the_toggle_endpoints_and_on_undo(self):
        worker = Worker.objects.create(name='Sato')
        token = token_of(self.toggle_worker(worker))
        self.client.force_login(make_user('op', 'Operator'))
        self.assertEqual(self.client.post('/settings/?tab=workers', {'action': 'toggle', 'pk': worker.pk}).status_code, 403)
        self.assertEqual(self.client.post('/tags/devices/', {'action': 'toggle', 'device': 1}).status_code, 403)
        self.assertEqual(self.client.post('/orders/?tab=processes', {'action': 'deactivate', 'process': 1}).status_code, 403)
        self.assertEqual(self.client.post(f'/undo/{token}/').status_code, 302)  # nothing in an Operator's session: refused with a message
        worker.refresh_from_db()
        self.assertFalse(worker.active)
        self.client.logout()
        self.assertEqual(self.client.post(f'/undo/{token}/').status_code, 302)  # to the login page

    def test_undo_is_not_offered_for_rename_delete_add_or_role_changes(self):
        worker = Worker.objects.create(name='Sato')
        victim = make_user('victim', 'Operator')
        posts = [
            self.client.post('/settings/?tab=workers', {'action': 'rename', 'pk': worker.pk, 'name': 'Sato2', 'name_ja': ''}, follow=True),
            self.client.post('/settings/?tab=workers', {'action': 'add', 'name': 'New'}, follow=True),
            self.client.post('/settings/?tab=workers', {'action': 'delete', 'pk': worker.pk}, follow=True),
            self.client.post(f'/users/{victim.pk}/', {'name': 'V', 'role': 'Planner', 'is_active': 'on'}, follow=True),
        ]
        for r in posts:
            self.assertIsNone(token_of(r))
        self.assertEqual(self.client.post(f'/users/{victim.pk}/reset/', follow=True).status_code, 200)


class ConfirmFormTests(TestCase):
    def setUp(self):
        self.planner = make_user('pl', 'Planner')
        self.client.force_login(self.planner)
        self.order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=5))
        self.item = OrderItem.objects.create(order=self.order, part_name='P', quantity=1)
        RouteStep.objects.create(item=self.item, sequence=1, process=Process.objects.create(name='Cut', kind=K.MACHINING))
        TagAssignment.objects.create(tag=Tag.objects.create(uid='CF1'), item=self.item, start_at=timezone.now() - timedelta(days=1))

    def html(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.content.decode()

    def test_delete_forms_name_the_item_and_are_danger_styled(self):
        Worker.objects.create(name='Sato')
        Process.objects.create(name='Spare', kind=K.MACHINING)
        ScannerDevice.objects.create(name='Spare device')
        for url, text in (('/settings/?tab=workers', 'Delete worker &quot;Sato&quot;? This can&#x27;t be undone.'),
                          ('/orders/?tab=processes', 'Delete process &quot;Spare&quot;? This can&#x27;t be undone.'),
                          ('/tags/devices/', 'Delete device &quot;Spare device&quot;? This can&#x27;t be undone.')):
            html = self.html(url)
            self.assertIn(f'data-confirm="{text}" data-confirm-danger', html, url)

    def test_reset_regenerate_deactivate_user_forms_are_confirmed(self):
        victim = make_user('victim', 'Operator')
        device = ScannerDevice.objects.create(name='Dev')
        self.assertIn('data-confirm="Reset the password of &quot;victim&quot;?', self.html(f'/users/{victim.pk}/reset/'))
        self.assertIn('data-confirm="Regenerate the key of &quot;Dev&quot;?', self.html(f'/tags/devices/{device.pk}/key/'))
        html = self.html(f'/users/{victim.pk}/')
        self.assertIn('data-confirm="Deactivate user &quot;victim&quot;?', html)
        self.assertIn('data-confirm-if-unchecked="is_active"', html)
        self.assertIn('<form method="post" class="card">', self.html('/users/add/'))  # adding a user needs no confirmation

    def test_release_replace_ship_and_cancel_forms_are_confirmed(self):
        html = self.html('/tags/?q=CF1')
        self.assertIn(f'data-confirm="Release tag CF1 from item {self.item.item_code}?', html)
        self.assertIn(f'data-confirm="Replace tag CF1 on item {self.item.item_code}?', html)
        html = self.html(f'/orders/{self.order.job_number}/')
        self.assertIn(f'data-confirm="Cancel item {self.item.item_code}?', html)

    def test_toggle_buttons_are_not_confirmed(self):
        Worker.objects.create(name='Sato')
        html = self.html('/settings/?tab=workers')
        toggle = re.search(r'<button[^>]*value="toggle"[^>]*>', html).group(0)
        self.assertNotIn('data-confirm', toggle)

    def test_confirm_script_supports_buttons_forms_escape_and_a_no_dialog_fallback(self):
        js = (settings.BASE_DIR / 'static' / 'js' / 'confirm.js').read_text(encoding='utf-8')
        for part in ('showModal', 'requestSubmit', 'data-confirm-danger', 'confirmIfUnchecked', "typeof dialog.showModal !== 'function'"):
            self.assertIn(part, js)
        html = self.client.get('/').content.decode()
        self.assertRegex(html, r'<button type="submit" value="cancel" class="button-secondary" autofocus>')  # focus starts on Cancel

