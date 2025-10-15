import base64
from PIL import Image, ImageOps, ImageDraw, ImageFont
import io
from fastapi import UploadFile, HTTPException, BackgroundTasks
import hashlib
import base64
from typing import Union, Any
from services.userauth import get_current_user_id

from datetime import datetime, timezone
import io
import numpy as np

from services.userauth import get_current_user_id
# --- Storage clients ---
from storage.storage_config import STORAGE_PROVIDER, BUCKET_NAME, CDN_BASE_URL, s3_client, gcs_client
from botocore.exceptions import ClientError

# for thumbnail
import base64
import io
from PIL import Image

from db.connection import (
    counters_collection,
    permission_rules_collection,
)


def compute_perceptual_hash(img: Image.Image) -> str:
    """
    Compute a perceptual hash for the image.
    Returns a 16-character hex string.
    """
    # Convert to grayscale and resize to 8x8
    img_small = img.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
    
    # Convert to numpy array and flatten
    pixels = np.array(img_small).flatten()
    
    # Compute average pixel value
    avg = pixels.mean()
    
    # Create binary string based on whether each pixel is above or below average
    binary_str = ''.join(['1' if pixel > avg else '0' for pixel in pixels])
    
    # Convert binary to hexadecimal
    return format(int(binary_str, 2), '016x')

async def normalize_image(image: Union[UploadFile, bytes, str, Image.Image]) -> bytes:
    """
    Normalize any input (UploadFile, bytes, base64 string, PIL Image)
    into deterministic PNG bytes (RGB/PNG, metadata stripped).
    Enhanced for browser consistency.
    """
    # --- Convert input into PIL.Image ---
    # if hasattr(image, 'read') and hasattr(image, 'file'):  # Check for UploadFile-like object
    #     image_bytes = await image.read()
    #     image.file.seek(0)  # reset file pointer so it can be reused later
    #     img = Image.open(io.BytesIO(image_bytes))
    if hasattr(image, "file"):  # UploadFile
        image.file.seek(0)  # rewind every time before reading
        image_bytes = image.file.read()
        if not image_bytes:
            raise ValueError("UploadFile is empty or could not be read")
        img = Image.open(io.BytesIO(image_bytes))
    elif isinstance(image, bytes):
        img = Image.open(io.BytesIO(image))
    elif isinstance(image, str):
        b64_data = image.split(",")[1] if "," in image else image
        img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
    elif isinstance(image, Image.Image):
        img = image
    else:
        raise ValueError(f"Unsupported input type: {type(image)}")

    # --- Enhanced normalization for browser consistency ---
    # Remove EXIF data and apply any rotation
    img = ImageOps.exif_transpose(img)

    # --- Normalize mode with transparency handling ---
    if img.mode in ("RGBA", "LA") or ("transparency" in img.info):
        # Convert transparent images to RGB with white background for consistency
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")

    # --- Quality normalization to remove browser compression differences ---
    temp_buffer = io.BytesIO()
    img.save(temp_buffer, format="JPEG", quality=95, optimize=False)
    temp_buffer.seek(0)
    img = Image.open(temp_buffer)
    img = img.convert("RGB")

    # --- Save normalized PNG in-memory (strips metadata) ---
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

async def compute_hash(image: Union[UploadFile, bytes, str, Image.Image]):
    """
    Compute SHA-256 hash and perceptual hash of an image.
    Returns dict with both exact_hash and perceptual_hash.
    """
    # Get PIL image for perceptual hash (before normalization)
    if hasattr(image, 'read') and hasattr(image, 'file'):  # Check for UploadFile-like object
        image.file.seek(0)  # rewind pointer
        # image_bytes = await image.read()
        image_bytes = image.file.read()
        # image.file.seek(0)
        # img = Image.open(io.BytesIO(image_bytes))
        if not image_bytes:
            raise ValueError("UploadFile is empty or has no data")
        try:
            img = Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            raise ValueError(f"Failed to identify image from UploadFile: {str(e)}")
    elif isinstance(image, bytes):
        img = Image.open(io.BytesIO(image))
    elif isinstance(image, str):
        b64_data = image.split(",")[1] if "," in image else image
        img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
    elif isinstance(image, Image.Image):
        img = image
    else:
        raise ValueError(f"Unsupported input type: {type(image)}")

    # Compute perceptual hash from original image
    perceptual_hash = compute_perceptual_hash(img)
    
    # Compute exact hash from normalized image
    normalized_bytes = await normalize_image(image)
    exact_hash = hashlib.sha256(normalized_bytes).hexdigest()
    
    return perceptual_hash
    

