from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import JSONResponse
from services.userauth import get_current_user, get_current_user_id, get_organisation_id
from db.connection import translations_collection, objects_collection
from storage.imagestore import retrieve_image
from utils.common import make_thumbnail_from_base64, image_to_base64
from services.db_crud import map_object_collection, map_translation_collection
from services.update_embeddings import get_text_embedding
from bson import ObjectId
import traceback
import os
from typing import Optional

router = APIRouter(prefix="/repository", tags=["repository"])


@router.get("/get_repository")
async def get_repository(
    language: str = Query(..., description="Language for translations (mandatory)"),
    search_text: Optional[str] = Query(None, description="Search text to filter by object name or embedding text"),
    use_vector_search: bool = Query(True, description="Use vector search (True) or simple fuzzy search (False)"),
    last_txn_id: Optional[str] = Query(None, description="Last transaction ID for pagination"),
    limit: int = Query(10, description="Number of objects to retrieve", le=50),
    skip: int = Query(0, description="Number of objects to skip (for search pagination)"),
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieve a list of objects and translations for the logged-in user's organization or user.
    
    Logic:
    1. Check if user belongs to an org
    2. If yes: filter translations by org_id + language + object_name (if search_text)
    3. If no: filter translations by no org_id + created_by user + language
    4. Apply pagination with last_txn_id
    5. Retrieve objects, images, and generate thumbnails
    """
    try:
        user_id = get_current_user_id()
        org_id = get_organisation_id()
        
        print(f"\n🔍 Repository request - User: {user_id}, Org: {org_id}, Language: {language}")
        
        # Build base filter for translations
        base_filter = {
            "requested_language": language.title()
        }
        
        # Apply org/user filter
        if org_id:
            base_filter["org_id"] = org_id
        else:
            # No org - filter by user and no org_id
            base_filter["$and"] = [
                {"created_by": user_id},
                {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]}
            ]
        
        # Apply pagination
        if last_txn_id:
            try:
                base_filter["_id"] = {"$lt": ObjectId(last_txn_id)}
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid last_txn_id: {str(e)}")
        
        # Search and Filtering Logic
        if not search_text:
            # If search_text is empty or None, fetch all records (no filter on embedding_text)
            print("No search text provided, fetching all records.")
            translations_cursor = translations_collection.find(base_filter).sort("_id", -1).limit(limit)
            if skip > 0:
                translations_cursor = translations_cursor.skip(skip)
            
            translations_list = await translations_cursor.to_list(length=limit)
            
            count_filter = {k: v for k, v in base_filter.items() if k != "_id"}
            total_count = await translations_collection.count_documents(count_filter)
        else:
            # Perform Fuzzy Search First
            print(f"Applying Fuzzy Search for: {search_text}")
            fuzzy_filter = base_filter.copy()
            fuzzy_filter["embedding_text"] = {"$regex": search_text, "$options": "i"}
            
            translations_cursor = translations_collection.find(fuzzy_filter).sort("_id", -1)
            if skip > 0:
                translations_cursor = translations_cursor.skip(skip)
            
            translations_list = await translations_cursor.limit(limit).to_list(length=limit)
            
            # Count fuzzy search matches
            count_filter = {k: v for k, v in fuzzy_filter.items() if k != "_id"}
            total_count = await translations_collection.count_documents(count_filter)
            
            # FALLBACK: If nothing found in fuzzy search and use_vector_search is True, perform vector search
            if total_count == 0 and use_vector_search:
                print(f"No results in fuzzy search, falling back to Vector Search for: {search_text}")
                query_vector = get_text_embedding(search_text)
                if query_vector:
                    # For pagination in vector search, we fetch (skip + limit) candidates
                    vector_limit = skip + limit
                    pipeline = [
                        {
                            "$vectorSearch": {
                                "index": "translations_vector_index",
                                "path": "embedding_vector",
                                "queryVector": query_vector,
                                "numCandidates": 100,
                                "limit": vector_limit,
                                "filter": {k: v for k, v in base_filter.items() if k != "_id"} # Use base filter without pagination _id
                            }
                        },
                        {
                            "$set": {
                                "score": {"$meta": "vectorSearchScore"}
                            }
                        },
                        {
                            "$match": {
                                "score": {"$gt": float(os.getenv("SIMILARITY_THRESHOLD", 0.75)) if language.lower() == "english" else float(os.getenv("SIMILARITY_THRESHOLD_NON_EN", 0.70))}
                            }
                        }
                    ]
                    
                    if skip > 0:
                        pipeline.append({"$skip": skip})
                    
                    pipeline.append({"$limit": limit})
                    
                    translations_cursor = translations_collection.aggregate(pipeline)
                    translations_list = await translations_cursor.to_list(length=limit)
                    
                    # For total count in vector search, it's hard to get exact count within Top-K efficiently
                    # but we can return the length of this page or 0 if empty
                    total_count = len(translations_list) if skip == 0 else total_count # Simple heuristic or just keep 0 if skip > 0
                    # Actually, better to just use some count or 0/1 indicator for pagination UI
                else:
                    print(f"⚠️ Failed to generate embedding for search: {search_text}")
            else:
                print(f"Fuzzy search found {total_count} results.")
        
        print(f"Found {len(translations_list)} translations total")
        
        
        # Prepare results
        results = []
        
        for translation in translations_list:
            try:
                # Get corresponding object
                obj = await objects_collection.find_one({"_id": translation["object_id"]})
                
                if not obj:
                    print(f"⚠️ Object not found for translation {translation['_id']}")
                    continue
                
                # Retrieve image from storage
                image_store = obj.get("image_store", {})
                if not image_store:
                    print(f"⚠️ No image_store for object {obj['_id']}")
                    continue
                
                # Retrieve image as base64
                image_base64 = await retrieve_image(image_store)
                
                # Generate thumbnail
                thumbnail_base64 = make_thumbnail_from_base64(image_base64, size=(128, 128))
                
                # Build response object with only required fields
                result = {
                    "translation_id": str(translation["_id"]),
                    "object_id": str(obj["_id"]),
                    "image_hash": obj.get("image_hash", ""),
                    "thumbnail": thumbnail_base64,
                    "image_status": obj.get("image_status", ""),
                    "translation_status": translation.get("translation_status", ""),
                    "object_name": translation.get("object_name", "")
                }
                
                results.append(result)
                
            except Exception as e:
                print(f"❌ Error processing translation {translation.get('_id')}: {e}")
                traceback.print_exc()
                continue
        
        print(f"✅ Returning {len(results)} repository items out of {total_count} total")
        
        return JSONResponse(content={
            "status": "success",
            "total": total_count,
            "count": len(results),
            "items": results
        })
        
    except Exception as e:
        print(f"❌ Repository error: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
