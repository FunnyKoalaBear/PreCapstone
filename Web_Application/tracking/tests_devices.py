import json
from datetime import timedelta

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.utils import timezone

from core.rules import silent_after
from core.testing import TestCase, make_user
from tracking.models import Event, ScannerDevice


class DeviceTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def add(self, name='Bench 1', location='Hall'):
        return self.client.post('/tags/devices/add/', {'name': name, 'location': location})

    def scan(self, key):
        return self.client.post('/api/scan/', json.dumps({'tag_uid': 'NOPE'}), content_type='application/json', HTTP_X_API_KEY=key)

    def test_operator_gets_403_everywhere(self):
        d = ScannerDevice.objects.create(name='D')
        self.client.force_login(make_user('op', 'Operator'))
        for url in ('/tags/devices/', '/tags/devices/add/', f'/tags/devices/{d.pk}/', f'/tags/devices/{d.pk}/key/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(f'/tags/devices/{d.pk}/key/').status_code, 403)
        self.assertEqual(self.client.get('/tags/').status_code, 403)

    def test_key_is_shown_once_on_create_and_never_in_list_or_edit(self):
        r = self.add()
        d = ScannerDevice.objects.get(name='Bench 1')
        self.assertContains(r, d.api_key)
        self.assertContains(r, 'scanner/setter settings')
        self.assertIn('no-store', r['Cache-Control'])
        for url in ('/tags/devices/', f'/tags/devices/{d.pk}/', f'/tags/devices/{d.pk}/key/', '/tags/', '/dashboard/'):
            self.assertNotContains(self.client.get(url), d.api_key, msg_prefix=url)

    def test_key_never_goes_into_the_log(self):
        self.add()
        d = ScannerDevice.objects.get(name='Bench 1')
        self.client.post(f'/tags/devices/{d.pk}/key/')
        d.refresh_from_db()
        logs = LogEntry.objects.filter(object_id=str(d.pk))
        self.assertEqual(logs.filter(action_flag=ADDITION).count(), 1)
        self.assertTrue(logs.filter(action_flag=CHANGE).exists())
        self.assertFalse(any(d.api_key in f'{e.change_message}{e.object_repr}' for e in logs))

    def test_regenerate_asks_first_then_replaces_the_key(self):
        d = ScannerDevice.objects.create(name='D')
        old = d.api_key
        self.assertContains(self.client.get(f'/tags/devices/{d.pk}/key/'), 'stops working at once')
        d.refresh_from_db()
        self.assertEqual(d.api_key, old)  # a GET changes nothing
        r = self.client.post(f'/tags/devices/{d.pk}/key/')
        d.refresh_from_db()
        self.assertNotEqual(d.api_key, old)
        self.assertContains(r, d.api_key)
        self.assertEqual(self.scan(old).status_code, 401)
        self.assertEqual(self.scan(d.api_key).status_code, 404)  # known device, unassigned tag

    def test_duplicate_name_rejected_and_edit_changes_name_and_location(self):
        ScannerDevice.objects.create(name='Bench 1')
        self.assertContains(self.add(name=' bench 1 '), 'already exists')
        self.assertEqual(ScannerDevice.objects.count(), 1)
        d = ScannerDevice.objects.create(name='Old')
        key = d.api_key
        self.client.post(f'/tags/devices/{d.pk}/', {'name': 'New', 'location': 'Door'})
        d.refresh_from_db()
        self.assertEqual((d.name, d.location, d.api_key), ('New', 'Door', key))

    def test_list_shows_last_seen_and_silent_badge_by_the_rule(self):
        now = timezone.now()
        ScannerDevice.objects.create(name='Fresh', last_seen=now - timedelta(hours=2))
        ScannerDevice.objects.create(name='Stale', last_seen=now - silent_after() - timedelta(hours=1))
        ScannerDevice.objects.create(name='Never')
        ScannerDevice.objects.create(name='Off', active=False)
        r = self.client.get('/tags/devices/')
        self.assertEqual({d.name: d.silent for d in r.context['devices']}, {'Fresh': False, 'Stale': True, 'Never': True, 'Off': False})
        self.assertContains(r, 'badge-overdue', count=2)

    def test_toggle_active_blocks_the_key(self):
        d = ScannerDevice.objects.create(name='D')
        self.client.post('/tags/devices/', {'device': d.pk, 'action': 'toggle'})
        d.refresh_from_db()
        self.assertFalse(d.active)
        self.assertEqual(self.scan(d.api_key).status_code, 401)

    def test_delete_only_without_events_and_never_seen(self):
        fresh = ScannerDevice.objects.create(name='Fresh')
        seen = ScannerDevice.objects.create(name='Seen', last_seen=timezone.now())
        evented = ScannerDevice.objects.create(name='Evented')
        Event.objects.create(device=evented, tag_uid='X', event_type=Event.Type.ARRIVED)
        self.assertEqual(self.client.get('/tags/devices/').content.decode().count('value="delete"'), 1)
        for d in (seen, evented):
            self.assertContains(self.client.post('/tags/devices/', {'device': d.pk, 'action': 'delete'}, follow=True), 'cannot be deleted')
            self.assertTrue(ScannerDevice.objects.filter(pk=d.pk).exists())
        self.client.post('/tags/devices/', {'device': fresh.pk, 'action': 'delete'})
        self.assertFalse(ScannerDevice.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(LogEntry.objects.filter(object_id=str(fresh.pk), action_flag=DELETION).exists())

    def test_tag_pages_link_to_the_three_tabs(self):
        for url in ('/tags/', '/tags/history/', '/tags/devices/'):
            r = self.client.get(url)
            for href in ('/tags/', '/tags/history/', '/tags/devices/'):
                self.assertContains(r, f'href="{href}"')
