from fastapi import APIRouter, Depends
from services.userauth import get_current_user
from services.db_crud import get_objects_translations_collection
from bson import ObjectId
from typing import Optional
from db.connection import objects_collection, translations_collection

router = APIRouter(prefix="/translations", tags=["translations"])


def convert_objectid(document: dict) -> dict:
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


@router.get("/{translation_id}")
async def get_object(
    translation_id: Optional[str] = None,
    image_hash: Optional[str] = None,
    language: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):  
    print(f"🔵 get_object called with translation_id: {translation_id}, image_hash: {image_hash}, language: {language}")
    if(image_hash and language):
        # If additional filters are provided, handle accordingly (not implemented here)
        # Get the object first basedo n image_hash
        # org_id filter is implicit due to overloading fr objects and translations collection
        print(f"🔵 Fetching translation for image_hash: {image_hash}, language: {language}")

        obj_doc = await objects_collection.find_one({"image_hash": image_hash, "image_status": "Approved"}, {"_id": 1})  # Adjust projection as needed
        if obj_doc:
            #get the corresponding object name from translation document
            
            trans_obj = await translations_collection.find_one({"object_id": obj_doc['_id'], "requested_language": language, "translation_status": "Approved"}, {"object_name": 1})
            if trans_obj: #return the object name
                object_name = trans_obj.get("object_name")
                print(f"Object Name found in translation for {language}: {object_name}")
                return {"object_name": trans_obj.get("object_name")}
            else:
                return {"detail": "Translation not found for the specified image_hash and language."}
    elif translation_id:
        response = await get_objects_translations_collection(translation_id)
        return convert_objectid(response)
    else:
        return {"detail": "Please provide either translation_id or both image_hash and language."}  
    
    return {"detail": "Translation not found."}

