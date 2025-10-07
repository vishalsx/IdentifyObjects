import boto3
import os

s3_client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION")
)

BUCKET_NAME = os.getenv("S3_BUCKET_NAME")


from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from botocore.exceptions import NoCredentialsError
from s3_client import s3_client, BUCKET_NAME

app = FastAPI()

# @app.post("/upload/")
async def upload_file(file: UploadFile):
    try:
        s3_client.upload_fileobj(file.file, BUCKET_NAME, file.filename)
        return {"message": "File uploaded", "url": f"s3://{BUCKET_NAME}/{file.filename}"}
    except NoCredentialsError:
        raise HTTPException(status_code=403, detail="AWS credentials not found")


# @app.get("/download/{filename}")
def download_file(filename: str):
    try:
        file_obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=filename)
        return StreamingResponse(file_obj["Body"], media_type="application/octet-stream")
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


# @app.delete("/delete/{filename}")
def delete_file(filename: str):
    try:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=filename)
        return {"message": "File deleted"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
