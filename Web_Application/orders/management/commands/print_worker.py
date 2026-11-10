import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from orders.models import PrintJob
from orders.printing import run_next_job


class Command(BaseCommand):
    help = 'Print queued work sheets one by one. Runs as the print-worker service on the factory server.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Work through the queue once, then exit.')
        parser.add_argument('--interval', type=float, default=2, help='Seconds between checks of an empty queue.')
        parser.add_argument('--retry-wait', type=float, default=15, help='Seconds to wait after a failed job.')

    def handle(self, *args, once=False, interval=2, retry_wait=15, **options):
        self.stdout.write('print_worker started')
        tried = set()
        while True:
            close_old_connections()  # a long-running loop must drop dead DB connections itself
            job = run_next_job()
            if job is None or (once and job.pk in tried):
                if once:
                    return
                time.sleep(interval)
                continue
            tried.add(job.pk)
            self.stdout.write(f'{job} {job.status} {job.printer_job or job.error}')
            if job.status != PrintJob.Status.SENT and not once:
                time.sleep(retry_wait)
