import html
import re
import shutil
from io import StringIO
from collections import deque
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import override_settings, setup_test_environment, teardown_test_environment

ASSETS = Path(__file__).resolve().parents[2] / 'demo_assets'
LANGS = ('ja', 'en')
BANNER = {
    'ja': '<strong>デモ / サンプルデータ</strong> &mdash; 架空のデータです。入力した内容は保存されません。',
    'en': '<strong>DEMO / sample data</strong> &mdash; invented data. Nothing you enter is saved.',
}
TIERS = ('overdue', 'critical', 'watch', 'on_track')
# Query parameters the browser-side filters read; the others only pick which page file is used
DROP = {'tab', 'current', 'tier', 'n', 'edit'}
LINK = re.compile(r'(?<![\w-])(href|data-href|action|src)="([^"]*)"')
FORBIDDEN = ('csrf', 'localhost', '127.0.0.1', 'testserver', 'password', 'api_key', 'X-API-Key', 'secret')


def canon(url):
    """(page key, URL to fetch) of an app URL that the demo contains, or None."""
    parts = urlsplit(url)
    path, q = parts.path, dict(parse_qsl(parts.query))
    if path == '/':
        return ('index_current', '/?current=1&n=500') if q.get('current') == '1' else ('index', '/?n=500')
    if path == '/orders/':
        tab = q.get('tab')
        if tab in ('new', 'import', 'codes'):
            return f'orders_{tab}', f'/orders/?tab={tab}' + ('&inactive=1' if tab == 'codes' else '')
        return 'orders', '/orders/'
    if m := re.fullmatch(r'/orders/(\d{4}-\d{3})/', path):
        return f'order_{m[1]}', path
    if path == '/analysis/':
        tab = 'plan' if q.get('tab') == 'plan' else 'results'
        return f'analysis_{tab}', f'/analysis/?tab={tab}'
    if path == '/priorities/':
        tier = q.get('tier', '').lower()
        return (f'priorities_{tier}', f'/priorities/?tier={tier.upper()}') if tier in TIERS else ('priorities', '/priorities/')
    if path == '/boxes/':
        if q.get('vendor_late') == '1':
            return 'boxes_vendor_late', '/boxes/?status=at_vendor&vendor_late=1'
        if q.get('stagnant') == '1':
            return 'boxes_stagnant', '/boxes/?stagnant=1'
        if re.fullmatch(r'[a-z_]+', q.get('status', '')):
            return f'boxes_{q["status"]}', f'/boxes/?status={q["status"]}'
        return 'boxes', '/boxes/'
    if m := re.fullmatch(r'/customers/(\d+)/', path):
        return f'customer_{m[1]}', path
    for p, key in (('/shipping/', 'shipping'), ('/customers/', 'customers'), ('/tags/', 'tags'), ('/tags/history/', 'tag_history')):
        if path == p:
            return key, p
    return None


def absolute(href, base):
    """Links like ?tier=X are relative to the page they are on."""
    return urlsplit(base).path + href if href.startswith('?') else href


