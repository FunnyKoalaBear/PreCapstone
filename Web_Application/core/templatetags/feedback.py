from django import template

register = template.Library()


@register.filter
def undo_token(message):
    """The Undo token a view attached to a message ('' when it has none)."""
    tags = message.extra_tags or ''
    return tags[5:] if tags.startswith('undo:') else ''