async def image_to_base64(image: Union[UploadFile, bytes, str, Image.Image]) -> str:
    """
    Convert any image into original base64 (preserves original quality).
    """
    # Return original image as base64 without normalization
    if hasattr(image, 'read') and hasattr(image, 'file'):  # Check for UploadFile-like object
        image_bytes = await image.read()
        image.file.seek(0)
        return base64.b64encode(image_bytes).decode("utf-8")
    elif isinstance(image, bytes):
        return base64.b64encode(image).decode("utf-8")
    elif isinstance(image, str):
        # If already base64, return as-is (strip prefix if present)
        return image.split(",")[1] if "," in image else image
    elif isinstance(image, Image.Image):
        # Convert PIL image to bytes then base64
        buffer = io.BytesIO()
        img_format = image.format if image.format else "PNG"
        image.save(buffer, format=img_format)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    else:
        raise ValueError(f"Unsupported input type: {type(image)}")


######## Audit Function #######
###############################

async def insert_into_audit(
    coll,  # Mongo collection (translations or objects)
    query: dict,  # filter used in update_one
    userid: str,
    permission_action: str,
    new_values: dict
) -> dict:
    
    # Build an audit entry showing old vs new values for updated fields.
    
    # Fetch current document
    existing_doc = await coll.find_one(query, projection=new_values.keys())

    changes = {}
    for field, new_val in new_values.items():
        old_val = existing_doc.get(field) if existing_doc else None
        if old_val != new_val:
            changes[field] = {"old": old_val, "new": new_val}

    audit_entry = {
        "user_id": userid,
        "action": permission_action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": changes or {"note": "No actual field changes"}
    }
    return audit_entry


async def get_next_sequence(name: str) -> int:
    counter = await counters_collection.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return counter["seq"]



# Fetch all permission rules
async def get_permission_rules_dict() -> dict:
    permission_rules_cursor = permission_rules_collection.find({})
    all_rules = await permission_rules_cursor.to_list(length=None)
    return {
        rule["_id"]: {k: v for k, v in rule.items() if k != "_id"}
        for rule in all_rules
    }

# Retrieve Metdata next state
async def get_permission_state_metadata(current_metadata_state: str, action: str) -> str:
    permission_rules = await get_permission_rules_dict()
    rule = permission_rules.get(action)
    if not rule:
        raise HTTPException(status_code=400, detail=f"Invalid action {action}")

    state_transitions = rule.get("stateTransitions", {})
    print("state Transition Metadata: ", state_transitions)
    
    # normalising the current_metadata_state

    normalized_current_metadata_state = None if current_metadata_state in (None, "", "null") else current_metadata_state

    # metadata transition
    metadata_next = None
    for t in state_transitions.get("metadata", []):
        if t["from"] == normalized_current_metadata_state:
            metadata_next = t["to"]
            break
    print ("Metadata next State : ", metadata_next)
    # if metadata_next is None:
    #     return "Invalid"
        # raise HTTPException(status_code=400, detail=f"Invalid Metadata state transition for action {action} from state {normalized_current_metadata_state}")
    return metadata_next

# Retrieve translations next state
async def get_permission_state_translations(current_translations_state: str, action: str) -> str:
    permission_rules = await get_permission_rules_dict()
    rule = permission_rules.get(action)
    if not rule:
        raise HTTPException(status_code=400, detail=f"Invalid action {action}")
    
    # normalising the current_metadata_state

    normalized_current_translation_state = None if current_translations_state in (None, "", "null") else current_translations_state

    state_transitions = rule.get("stateTransitions", {})

    print("state Transition Metadata: ", state_transitions)

    # Translations transition
    translations_next = None
    for t in state_transitions.get("language", []):
        if t["from"] == normalized_current_translation_state:
            translations_next = t["to"]
            break
    if translations_next is None:
        raise HTTPException(status_code=400, detail=f"Invalid Translation state transition for action {action} from state {normalized_current_translation_state}")
    return translations_next


def make_thumbnail_from_base64(image_base64: str, size=(128, 128)) -> str:
    """
    Convert base64 image to a fixed-size thumbnail (consistent dimensions)
    with padding to maintain aspect ratio.
    Returns new base64 string.
    """
    try:
        # Decode base64 to image
        image_data = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_data))

        # Convert to RGB to handle alpha channels
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")

        # Create thumbnail preserving aspect ratio
        image.thumbnail(size, Image.Resampling.LANCZOS)

        # Create a new image with consistent size and white background
        thumb = Image.new("RGB", size, (255, 255, 255))
        
        # Center the thumbnail on the canvas
        x_offset = (size[0] - image.width) // 2
        y_offset = (size[1] - image.height) // 2
        thumb.paste(image, (x_offset, y_offset))

        # Convert back to base64
        buffer = io.BytesIO()
        thumb.save(buffer, format="JPEG", quality=80)
        thumbnail_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return thumbnail_b64

    except Exception as e:
        print(f"⚠️ Error creating thumbnail: {e}")
        return image_base64  # fallback to original


