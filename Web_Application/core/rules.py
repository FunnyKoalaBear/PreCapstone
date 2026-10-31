from datetime import timedelta


def silent_after():
    """How long a scanner may stay unseen before it counts as silent."""
    return timedelta(hours=24)
