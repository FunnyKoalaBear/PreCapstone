import logging
import os
import random
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PrintJob
from .pdf import item_pdf

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _run(cmd):
    """Run a CUPS command with English output (the checks below read its text)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env={**os.environ, 'LC_ALL': 'C'})
    except FileNotFoundError:
        raise RuntimeError(f'{cmd[0]} not found: CUPS is not installed on this computer') from None


def print_pdf(path, printer=None):
    """Send the PDF at path to the CUPS queue and return the job id (ported from Factory_Server/print_service)."""
    if not Path(path).exists():
        raise FileNotFoundError(f'File not found: {path}')
    if settings.PRINT_DRY_RUN:
        return 'dry-run'
    printer = printer or settings.PRINTER_NAME
    if not printer:
        raise RuntimeError('PRINTER_NAME is not set')
    status = _run(['lpstat', '-p', printer])
    if status.returncode != 0 or f'printer {printer} ' not in status.stdout:
        raise RuntimeError(f'Printer not found: {printer} {status.stderr.strip()}')
    if 'disabled' in status.stdout:
        raise RuntimeError('Printer is paused (disabled)')
    result = _run(['lp', '-d', printer, str(path)])
    if result.returncode != 0 or 'request id is' not in result.stdout:
        raise RuntimeError(f'Print failed: {result.stderr.strip()}')
    return result.stdout.split()[3]  # "request id is <job> (1 file(s))"


def _random_print_drawing():
    drawings = sorted(
        path for path in Path(settings.PRINT_RANDOM_DRAWINGS_DIR).iterdir()
        if path.is_file() and path.suffix.lower() in {'.jpg', '.jpeg', '.png'}
    )
    if not drawings:
        raise FileNotFoundError(
            f'No printable drawings found in {settings.PRINT_RANDOM_DRAWINGS_DIR}'
        )
    return random.choice(drawings)


def print_item(item, name=None):
    """Save the item's sheet with a random extra drawing, print it, and return the job id."""
    folder = Path(settings.PRINT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f'{item.item_code}.pdf')
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(item_pdf(item, extra_image=_random_print_drawing()))
    os.replace(tmp, path)  # the printer never sees a half-written file
    return print_pdf(str(path))


def queue_print(item, source, assignment=None):
    """Add a sheet to the print queue; call it inside the transaction that caused it."""
    return PrintJob.objects.create(item=item, source=source, assignment=assignment)


def run_next_job():
    """Print the oldest PENDING job and return it, or None when the queue is empty."""
    with transaction.atomic():
        job = (PrintJob.objects.select_for_update(skip_locked=True).select_related('item__order')
               .filter(status=PrintJob.Status.PENDING).order_by('pk').first())
        if job is None:
            return None
        job.attempts += 1
        name = f'{job.item.item_code}_{job.pk}.pdf'
        try:
            job.printer_job = print_item(job.item, name)
            job.status, job.pdf_name, job.sent_at, job.error = PrintJob.Status.SENT, name, timezone.now(), ''
        except Exception as e:
            # Printer off, paused or PDF error: retry later, give up after MAX_ATTEMPTS
            logger.exception('Print job %s failed', job.pk)
            job.error = f'{type(e).__name__}: {e}'
            if job.attempts >= MAX_ATTEMPTS:
                job.status = PrintJob.Status.FAILED
        job.save()
        return job
