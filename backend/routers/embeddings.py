from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from bson import ObjectId
from services.update_embeddings import update_object_embeddings
from services.userauth import get_current_user
from typing import Optional
import logging

router = APIRouter(prefix="/embeddings", tags=["Embeddings"])
logger = logging.getLogger(__name__)

@router.post("/update")
async def trigger_update_embeddings(
    object_id: str,
    translation_id: Optional[str] = None,
    # background_tasks: BackgroundTasks = None
    # current_user: dict = Depends(get_current_user)
):
    """
    Trigger manual update of embeddings for a specific object and optionally a translation.
    """
    try:
        if not ObjectId.is_valid(object_id):
            raise HTTPException(status_code=400, detail=f"Invalid object_id: {object_id}")
            
        obj_id_obj = ObjectId(object_id)
        trans_id_obj = ObjectId(translation_id) if translation_id and ObjectId.is_valid(translation_id) else None
        
        if translation_id and not ObjectId.is_valid(translation_id):
             raise HTTPException(status_code=400, detail=f"Invalid translation_id: {translation_id}")

        # Add to background tasks to avoid blocking the API response
        # if background_tasks:
        #     background_tasks.add_task(update_object_embeddings, obj_id_obj, trans_id_obj)
        # else:
        #     # Fallback if background_tasks is not provided (though FastAPI usually handles this)
        await update_object_embeddings(obj_id_obj, trans_id_obj)
        
        return {
            "status": "success", 
            "message": "Embedding update triggered",
            "object_id": object_id,
            "translation_id": translation_id
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error triggering embedding update: {e}")
        raise HTTPException(status_code=500, detail=str(e))
