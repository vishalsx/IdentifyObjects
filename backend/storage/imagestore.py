
from PIL import Image, ImageOps, ImageDraw, ImageFont
import io
from fastapi import UploadFile, HTTPException, BackgroundTasks

import base64
from services.userauth import get_current_user_id, get_organisation_id

from datetime import datetime, timezone
import io

# --- Storage clients ---
from storage.storage_config import STORAGE_PROVIDER, BUCKET_NAME, CDN_BASE_URL, s3_client, gcs_client
from botocore.exceptions import ClientError
from utils.common import image_to_base64, get_next_sequence


def _process_and_upload(buffer: io.BytesIO, object_key: str):
    # Pending task: update status in objects_collection indication the successful save "image_store_status"
    buffer.seek(0)
    if STORAGE_PROVIDER == "aws_s3":
        s3_client.upload_fileobj(buffer, BUCKET_NAME, object_key, ExtraArgs={"ContentType": "image/jpeg"})
    elif STORAGE_PROVIDER == "gcs":
        bucket = gcs_client.bucket(BUCKET_NAME)
        blob = bucket.blob(object_key)
        blob.upload_from_file(buffer, content_type="image/jpeg")
        # Update the image_store_status to true here in the backfround.
    else:
        raise ValueError(f"Unsupported storage provider: {STORAGE_PROVIDER}")
    
 
async def store_image(image_file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    """
    Stores an uploaded image in S3/Cloud Storage after:
    - Converting to JPEG
    - Adding watermark
    - Compressing
    Returns metadata about the stored image.
    """

    user_id = get_current_user_id()
    print("\nUserId found inside Store Image: ", user_id)

    if not user_id:
        raise HTTPException(status_code=401, detail="No logged-in user found in context")

    # Step 0: Get sequence number
    try:
        seqno = await get_next_sequence(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate sequence number: {str(e)}")

    # Step 1: Read and open image
    try:
        image_file.file.seek(0)  # Ensure file pointer is at start
        contents = await image_file.read()
        image = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read or process image file: {str(e)}")

    # Step 2: Watermark
    try:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        watermark_text = "© alphaTUB"
        w, h = image.size

        # Use textbbox instead of deprecated textsize
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        draw.text((w - tw - 10, h - th - 10), watermark_text, fill=(255, 255, 255), font=font)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add watermark: {str(e)}")

    # Step 3: Compress to buffer
    try:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        buffer.seek(0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compress image: {str(e)}")

    # Include org_id in the path after images folder
    org_id = None
    org_id = get_organisation_id()
    # Step 4: Build object key (date-based path)
    
    try:
        now = datetime.now(timezone.utc)
        year, month = now.strftime("%Y"), now.strftime("%m")
        ts_prefix = now.strftime("%Y%m%d%H%M")
        filename = f"{user_id}-{ts_prefix}-{seqno}.jpeg"
        if org_id:
            object_key = f"images/{org_id}/{year}/{month}/{filename}"
        else:
            object_key = f"images/PUBLIC/{year}/{month}/{filename}"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build object key: {str(e)}")

    print(f"\nObject Key: {object_key}, Buffer: {buffer}")

    # Step 5: Schedule background upload
    try:
        background_tasks.add_task(_process_and_upload, buffer, object_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to schedule background upload: {str(e)}")

    # Step 6: Return metadata
    return {
        "storage_provider": STORAGE_PROVIDER,
        "object_key": object_key,
        "url": f"{CDN_BASE_URL}/{object_key}"
    }


async def retrieve_image (image_store: dict) -> str:
    # To be called whenever a real image has to be retrieved from storage. E.g.in Worklists, Thumbnail, Hints game...
    # Returns stored image as a file based on search on image_hash
    # extract the image_store attributes from  object colletion and calls get function to retrieve image from bucket.
    storage_provider = image_store.get("storage_provider")
    object_key = image_store.get("object_key")

    if not storage_provider or not object_key:
        raise HTTPException(status_code=400, detail="Invalid image_store dict")

    try:
        if storage_provider == "aws_s3":
            # Fetch from AWS S3
            response = s3_client.get_object(Bucket=BUCKET_NAME, Key=object_key)
            image_data = response["Body"].read()

        elif storage_provider == "gcs":
            # Fetch from Google Cloud Storage
            client = gcs_client.Client()
            bucket = client.bucket(BUCKET_NAME)
            blob = bucket.blob(object_key)
            image_data = blob.download_as_bytes()

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported storage provider: {storage_provider}")

        # Convert to base64
        image_base64 = await image_to_base64(image_data)
        return image_base64

    except ClientError as e:
        raise HTTPException(
            status_code=404,
            detail=f"Unable to retrieve {object_key} from {storage_provider}: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error retrieving image: {str(e)}"
        )