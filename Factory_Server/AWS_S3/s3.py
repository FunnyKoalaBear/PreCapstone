import argparse
import os
import sys
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

BUCKET = os.getenv("S3_BUCKET")
REGION = os.getenv("AWS_REGION", "ap-northeast-1")

s3 = boto3.client('s3', region_name=REGION)


#uploading pdf 
def upload_file(file_name, bucket=BUCKET):

    object_name = os.path.basename(file_name)

    try: 
        key = "mithun_testing/" + object_name
        response = s3.upload_file(file_name, bucket, key)
        logging.debug(response)

    except ClientError as e:
        logging.error(e)
        return False

    return True



#downloading pdf 
def download_file(key, out_path=None):

    if not out_path:
        out_path = os.path.join("static_testing", os.path.basename(key))
    
    s3.download_file(BUCKET, key, out_path)




#list all pdfs
def list_files(prefix=""):
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return [(o["Key"], o["Size"]) for o in resp.get("Contents", [])]


#main
def main():
    upload_file("AWS_Install.pdf")
    download_file("mithun_testing/AWS_Install.pdf")
    
    list = list_files()
    for i in list:
        print(i[0])





if __name__ == "__main__":
    main()
