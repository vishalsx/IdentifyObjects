import os
import boto3
from google.cloud import storage as gcs_storage

# --- Storage provider ---
STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "aws_s3")  # "aws_s3" or "gcs"
BUCKET_NAME = os.getenv("STORAGE_BUCKET", "my-bucket")
CDN_BASE_URL = os.getenv("CDN_DOMAIN", f"https://{BUCKET_NAME}.s3.amazonaws.com")

# Initialize clients
s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
) if STORAGE_PROVIDER == "aws_s3" else None

gcs_client = gcs_storage.Client() if STORAGE_PROVIDER == "gcs" else None