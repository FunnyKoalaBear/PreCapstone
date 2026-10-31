from django.contrib.admin.models import LogEntry
from django.db.models import Model


def log(actor, objs, flag, message=''):
    """One admin LogEntry per object; objs is a model instance or a queryset."""
    if isinstance(objs, Model):
        objs = type(objs).objects.filter(pk=objs.pk)
    LogEntry.objects.log_actions(actor.pk, objs, flag, message)
