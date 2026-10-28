"""Print a PDF on the Brother HL-L2460DW through CUPS (the Linux print system)."""
import argparse
import subprocess
import sys
from pathlib import Path

PRINTER = "Brother_HL_L2460DW"


def build_command(pdf, printer, copies, duplex, pages):
    cmd = ["lp", "-d", printer, "-n", str(copies), "-o", "media=A4"]
    if duplex:
        # Long-edge flip, like a normal book
        cmd += ["-o", "sides=two-sided-long-edge"]
    if pages:
        cmd += ["-P", pages]
    cmd.append(str(pdf))
    return cmd


def main():
    parser = argparse.ArgumentParser(description="Print a PDF on the Brother printer.")
    parser.add_argument("pdf", type=Path, help="path to the PDF file")
    parser.add_argument("-n", "--copies", type=int, default=1)
    parser.add_argument("-d", "--duplex", action="store_true", help="print on both sides")
    parser.add_argument("-p", "--pages", help='page range, e.g. "1-3,5"')
    parser.add_argument("--printer", default=PRINTER)
    parser.add_argument("--dry-run", action="store_true", help="show the command, print nothing")
    args = parser.parse_args()

    if not args.pdf.is_file():
        sys.exit(f"File not found: {args.pdf}")
    # Every real PDF starts with these bytes, whatever its file name
    with args.pdf.open("rb") as f:
        if f.read(5) != b"%PDF-":
            sys.exit(f"Not a PDF: {args.pdf}")
    if args.copies < 1:
        sys.exit("Copies must be at least 1")

    cmd = build_command(args.pdf, args.printer, args.copies, args.duplex, args.pages)
    if args.dry_run:
        print(" ".join(cmd))
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"Printing failed: {result.stderr.strip()}")
    print(result.stdout.strip())


if __name__ == "__main__":
    main()
