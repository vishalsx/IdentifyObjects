from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import JSONResponse
from services.userauth import get_current_user
from services.worklist import get_workitem_for_user
import traceback

router = APIRouter(prefix="/worklist", tags=["worklist"])


@router.get("/queue")
async def dynamic_worklist_queue(
    languages: str = Query(None, description="Comma separated languages e.g. en,hi"),
    current_user: dict = Depends(get_current_user)
):
    try:
        language_list = languages.split(",") if languages else []
        response = await get_workitem_for_user(current_user, language_list)
        return JSONResponse(content=response)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
