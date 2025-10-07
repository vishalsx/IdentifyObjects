from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional
from bson import ObjectId
from PIL import Image
import base64
import io
from db.connection import objects_collection # centralized DB connection
from utils.common import compute_hash
from storage.imagestore import retrieve_image

# def compute_hash(data: bytes) -> str:
#     img = Image.open(io.BytesIO(data)).convert("RGB")
#     return hashlib.sha256(img.tobytes()).hexdigest()

#     #return hashlib.sha256(data).hexdigest()


def get_image_info(data: bytes):
    """Return size (bytes), dimensions (w,h), mime type"""
    size = len(data)
    image = Image.open(io.BytesIO(data))
    width, height = image.size
    mime_type = Image.MIME.get(image.format)
    return size, (width, height), mime_type


def decode_base64(base64_str: str) -> bytes:
    """Decode base64 string into bytes (remove prefix if present)."""
    if "," in base64_str:
        base64_str = base64_str.split(",")[1]
    return base64.b64decode(base64_str)


# --- Core logic ---
async def process_file_info(
    file: Optional[UploadFile],
    base64_str: Optional[str],
    filename: Optional[str],
    object_id: Optional[str],
):
    doc = None
    data_bytes = None
    mime_type = None
    size = None
    w = h = None
    new_filename = filename
    

    # --- Case 1: File provided ---
    if file:
        file.file.seek(0)  # rewind every time before reading
        data_bytes = file.file.read()
        image_hash = await compute_hash(file)
        doc = await objects_collection.find_one({"image_hash": image_hash})
        new_filename = doc.get("image_name", file.filename) if doc else file.filename

    # --- Case 2: Base64 provided ---
    elif base64_str:
        if not filename:
            filename= "unknown"
        data_bytes = decode_base64(base64_str)
        image_hash = await compute_hash(data_bytes)
        doc = await objects_collection.find_one({"image_hash": image_hash})
        new_filename = doc.get("image_name", filename) if doc else filename

    # --- Case 3: ObjectId provided ---
    elif object_id:
        try:
            oid = ObjectId(object_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid object_id format")

        doc = await objects_collection.find_one({"_id": oid})
        if not doc:
            raise HTTPException(status_code=404, detail="Object not found")

        new_filename = doc.get("image_name")
        # if "image_base64" in doc:
        #     data_bytes = decode_base64(doc["image_base64"])
        try:
            if "image_store" in doc:
                image_store = doc.get("image_store", {})
                image_base64 = await retrieve_image (image_store)
                data_bytes = decode_base64(image_base64)
        except Exception as e:
            print(f"Failed to retrieve image from storage (supressing the error): {str(e)}")         
    else:
        raise HTTPException(status_code=400, detail="Provide either file, base64, or object_id")

    # --- Compute image details if we have bytes ---
    if data_bytes:
        size, (w, h), mime_type = get_image_info(data_bytes)

    # --- Build response ---
    response = {
        "filename": new_filename,
        "size": f"{size} bytes" if size else None,
        "dimensions": f"{w} × {h}" if w and h else None,
        "mime_type": mime_type,
        # "created_by": doc.get("file_info").get("created_by") if doc else None,
        # "created_at": doc.get("file_info").get("created_at") if doc else None,
        # "updated_by": doc.get("file_info").get("updated_by") if doc else None,
        # "updated_at": doc.get("file_info").get("updated_at") if doc else None,
    }
    # print ("\nFileinfo: ", response)
    return response




