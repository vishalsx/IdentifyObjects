from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from utils import identify_and_translate

from db_crud import retrieve_object_id, save_to_db, compute_hash, update_status_only, get_objects_translations_collection, mark_translation_doc_unlocked
from fastapi import Query
import json
from login import router as login_router
from typing import List, Optional

from auth_utils import get_current_user
from worklist import get_workitem_for_user
from bson import ObjectId
import datetime

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://snap-and-tell.streamlit.app/", "*"],  # Allow frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(login_router, prefix="/auth", tags=["auth"])


@app.post("/identify-object")
async def identify_object_route(
    image: UploadFile = File(...),
    language: str = Form(...),
    current_user: dict = Depends(get_current_user) 
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    image_bytes = await image.read()

    try:
        
        result = await identify_and_translate(compute_hash(image_bytes), image_bytes, language ) 

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return JSONResponse(content=result)

#API to get object ID by unchanged English name
@app.get("/getobjectid/byname")
async def get_object_id_by_name(object_name_en: str = Query(..., description="Unchanged English object name")):
    object_id = await retrieve_object_id(object_name_en)
    return {"object_id": object_id}



def clean_mongo_document(doc: dict) -> dict:
    """
    Convert MongoDB document (with ObjectId, datetime, etc.) into JSON-serializable dict.
    """
    clean_doc = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            clean_doc[k] = str(v)
        elif isinstance(v, datetime.datetime):
            clean_doc[k] = v.isoformat()
        elif isinstance(v, list):
            clean_doc[k] = [clean_mongo_document(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            clean_doc[k] = clean_mongo_document(v)
        else:
            clean_doc[k] = v
    return clean_doc


@app.post("/update-object")
async def update_object(
    image: Optional[UploadFile] = File(None),
    common_attributes: str = Form(...),
    language_attributes: str = Form(...),
    permission_action: str = Form(...),
    current_user: dict = Depends(get_current_user)  
):
    try:
        common_data = json.loads(common_attributes)
        language_data = json.loads(language_attributes)
        response: list = []

        print("\nPrint permission action: ", permission_action)

        if not isinstance(language_data, list) or not language_data:
            raise HTTPException(status_code=400, detail="language_attributes must be a non-empty list")

        if permission_action not in ["ApproveText", "RejectText"]:
            # Handle image case
            image_bytes = b""
            image_filename = None
            print("\n👉 image filename:", image.filename if image else "No image uploaded")
            if image:
                image_bytes = await image.read()
                image_filename = image.filename

            for lang_item in language_data:
                resp = await save_to_db(
                    image_filename,
                    image_bytes,
                    common_data,
                    lang_item,
                    permission_action
                )
                if resp:
                    if isinstance(resp, list):
                        response.extend(resp)
                        print("\nResponse sent back from Main:",resp)
                    else:
                        response.append(resp)
                        print("\nResponse from back end:", resp)

        else:  # Verify / Approve / Reject flow
            print("Language Data received:", language_data)
            for lang_item in language_data:
                resp = await update_status_only(common_data, lang_item, permission_action)
                if resp:
                    if isinstance(resp, list):
                        response.extend(resp)
                    else:
                        response.append(resp)

        # ✅ Clean before returning
        safe_response = [clean_mongo_document(r) if isinstance(r, dict) else r for r in response]
        print ("\nSafe response just before return:", safe_response)
        return JSONResponse(content=safe_response)

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in attributes")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))



import traceback

@app.get("/worklist-queue")
async def dynamic_worklist_queue(
    languages: Optional[str] = Query(None, description="Comma separated languages e.g. en,hi"),
    current_user: dict = Depends(get_current_user)
):
    
    print("👉 current_user passed to worklist:", current_user)

    try:
        language_list = languages.split(",") if languages else []
        response = await get_workitem_for_user(current_user, language_list)
        # print("✅ /worklist-queue response:\n", response)
        return JSONResponse(content=response)
    except Exception as e:
        print("❌ ERROR in /worklist-queue:", e)
        traceback.print_exc()   # full stack trace in server logs
        raise HTTPException(status_code=500, detail=str(e))


from bson import ObjectId

def convert_objectid(document: dict) -> dict:
    """Recursively convert ObjectId fields to strings in a document"""
    if not document:
        return document
    for key, value in document.items():
        if isinstance(value, ObjectId):
            document[key] = str(value)
        elif isinstance(value, dict):
            document[key] = convert_objectid(value)
        elif isinstance(value, list):
            document[key] = [str(v) if isinstance(v, ObjectId) else v for v in value]
    return document

@app.get("/object/{translation_id}")
async def get_object(
    translation_id: str,
    current_user: dict = Depends(get_current_user)
):
    
    print ("\ntranslation Id received at backend", translation_id)
    response = await get_objects_translations_collection(translation_id)
    print("Response received,being sent to frontend:", response)
    return convert_objectid(response)

from pydantic import BaseModel
class translationUnLockRequest(BaseModel):
    translation_id: str

@app.put("/object/skip-to-unlock")
async def unlock_translation(
    request: translationUnLockRequest,
    current_user: dict = Depends(get_current_user)):
    
    try:
        print("Current User:", current_user)
        response = await mark_translation_doc_unlocked (request.translation_id, current_user.get("user_id"))
    
        return convert_objectid(response) 
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error unlocking translation: {str(e)}")