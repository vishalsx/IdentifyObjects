from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.userauth import get_current_user
from services.db_crud import mark_translation_doc_unlocked
from services.recent_translations import get_recent_translations

router = APIRouter(prefix="/translations", tags=["skip-translations"])


class TranslationUnlockRequest(BaseModel):
    translation_id: str


@router.put("/skipToUnlock")
async def unlock_translation(
    request: TranslationUnlockRequest,
    current_user: dict = Depends(get_current_user)
):
    try:
        response = await mark_translation_doc_unlocked(request.translation_id, current_user.get("user_id"))
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error unlocking translation: {str(e)}")


@router.get("/recent/{username}")
async def get_recent_translations_endpoint(username: str):
    try:
        results = await get_recent_translations(username)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
