import os
from pathlib import Path

from django.conf import settings

from .pdf import item_pdf


def print_pdf(path):
    """Placeholder: send the PDF at path to the printer and return the job id (the real one replaces this body)."""
    if not Path(path).exists():
        raise FileNotFoundError(f'File not found: {path}')
    if settings.PRINT_DRY_RUN:
        return 'dry-run'
    raise RuntimeError('Printing is not set up on this computer')


def print_item(item):
    """Save the item's sheet as <item_code>.pdf in PRINT_DIR, print it and return the job id."""
    folder = Path(settings.PRINT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{item.item_code}.pdf'
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(item_pdf(item))
    os.replace(tmp, path)  # the printer never sees a half-written file
    return print_pdf(str(path))
