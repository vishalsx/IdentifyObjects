from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from checkwithAI import identify_and_translate
from db.connection import objects_collection
from db.db_crud import compute_hash, retrieve_object_id
from userauth import get_current_user
from typing import Optional
from common import image_to_base64
router = APIRouter(prefix="/identify", tags=["identify"])

  
@router.post("/object")
async def identify_object(
    image: Optional[UploadFile] = File(None),   # ✅ optional file
    image_hash: Optional[str] = Form(None),     # ✅ optional hash
    language: str = Form(...),
    current_user: dict = Depends(get_current_user)
):

    # --- Case 1: image has provided. hash takes priority ---
    if image_hash:
        doc = await objects_collection.find_one({"image_hash": image_hash})
        if not doc:
            raise HTTPException(status_code=404, detail="No object found for given image_hash")

        image_filename = doc.get("image_name")
        image_base64 = doc.get("image_base64")    
        imagehash = image_hash

    # --- Case 2: Only image provided ---
    elif image:

        if not image.content_type or not image.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image.")
        image_base64 = await image_to_base64(image) 
        imagehash = await compute_hash (image_base64)
        image_filename = image.filename if image else None
            
    else:
        raise HTTPException(status_code=400, detail="Either image file or image_hash must be provided.")

    # --- Call main pipeline ---
    try:
        result = await identify_and_translate(
            image_base64, imagehash, image_filename, language
        )
        if "error" in result and result["error"]:
            return {"Error": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return JSONResponse(content=result)




@router.get("/getobjectid/byname")
async def get_object_id_by_name(object_name_en: str):
    object_id = await retrieve_object_id(object_name_en)
    return {"object_id": object_id}
