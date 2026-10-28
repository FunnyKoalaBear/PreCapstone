# printing: context for Claude

Goal: a script that prints a PDF on the factory printer. Later it fetches the PDF from AWS S3 first.
The developer is learning and wants to write the code themselves. Explain and guide, and don't implement unless asked.

## Environment
- Runs on the factory server (Ubuntu, mini PC). Developed partly on Windows, but real printing and testing happen on Ubuntu only.
- Printer: Brother HL-L2460DW (A4, mono laser, wireless/wired LAN, duplex). Likely no native PDF support, so do not send raw PDF to port 9100.
- Printing goes through CUPS (`lp`, `lpstat`, `cancel`). Install: `sudo apt install cups`. Web UI: `http://localhost:631`.
- Use `subprocess` with an argument list (never a shell string), or `pycups` (needs `libcups2-dev`).

## Files
- `print_pdf.py`: stubbed `print_pdf(path, printer, copies, duplex)`; should validate the PDF, run `lp`, return the job id (from `request id is X-123`), raise on failure. Has a small `__main__` CLI for testing. Knows nothing about S3 or FastAPI.
- `__init__.py`: empty, makes this a package (`from printing.print_pdf import print_pdf`).
- `sample_pdfs/`: local test PDFs.

## Plan
1. Manual `lp -d <printer> test.pdf` works on Ubuntu.
2. Implement `print_pdf` and the CLI.
3. Later: a separate `s3_fetch.py` (boto3, download to a temp file), then call `print_pdf`, then delete the temp file. Possibly a plain `def` FastAPI route in `main.py`.

## Rules
- Read the printer name from `.env` (never read or print `.env` itself), not hardcoded.
- Keep code comments to one short line. Never commit or push unless asked.
- Explain fixes and ask before editing the developer's existing code.
