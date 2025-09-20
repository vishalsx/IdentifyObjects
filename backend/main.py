# from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends
# from fastapi.responses import JSONResponse
# from fastapi.middleware.cors import CORSMiddleware
# from utils import identify_and_translate
# import uvicorn
# from db_crud import retrieve_object_id, save_to_db, compute_hash, update_status_only, get_objects_translations_collection, mark_translation_doc_unlocked, get_recent_translations
# from fastapi import Query
# import json
# from login import router as login_router
# from typing import List, Optional

# from auth_utils import get_current_user
# from worklist import get_workitem_for_user
# from bson import ObjectId
# import datetime
# import io
# from fileinfo import process_file_info

# app = FastAPI(title="Identify Objects API")



# # Add CORS middleware
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["https://snap-and-tell.streamlit.app/", "*"],  # Allow frontend origin
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# app.include_router(login_router, prefix="/auth", tags=["auth"])


# @app.post("/identify-object")
# async def identify_object_route(
#     image: UploadFile = File(...),
#     language: str = Form(...),
#     current_user: dict = Depends(get_current_user) 
# ):
#     if not image.content_type.startswith("image/"):
#         raise HTTPException(status_code=400, detail="File must be an image.")

#     image_bytes = await image.read()

#     try:
        
#         result = await identify_and_translate(compute_hash(image_bytes), image_bytes, language ) 
#         if "error" in result and result["error"]:
#             raise HTTPException(status_code=400, detail=result["error"])  # send back the error message
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

#     return JSONResponse(content=result)

# #API to get object ID by unchanged English name
# @app.get("/getobjectid/byname")
# async def get_object_id_by_name(object_name_en: str = Query(..., description="Unchanged English object name")):
#     object_id = await retrieve_object_id(object_name_en)
#     return {"object_id": object_id}



# def clean_mongo_document(doc: dict) -> dict:
#     """
#     Convert MongoDB document (with ObjectId, datetime, etc.) into JSON-serializable dict.
#     """
#     clean_doc = {}
#     for k, v in doc.items():
#         if isinstance(v, ObjectId):
#             clean_doc[k] = str(v)
#         elif isinstance(v, datetime.datetime):
#             clean_doc[k] = v.isoformat()
#         elif isinstance(v, list):
#             clean_doc[k] = [clean_mongo_document(i) if isinstance(i, dict) else i for i in v]
#         elif isinstance(v, dict):
#             clean_doc[k] = clean_mongo_document(v)
#         else:
#             clean_doc[k] = v
#     return clean_doc


# @app.post("/update-object")
# async def update_object(
#     image: Optional[UploadFile] = File(None),
#     common_attributes: str = Form(...),
#     language_attributes: str = Form(...),
#     permission_action: str = Form(...),
#     current_user: dict = Depends(get_current_user)  
# ):
#     try:
#         common_data = json.loads(common_attributes)
#         language_data = json.loads(language_attributes)
#         response: list = []

#         print("\nPermission action: ", permission_action)

#         if not isinstance(language_data, list) or not language_data:
#             raise HTTPException(status_code=400, detail="language_attributes must be a non-empty list")

#         if permission_action not in ["ApproveText", "RejectText"]:
#             # Handle image case
#             image_bytes = b""
#             image_filename = None
#             print("\n👉 image filename:", image.filename if image else "No image uploaded")
#             if image:
#                 image_bytes = await image.read()
#                 image_filename = image.filename

#             for lang_item in language_data:
#                 resp = await save_to_db(
#                     image_filename,
#                     image_bytes,
#                     common_data,
#                     lang_item,
#                     permission_action
#                 )
#                 if resp:
#                     if isinstance(resp, list):
#                         response.extend(resp)
#                         print("\nResponse sent back from Main:",resp)
#                     else:
#                         response.append(resp)
#                         print("\nResponse from back end:", resp)

#         else:  # Verify / Approve / Reject flow
#             print("Language Data received:", language_data)
#             for lang_item in language_data:
#                 resp = await update_status_only(common_data, lang_item, permission_action)
#                 if resp:
#                     if isinstance(resp, list):
#                         response.extend(resp)
#                     else:
#                         response.append(resp)

#         # ✅ Clean before returning
#         safe_response = [clean_mongo_document(r) if isinstance(r, dict) else r for r in response]
#         print ("\nSafe response just before return:", safe_response)
#         return JSONResponse(content=safe_response)

