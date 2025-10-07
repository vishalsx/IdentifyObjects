from fastapi import APIRouter, Depends, Form, HTTPException
from services.userauth import get_current_user, get_current_user_id
from services.db_crud import get_recent_translations

router = APIRouter(
    prefix="/thumbnail",
    tags=["thumbnail"]
)

@router.post("")  # ✅ must match frontend fetch (POST /thumbnail)
async def get_recent_translations_endpoint(
    username: str = Form(...),                # ✅ match FormData from frontend
    current_user: dict = Depends(get_current_user)  # ✅ enforce auth
):
    try:
        print(f"\n👉 Thumbnail endpoint hit by user: { get_current_user_id()}, requested username={username}")
        results = await get_recent_translations(username)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
