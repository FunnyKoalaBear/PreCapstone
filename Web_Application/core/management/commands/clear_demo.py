from django.core.management.base import BaseCommand
from django.db import transaction

from orders.models import Order, OrderChange, PrintJob
from tracking.models import Event, TagAssignment

from .seed_demo import Command as Seed


class Command(Seed):
    help = 'Remove the demo data (works with DEBUG off). With --everything also remove every order, scan and card assignment.'

    def add_arguments(self, parser):
        parser.add_argument('--everything', action='store_true', help='Also delete non-demo orders, events and assignments.')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be removed, then roll back.')

    def handle(self, *args, everything=False, dry_run=False, **options):
        class Rollback(Exception):
            pass
        try:
            with transaction.atomic():
                removed = self.wipe()
                if everything:
                    Event.objects.filter(corrects__isnull=False).delete()
                    removed['events'] += Event.objects.count()
                    Event.objects.all().delete()  # QuerySet.delete() bypasses the append-only Model.delete
                    TagAssignment.objects.all().delete()
                    PrintJob.objects.all().delete()
                    OrderChange.objects.all().delete()
                    removed['orders'] += Order.objects.count()
                    Order.objects.all().delete()
                self.stdout.write(f'Removed {removed["orders"]} orders, {removed["events"]} events, {removed["processes"]} demo processes.')
                self.stdout.write(f'Left: {Order.objects.count()} orders, {Event.objects.count()} events, {TagAssignment.objects.count()} assignments.')
                if dry_run:
                    raise Rollback
        except Rollback:
            self.stdout.write(self.style.WARNING('Dry run: nothing was changed.'))
