# Prints a local PDF via CUPS (Ubuntu). Windows dev: see TODO at the bottom.
import subprocess
import sys
from pathlib import Path

PRINTER_NAME = ""  # TODO: load from .env (name as shown by `lpstat -p`)


def print_pdf(path, printer=PRINTER_NAME, copies=1, duplex=False):
    # TODO: check the file exists and starts with b"%PDF"
    # TODO: build the `lp` argument list (-d, -n, -o sides=..., -o media=A4)
    # TODO: subprocess.run(..., capture_output=True, text=True, check=True)
    # TODO: parse the job id from "request id is X-123" and return it
    raise NotImplementedError


if __name__ == "__main__":
    # Usage: python print_pdf.py sample_pdfs/test.pdf
    pass
