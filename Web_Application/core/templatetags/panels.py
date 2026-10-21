from django import template
from django.utils.html import format_html

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
