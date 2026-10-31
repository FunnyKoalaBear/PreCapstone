import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.testing import TestCase, make_user
from core.tests import _contrast, _mix
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import ScannerDevice, Tag, TagAssignment

CSS = settings.BASE_DIR / 'static' / 'css'
STATES = ('not-started', 'waiting', 'in-progress', 'at-vendor', 'complete', 'shipped', 'paused', 'cancelled', 'overdue', 'partial')


def css(name):
    return (CSS / name).read_text(encoding='utf-8')


def block(mode):
    text = css('theme.css')
    start = {'light': ':root, :root[data-theme="light"] {', 'dark': ':root[data-theme="dark"] {'}[mode]
    i = text.index(start)
    return text[i:text.index('\n}', i)]


class StyleGuideTests(TestCase):
    def test_only_admin_gets_the_page_and_it_is_in_the_developer_group(self):
        self.client.force_login(make_user('ad', 'Admin'))
        r = self.client.get('/styleguide/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for part in ('Primary', 'Secondary', 'Danger', 'Ghost', 'disabled', 'class="pill pill-complete"', 'class="field-error"',
                     'data-toast="error"', 'data-confirm=', 'text-2xl', 'class="num"'):
            self.assertIn(part, html)
        self.assertEqual([i['label'] for g in r.context['nav'] if str(g['label']) == 'Developer' for i in g['items']][-1], 'Style guide')
        for role in ('Planner', 'Operator'):
            self.client.force_login(make_user(role.lower(), role))
            self.assertEqual(self.client.get('/styleguide/').status_code, 403, role)
            self.assertNotContains(self.client.get('/'), 'href="/styleguide/"')
        self.client.logout()
        self.assertEqual(self.client.get('/styleguide/').status_code, 302)

    def test_posting_the_sample_confirmation_only_shows_a_toast(self):
        self.client.force_login(make_user('ad', 'Admin'))
        r = self.client.post('/styleguide/', follow=True)
        self.assertContains(r, 'Nothing was changed.')


class SmokeTests(TestCase):
    """Every main page opens for Planner and Admin with demo-like data."""

    def setUp(self):
        self.order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date=timezone.localdate() + timedelta(days=4))
        self.item = OrderItem.objects.create(order=self.order, part_name='P', quantity=2)
        RouteStep.objects.create(item=self.item, sequence=1, process=Process.objects.create(name='Cut', kind=Process.Kind.MACHINING))
        TagAssignment.objects.create(tag=Tag.objects.create(uid='SM1'), item=self.item, start_at=timezone.now() - timedelta(days=1))
        self.device = ScannerDevice.objects.create(name='Dev')

    def urls(self):
        job = self.order.job_number
        return ['/', '/boxes/', '/orders/', '/orders/?tab=new', '/orders/?tab=import', '/orders/?tab=codes', '/orders/?tab=processes', f'/orders/{job}/',
                f'/orders/{job}/edit/', '/priorities/', '/analysis/', '/analysis/?tab=plan', '/shipping/', '/dashboard/', '/customers/',
                f'/customers/{self.order.customer_id}/', f'/customers/{self.order.customer_id}/edit/', '/tags/', '/tags/history/', '/tags/devices/',
                '/tags/devices/add/', f'/tags/devices/{self.device.pk}/', '/users/', '/users/add/', '/settings/?tab=workers', '/settings/?tab=machines',
                '/settings/?tab=causes', '/settings/?tab=rules', '/account/password/']

    def test_main_pages_open_for_planner_and_admin_in_both_languages(self):
        for role in ('Planner', 'Admin'):
            self.client.force_login(make_user(role.lower(), role))
            for lang in ('en', 'ja'):
                self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
                for url in self.urls():
                    self.assertEqual(self.client.get(url).status_code, 200, (role, lang, url))
        self.client.force_login(make_user('ad2', 'Admin'))
        self.assertEqual(self.client.get('/simulate/').status_code, 200)