#     except json.JSONDecodeError:
#         raise HTTPException(status_code=400, detail="Invalid JSON in attributes")
#     except Exception as e:
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=str(e))



# import traceback

# @app.get("/worklist-queue")
# async def dynamic_worklist_queue(
#     languages: Optional[str] = Query(None, description="Comma separated languages e.g. en,hi"),
#     current_user: dict = Depends(get_current_user)
# ):
    
#     print("👉 current_user passed to worklist:", current_user)

#     try:
#         language_list = languages.split(",") if languages else []
#         response = await get_workitem_for_user(current_user, language_list)
#         # print("✅ /worklist-queue response:\n", response)
#         return JSONResponse(content=response)
#     except Exception as e:
#         print("❌ ERROR in /worklist-queue:", e)
#         traceback.print_exc()   # full stack trace in server logs
#         raise HTTPException(status_code=500, detail=str(e))


# from bson import ObjectId

# def convert_objectid(document: dict) -> dict:
#     """Recursively convert ObjectId fields to strings in a document"""
#     if not document:
#         return document
#     for key, value in document.items():
#         if isinstance(value, ObjectId):
#             document[key] = str(value)
#         elif isinstance(value, dict):
#             document[key] = convert_objectid(value)
#         elif isinstance(value, list):
#             document[key] = [str(v) if isinstance(v, ObjectId) else v for v in value]
#     return document

# @app.get("/object/{translation_id}")
# async def get_object(
#     translation_id: str,
#     current_user: dict = Depends(get_current_user)
# ):
    
#     print ("\ntranslation Id received at backend", translation_id)
#     response = await get_objects_translations_collection(translation_id)
#     # print("Response received,being sent to frontend:", response)
#     return convert_objectid(response)

# from pydantic import BaseModel
# class translationUnLockRequest(BaseModel):
#     translation_id: str

# @app.put("/object/skip-to-unlock")
# async def unlock_translation(
#     request: translationUnLockRequest,
#     current_user: dict = Depends(get_current_user)):
    
#     try:
#         print("Current User:", current_user)
#         response = await mark_translation_doc_unlocked (request.translation_id, current_user.get("user_id"))
    
#         return convert_objectid(response) 
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error unlocking translation: {str(e)}")


# @app.get("/recent-translations/{username}")
# async def get_recent_translations_endpoint(username: str) :
#     """
#     API endpoint: Return top 3 active translations for a user, joined with their objects.
#     """
#     try:
#         print(f"\nInside Recent tranlations: {username}")
#         results = await get_recent_translations(username)
#         print("\nRESULTS:", results)
#         return results
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error: {str(e)}")



# # --- API endpoint ---
# @app.post("/get_fileinfo")
# async def process_input(
#     file: Optional[UploadFile] = File(None),
#     base64_str: Optional[str] = Form(None),
#     filename: Optional[str] = Form(None),
#     object_id: Optional[str] = Form(None),
# ):
#     try:
#         result = process_file_info(file, base64_str, filename, object_id)
#         return JSONResponse(content=result)
#     except HTTPException as e:
#         raise e
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))



# @app.get("/")
# def read_root():
#     return {"message": "Hello from Idnetify Object app"}

# if __name__ == "__main__":
#     uvicorn.run(
#         "main:app",   # points to this file and the FastAPI instance
#         host="0.0.0.0",
#         port=8000,
#         reload=True   # optional, for auto-reload in dev mode
#     )


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Routers
from routers import (
    identifyObjects,
    updateObjects,
    getWorklist,
    getTranslations,
    skipTranslation,
    extractFileInfo,
    thumbNail,
)

from routers.login import router as login_router
# from db import connect_to_mongo, close_mongo_connection

# --- Import database connection ---
from db.connection import db, client


app = FastAPI(title="Identify Objects API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# # Startup / Shutdown events
# @app.on_event("startup")
# async def startup_event():
#     await connect_to_mongo()

# @app.on_event("shutdown")
# async def shutdown_event():
#     await close_mongo_connection()

# Routers
# app.include_router(login_router, prefix="/auth", tags=["auth"])
app.include_router(login_router, prefix="/auth", tags=["Authentication"])  # ✅ matches login.py
app.include_router(identifyObjects.router)
app.include_router(updateObjects.router)
app.include_router(getWorklist.router)
app.include_router(getTranslations.router)
app.include_router(skipTranslation.router)
app.include_router(extractFileInfo.router)
app.include_router(thumbNail.router)


@app.get("/")
def read_root():
    return {"message": "Hello from Identify Object app"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
