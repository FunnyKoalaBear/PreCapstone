# Prints a pdf file to the printer using the lp command on Linux systems.
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
import time
import os

#local commands to use
#lpstat -p -d //checks printer status
#lp -d Brother_HL_L2340DW_series lined_paper.pdf //prints test.pdf to the printer

#fix slow to print issue by using the following command to set the printer to direct mode
#sudo lpadmin -p Brother_HL_L2460DW_direct -E -v ipp://BRN94DDF8C476E2.local/ipp/print -m everywhere
#lpstat -v


load_dotenv()
PRINTER_NAME = os.getenv("PRINTER_NAME")
FILE_NAME = "lined_paper.pdf"
BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = BASE_DIR / "tmp"


def print_pdf(filename, printer=PRINTER_NAME):

    #building path from root
    path = (TMP_DIR / filename).resolve()

    #check if file exists
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
        return false


    #no need to check if printer is busy to queue print manually, but check for status report
    cmd1 = ["lpstat", "-p", printer]  # only this printer's line, so other queues can't trip the checks
    result = subprocess.run(cmd1, capture_output=True, text=True)

    if result.returncode != 0 or f"printer {printer} " not in result.stdout:
        raise RuntimeError(f"Printer not found: {result.stderr.strip()}")
    
    elif "disabled" in result.stdout:
        raise RuntimeError("Printer is paused (disabled)")
    
    elif "now printing" in result.stdout:
        print("Printer status: ", "Busy, print will wait in queue")

    else:
        print("Printer status: ", "Idle")


    #building and executing print command
    cmd2 = ["lp", "-d", printer, path]
    result = subprocess.run(cmd2, capture_output=True, text=True)

    if result.returncode != 0 or "request id is" not in result.stdout:
        raise RuntimeError(f"Print failed: {result.stderr.strip()}")
    
    else:
        job_id = result.stdout.split()[3]
        print("Printer status: ", "Print Queued", job_id)



    #check if print complete, report status once again
    time.sleep(8)
    result = subprocess.run(cmd1, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Status check failed: {result.stderr.strip()}")
    
    elif f"now printing {job_id}." in result.stdout:  # period so -1 does not match -12
        print("Printer status: ", "Printing", job_id)

    else:
        print("Printer status: ", "Printed", job_id)



if __name__ == "__main__":
    # Usage: python print_pdf.py sample_pdfs/test.pdf
    print_pdf("lined_paper.pdf")