class FormComponentTests(TestCase):
    def setUp(self):
        self.client.force_login(make_user('pl', 'Planner'))

    def test_an_invalid_post_renders_field_error_with_aria_invalid_and_ids(self):
        r = self.client.post('/customers/', {'name': ''})
        html = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertIn('class="field field-invalid"', html)
        self.assertIn('class="field-error" id="id_name_error"', html)
        self.assertRegex(html, r'<input[^>]*name="name"[^>]*aria-invalid="true"[^>]*aria-describedby="[^"]*id_name_error')
        self.assertIn('<span class="required" aria-hidden="true"> *</span><span class="sr-only"> (required)</span>', html)

    def test_user_form_error_and_help_text_use_the_field_component(self):
        make_user('taken', 'Operator')
        r = self.client.post('/users/add/', {'username': 'taken', 'name': '', 'role': 'Operator'})
        self.assertContains(r, 'class="field-error" id="id_username_error"')
        self.assertContains(r, 'aria-invalid="true"')

    def test_main_forms_use_form_actions_with_a_cancel_link(self):
        order = Order.objects.create(customer=Customer.objects.create(name='C2'), due_date=timezone.localdate())
        OrderItem.objects.create(order=order, part_name='P', quantity=1)
        for url in (f'/orders/{order.job_number}/edit/', f'/customers/{order.customer_id}/edit/', '/users/add/', '/tags/devices/add/'):
            html = self.client.get(url).content.decode()
            self.assertIn('class="form-actions', html, url)
            self.assertRegex(html, r'class="button-secondary" href="[^"]+">Cancel</a>', url)
        self.assertIn('form-actions form-actions-sticky', self.client.get(f'/orders/{order.job_number}/edit/').content.decode())


class ComponentCssTests(TestCase):
    def test_tokens_and_button_variants_exist(self):
        theme, base = css('theme.css'), css('base.css')
        for token, value in (('space-1', '0.25rem'), ('space-2', '0.5rem'), ('space-3', '0.75rem'), ('space-4', '1rem'), ('space-5', '1.5rem'),
                             ('space-6', '2rem'), ('control-h', '2.25rem'), ('control-h-sm', '1.75rem'), ('radius', '6px')):
            self.assertRegex(theme, rf'--{token}:\s*{re.escape(value)};')
        for cls in ('.button', '.button-secondary', '.button-danger', '.button-ghost', '.button-sm'):
            self.assertRegex(base, re.escape(cls) + r'[ ,{:]')
        self.assertIn('display: inline-flex', base)
        self.assertIn('text-decoration: none', base)
        self.assertIn('cursor: not-allowed', base)
        self.assertIn('h1 .button-secondary, h2 .button-secondary, h1 .button, h2 .button {', base)  # the compact heading buttons stay
        self.assertIn('min-height: var(--control-h);', base)
        self.assertRegex(base, r'input:not\(\[type="checkbox"\]\)[^{]*select, textarea \{\s*min-height: var\(--control-h\)')

    def test_table_base_style_hover_and_sticky_header(self):
        base = css('base.css')
        self.assertIn('.table tbody tr:hover { background: var(--color-hover); }', base)
        self.assertRegex(base, r'\.scroll-panel thead th \{[^}]*background: var\(--color-surface-2\)')
        self.assertRegex(base, r'\.table th \{[^}]*background: var\(--color-surface-2\)')
        self.assertIn('padding: var(--space-2) var(--space-3)', base)

    def test_one_pill_component_with_a_modifier_and_an_icon_per_state(self):
        base = css('base.css')
        self.assertIn('.pill, .badge {', base)
        for state in STATES:
            self.assertRegex(base, rf'\.pill-{state}, \.badge-{state.replace("-", "_")} \{{ color: var\(--status-{state}\)')
            self.assertRegex(base, rf'\.pill-{state}::before')
        self.assertNotRegex(base, r'#[0-9a-fA-F]{3,8}\b')

    def test_pill_text_keeps_4_5_contrast_on_its_tint_in_both_themes(self):
        for mode in ('light', 'dark'):
            b = block(mode)
            v = dict(re.findall(r'--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})', b))
            for name in [f'status-{s}' for s in STATES] + ['risk-overdue', 'risk-critical', 'risk-watch', 'risk-on-track', 'stagnant-color', 'color-danger']:
                if name not in v:
                    continue
                tint = _mix(v[name], v['color-surface'], 5)  # the pill background is 5% of the text colour
                self.assertGreaterEqual(_contrast(v[name], tint), 4.5, f'{mode}: {name}')

    def test_field_component_has_the_required_parts(self):
        html = (settings.BASE_DIR / 'templates' / 'includes' / 'field.html').read_text(encoding='utf-8')
        for part in ('<label', 'helptext', 'field-error', 'sr-only', 'required'):
            self.assertIn(part, html)
