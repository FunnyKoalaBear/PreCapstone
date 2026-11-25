from django import template
from django.utils.html import format_html

from core.navigation import order_url

register = template.Library()


@register.simple_block_tag
def scroll_panel(content, max_height='', first_col=False, label=''):
    """Scrolling box for a long table or list: sticky header row, optional sticky first column (see .scroll-panel in base.css)."""
    return format_html(
        '<div class="scroll-panel{}"{}{} tabindex="0">{}</div>',
        ' scroll-panel-first' if first_col else '',
        format_html(' style="--panel-max: {}"', max_height) if max_height else '',
        format_html(' role="region" aria-label="{}"', label) if label else '',
        content,
    )


@register.simple_tag(takes_context=True)
def order_link(context, job_number):
    """The order page URL with a ?from= marker so its back link returns to the current page."""
    return order_url(job_number, context['request'].get_full_path())
