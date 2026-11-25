from urllib.parse import parse_qsl, urlencode, urlsplit

from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

BACKSLASH = chr(92)
BACK_PAGES = ('home', 'boxes', 'completed', 'shipping', 'priorities', 'order_list', 'tag_history', 'tag_manage')


def _label(name, query):
    if name == 'boxes':
        from tracking.views import boxes_title
        return boxes_title(dict(parse_qsl(query)))
    return {'home': _('Home'), 'completed': _('Completed'), 'shipping': _('Shipping'), 'priorities': _('Priorities'),
            'order_list': _('Orders'), 'tag_history': _('Tag history'), 'tag_manage': _('Tags')}[name]


def back_target(value):
    """(url, page name) for a safe 'came from' marker: a relative path that is one of BACK_PAGES; else None."""
    if not value or not value.startswith('/') or value.startswith('//') or BACKSLASH in value or not url_has_allowed_host_and_scheme(value, None):
        return None
    parts = urlsplit(value)
    try:
        name = resolve(parts.path).url_name
    except Resolver404:
        return None
    return (value, _label(name, parts.query)) if name in BACK_PAGES else None


def order_url(job_number, marker=''):
    """The order page URL, carrying a (valid) back marker."""
    url = reverse('order_detail', args=[job_number])
    return f'{url}?{urlencode({"from": marker})}' if marker else url


def safe_marker(value):
    return back_target(value) and value or ''


def order_redirect(job_number, marker=''):
    return redirect(order_url(job_number, safe_marker(marker)))
