import re

from django.conf import settings

from core.testing import TestCase, make_user
from django.utils import timezone
from orders.models import Customer, Order, OrderItem, Process, RouteStep
from tracking.models import Tag, TagAssignment

STATIC = settings.BASE_DIR / 'static' / 'css'
TEMPLATES = settings.BASE_DIR / 'templates'


class TypographyTests(TestCase):
    def css(self, name):
        return (STATIC / name).read_text(encoding='utf-8')

    def test_scale_tokens_exist_with_the_agreed_values(self):
        theme = self.css('theme.css')
        for token, value in (('text-sm', '0.8125rem'), ('text-base', '1rem'), ('text-table', '0.9375rem'), ('text-lg', '1.05rem'),
                             ('text-xl', '1.2rem'), ('text-2xl', '1.5rem'), ('leading-tight', '1.3'), ('leading-base', '1.5'), ('leading-ja', '1.7')):
            self.assertRegex(theme, rf'--{token}:\s*{re.escape(value)};')

    def test_no_font_size_below_13px_and_none_outside_the_tokens(self):
        for path in list(STATIC.glob('*.css')) + list(TEMPLATES.rglob('*.html')):
            text = path.read_text(encoding='utf-8')
            for size in re.findall(r'font-size:\s*([^;}"]+)', text):
                size = size.strip()
                if path.name == 'theme.css':
                    continue  # the tokens themselves
                self.assertTrue(size.startswith('var(--text-') or size == 'inherit', f'{path.name}: font-size {size}')
        sizes = [float(v) for v in re.findall(r'--text-[a-z0-9]+:\s*([\d.]+)rem', self.css('theme.css'))]
        self.assertGreaterEqual(min(sizes), 0.8125)

    def test_weights_are_400_500_600_and_only_the_brand_uses_700(self):
        for name in ('base.css', 'lang.css'):
            text = self.css(name)
            self.assertNotRegex(text, r'font-weight:\s*(?:bold|700|800|900)', name)
        self.assertEqual(re.findall(r'(\.[\w-]+)[^{}]*\{[^}]*var\(--weight-bold\)', self.css('base.css')), ['.brand'])

    def test_headings_tables_and_helper_text_use_the_tokens(self):
        base = self.css('base.css')
        self.assertIn('h1 { font-size: var(--text-2xl); }', base)
        self.assertIn('h2 { font-size: var(--text-xl); }', base)
        self.assertIn('h3 { font-size: var(--text-lg); }', base)
        self.assertIn('.table th, .table td {', base)
        self.assertRegex(base, r'\.table th, \.table td \{[^}]*font-size: var\(--text-table\); line-height: 1\.4;')
        self.assertIn('label, .helptext { font-size: var(--text-sm); color: var(--color-muted); }', base)
        self.assertIn('td.num, th.num { text-align: right; }', base)
        self.assertIn('font-variant-numeric: tabular-nums', base)

    def test_japanese_rules_and_no_web_font_in_the_css(self):
        lang = self.css('lang.css')
        self.assertIn(':lang(ja) { line-break: strict; }', lang)
        self.assertIn('var(--leading-ja)', lang)
        self.assertRegex(lang, r':lang\(ja\) :is\(h1, h2, h3, \.nav-label[^)]*\) \{ font-feature-settings: "palt"; \}')
        for name in ('base.css', 'theme.css', 'lang.css'):
            self.assertNotIn('@font-face', self.css(name))
            self.assertNotIn('NotoSansJP', self.css(name))  # the font file is only for the PDFs
        self.assertIn('font-family: system-ui', self.css('base.css'))

    def test_rendered_pages_use_num_and_tnum_cells(self):
        self.client.force_login(make_user('pl', 'Planner'))
        order = Order.objects.create(customer=Customer.objects.create(name='C'), due_date='2030-01-01')
        item = OrderItem.objects.create(order=order, part_name='P', quantity=3)
        RouteStep.objects.create(item=item, sequence=1, process=Process.objects.create(name='Cut', kind=Process.Kind.MACHINING))
        TagAssignment.objects.create(tag=Tag.objects.create(uid='TY1'), item=item, start_at=timezone.now())
        for url in ('/orders/', '/analysis/', '/analysis/?tab=plan', '/shipping/', '/priorities/', '/customers/', '/settings/?tab=workers', f'/orders/{order.job_number}/'):
            html = self.client.get(url).content.decode()
            self.assertTrue('class="num"' in html or 'class="tnum"' in html or 'class="num ' in html, url)
        html = self.client.get('/orders/').content.decode()
        self.assertIn('<td class="num" data-label="Items">', html)
        self.assertIn('<td class="tnum" data-label="Job number">', html)
        self.assertIn('<th class="num">Items</th>', html)
