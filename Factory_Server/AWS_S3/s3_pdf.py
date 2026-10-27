"""Upload, list and download PDF files in one S3 bucket.

Usage (run inside AWS_S3 with the venv active):
    python s3_pdf.py upload drawing.pdf
    python s3_pdf.py upload drawing.pdf --key drawings/job-2611-7093.pdf
    python s3_pdf.py list
    python s3_pdf.py download drawings/job-2611-7093.pdf --out copy.pdf
"""
import argparse
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

BUCKET = os.getenv("S3_BUCKET")
REGION = os.getenv("AWS_REGION", "ap-northeast-1")

# credentials come from `aws configure` (~/.aws/credentials), never from this file
s3 = boto3.client("s3", region_name=REGION)


def upload_pdf(local_path, key=None):
    if not local_path.lower().endswith(".pdf"):
        raise ValueError("only .pdf files are allowed")
    
    key = key or "drawings/" + os.path.basename(local_path)
    s3.upload_file(local_path, BUCKET, key, ExtraArgs={"ContentType": "application/pdf"})
    return key


def list_pdfs(prefix=""):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [(o["Key"], o["Size"]) for o in resp.get("Contents", [])]


def download_pdf(key, out_path=None):
    out_path = out_path or os.path.basename(key)
    s3.download_file(BUCKET, key, out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="PDF upload/download for S3")
    sub = parser.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("upload")
    up.add_argument("path")
    up.add_argument("--key")

    ls = sub.add_parser("list")
    ls.add_argument("--prefix", default="")

    down = sub.add_parser("download")
    down.add_argument("key")
    down.add_argument("--out")

    args = parser.parse_args()

    if not BUCKET:
        sys.exit("S3_BUCKET is not set. Copy .env.example to .env and fill it in.")

    try:
        if args.cmd == "upload":
            print("uploaded to", f"s3://{BUCKET}/{upload_pdf(args.path, args.key)}")
        elif args.cmd == "list":
            for key, size in list_pdfs(args.prefix):
                print(f"{size:>10}  {key}")
        else:
            print("saved as", download_pdf(args.key, args.out))
    except (ClientError, BotoCoreError, ValueError, OSError) as err:
        sys.exit(f"failed: {err}")


if __name__ == "__main__":
    main()
