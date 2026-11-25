import time

from django.conf import settings
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
        # the service can see other settings than a terminal: show what this process will use
        self.stdout.write(f'print_worker started: printer {settings.PRINTER_NAME or "(not set)"}, '
                          f'dry run {settings.PRINT_DRY_RUN}, drawings {settings.PRINT_RANDOM_DRAWINGS_DIR}, '
                          f'PDFs {settings.PRINT_DIR}')
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