def md_to_html(text):
    """The few Markdown forms docs/manual_ja.md uses: headings, quotes, lists, tables, paragraphs, **bold** and `code`."""
    inline = lambda s: re.sub(r'`([^`]+)`', r'<code>\1</code>', re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html.escape(s, quote=False)))
    out, block, kind = [], [], None

    def flush():
        nonlocal block, kind
        if not block:
            return
        if kind == 'p':
            out.append('<p>' + '<br>'.join(inline(b) for b in block) + '</p>')
        elif kind in ('ol', 'ul'):
            out.append(f'<{kind}>' + ''.join(f'<li>{inline(b)}</li>' for b in block) + f'</{kind}>')
        elif kind == 'quote':
            out.append('<blockquote>' + inline(' '.join(block)) + '</blockquote>')
        elif kind == 'table':
            rows = [[c.strip() for c in r.strip().strip('|').split('|')] for r in block if not re.fullmatch(r'[|\s:-]+', r)]
            head, body = rows[0], rows[1:]
            out.append('<table><thead><tr>' + ''.join(f'<th>{inline(c)}</th>' for c in head) + '</tr></thead><tbody>'
                       + ''.join('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r) + '</tr>' for r in body) + '</tbody></table>')
        block, kind = [], None

    for line in text.splitlines():
        m = re.match(r'(#{1,3}) (.+)', line)
        item = re.match(r'(\d+\. |- )(.+)', line)
        new = ('h', None) if m else ('quote', line[2:]) if line.startswith('> ') else ('table', line) if line.startswith('|') \
            else (('ol' if item.group(1)[0].isdigit() else 'ul', item.group(2)) if item else ('p', line) if line.strip() else (None, None))
        if new[0] != kind:
            flush()
        if m:
            out.append(f'<h{len(m[1])}>{inline(m[2])}</h{len(m[1])}>')
        elif new[0]:
            kind = new[0]
            block.append(new[1])
    flush()
    return '\n'.join(out)


class Command(BaseCommand):
    help = 'Build the static demo (Japanese and English) from a scratch database that holds only the seed_demo data.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default=str(settings.BASE_DIR / 'demo'), help='Folder to write (old demo files in it are replaced).')

    def handle(self, *args, out, **options):
        out = Path(out)
        setup_test_environment()
        old_name = connection.settings_dict['NAME']
        connection.creation.create_test_db(verbosity=0, autoclobber=True)  # scratch database: no real data can leak
        try:
            with override_settings(DEBUG=True, DEMO_EXPORT=True):
                call_command('seed_demo', stdout=StringIO())
                pages = self.crawl()
        finally:
            connection.creation.destroy_test_db(old_name, verbosity=0)
            teardown_test_environment()
        self.write(out, pages)
        self.verify(out)

    def crawl(self):
        user = User.objects.create_user('demo_export')
        user.groups.add(Group.objects.get(name='Planner'))
        client = Client()
        client.force_login(user)
        pages = {}
        for lang in LANGS:
            client.cookies[settings.LANGUAGE_COOKIE_NAME] = lang
            queue, seen = deque([canon('/'), canon('/orders/'), canon('/orders/?tab=codes')]), set()
            while queue:
                key, url = queue.popleft()
                if key in seen:
                    continue
                seen.add(key)
                response = client.get(url)
                if response.status_code != 200:
                    self.stderr.write(f'{lang} {url}: {response.status_code}')
                    continue
                body = response.content.decode()
                pages[lang, key] = (url, body)
                for _attr, href in LINK.findall(body):
                    href = absolute(html.unescape(href), url)
                    target = canon(href) if href.startswith('/') and not href.startswith('/static/') else None
                    if target and target[0] not in seen:
                        queue.append(target)
            self.stdout.write(f'{lang}: {len(seen)} pages')
        return pages

    def rewrite(self, body, lang, key, known, url='/'):
        def link(m):
            attr, value = m[1], absolute(html.unescape(m[2]), url)
            if value.startswith('/static/'):
                return f'{attr}="../static/{value[8:]}"'
            if not value.startswith('/'):
                return m[0]
            target = canon(value)
            if target and target[0] in known:
                parts = urlsplit(value)
                drop = DROP | ({'status', 'stagnant', 'vendor_late'} if target[0].startswith('boxes') else set())
                query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k not in drop])
                return f'{attr}="{target[0]}.html' + (f'?{query}' if query else '') + (f'#{parts.fragment}' if parts.fragment else '') + '"'
            return f'{attr}="#"' + (' data-demo-link' if attr == 'href' else '')
        body = re.sub(r'<form action="[^"]*setlang[^"]*".*?</form>', lambda _: self.lang_links(lang, key), body, flags=re.S)
        body = re.sub(r'<form action="/logout/".*?</form>|<a class="account" href="/login/">.*?</a>', '', body, flags=re.S)
        body = re.sub(r'<input type="hidden" name="csrfmiddlewaretoken" value="[^"]*">', '', body)
        body = body.replace(' onchange="this.form.submit()"', '')
        body = re.sub(r'(name="inactive"[^>]*?) checked', r'\1', body)
        body = LINK.sub(link, body)
        manual = ('manual.html', '使い方') if lang == 'ja' else ('../ja/manual.html', '使い方')
        body = re.sub(r'(id="site-nav">.*?)(</nav>)', rf'\1<a href="{manual[0]}" lang="ja">{manual[1]}</a>\2', body, count=1, flags=re.S)
        body = re.sub(r'(<body[^>]*>)', rf'\1<div class="demo-banner" role="note">{BANNER[lang]}</div>', body, count=1)
        body = body.replace('</head>', '<link rel="stylesheet" href="../static/css/demo.css"></head>', 1)
        body = body.replace('</body>', '<script src="../static/js/demo.js" defer></script></body>', 1)
        return re.sub(r'</title>', ' (demo)</title>', body, count=1)

    @staticmethod
    def lang_links(lang, key):
        page = lambda other: f'../{other}/{key}.html' if key != 'manual' or other == 'ja' else '../en/index.html'
        return '<span class="lang-switch">' + ''.join(
            f'<a href="{page(o)}"{" class=\"active\" aria-current=\"true\"" if o == lang else ""}>{label}</a>' for o, label in (('ja', '日本語'), ('en', 'English'))) + '</span>'

    def write(self, out, pages):
        for name in ('ja', 'en', 'static', 'css', 'js'):
            shutil.rmtree(out / name, ignore_errors=True)
        for old in out.glob('*.html'):
            old.unlink()
        known = {key for _lang, key in pages}
        for (lang, key), (url, body) in pages.items():
            self.put(out / lang / f'{key}.html', self.rewrite(body, lang, key, known, url))
        shell = pages['ja', 'index'][1]
        manual = re.sub(r'(<main class="site-main">).*?(</main>)', lambda m: m[1] + '<div class="manual">' + md_to_html(
            (settings.BASE_DIR / 'docs' / 'manual_ja.md').read_text(encoding='utf-8')) + '</div>' + m[2], shell, flags=re.S)
        manual = re.sub(r'<title>.*?</title>', '<title>使い方 - 工程進捗管理</title>', manual, flags=re.S)
        self.put(out / 'ja' / 'manual.html', self.rewrite(manual, 'ja', 'manual', known))
        for sub, files in (('css', ('theme.css', 'base.css', 'lang.css')), ('js', ('nav.js', 'matrix.js', 'formset.js', 'codes.js'))):
            for f in files:
                self.put(out / 'static' / sub / f, (settings.BASE_DIR / 'static' / sub / f).read_text(encoding='utf-8'))
        self.put(out / 'static' / 'css' / 'demo.css', (ASSETS / 'demo.css').read_text(encoding='utf-8'))
        self.put(out / 'static' / 'js' / 'demo.js', (ASSETS / 'demo.js').read_text(encoding='utf-8'))
        self.put(out / 'index.html', '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=ja/index.html">'
                 '<title>工程進捗管理 (demo)</title></head><body><a href="ja/index.html">日本語</a> / <a href="en/index.html">English</a></body></html>')

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def verify(self, out):
        """Every relative link and asset must exist, and nothing that looks like a secret or an internal address may be present."""
        broken, leaks, count = [], [], 0
        for page in out.rglob('*.html'):
            text = page.read_text(encoding='utf-8')
            count += 1
            for _attr, href in LINK.findall(text):
                if href and not href.startswith(('#', 'http', 'mailto:')):
                    if not (page.parent / href.split('#')[0].split('?')[0]).exists():
                        broken.append(f'{page.relative_to(out)} -> {href}')
            leaks += [f'{page.relative_to(out)}: {w}' for w in FORBIDDEN if w in text]
        self.stdout.write(f'Wrote {count} pages to {out}')
        if broken or leaks:
            raise CommandError('Broken links: %s; suspicious text: %s' % (broken[:10], leaks[:10]))
        self.stdout.write(self.style.SUCCESS('No broken links, nothing suspicious found.'))
