from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from db.connection import db
from services.userauth import get_current_user

router = APIRouter(prefix="/import_content", tags=["Import Content"])

# Avoiding org_id filtering as this API has to work on non org_id objects only
objects_collection = db["objects"]
translations_collection = db["translations"]


@router.get("")
async def import_content(
    image_hash: str,
    language: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieve unique object document and its translation based on image_hash and language.
    Only returns documents with no org_id associated.
    """
    
    # 1. Find object in objects_collection with no org_id
    # We explicitly add org_id: {"$exists": False} to bypass any automatic org filtering that might include a user's org
    object_query = {
        "image_hash": image_hash,
        "image_status": "Approved",
        "$or": [{"org_id": {"$exists": False}}, {"org_id": None}]
    }
    
    obj_doc = await objects_collection.find_one(object_query)
    
    if not obj_doc:
        raise HTTPException(
            status_code=404, 
            detail="Object not found with the provided hash and no organization association."
        )

    # 2. Find translation for the object_id, language, Approved status, and no org_id
    translation_query = {
        "object_id": obj_doc["_id"],
        "requested_language": language.title() if language else "",
        "translation_status": "Approved",
        "$or": [{"org_id": {"$exists": False}}, {"org_id": None}]
    }
    
    translation_doc = await translations_collection.find_one(translation_query)
    
    if not translation_doc:
        raise HTTPException(
            status_code=404, 
            detail=f"Translation data missing for language '{language}' or it is not approved."
        )

    # 3. Retrieve and return specific fields
    return {
        "requested_language": translation_doc.get("requested_language"),
        "object_name": translation_doc.get("object_name"),
        "object_description": translation_doc.get("object_description"),
        "object_hint": translation_doc.get("object_hint"),
        "object_short_hint": translation_doc.get("object_short_hint"),
        "quiz_qa": translation_doc.get("quiz_qa", [])
    }
