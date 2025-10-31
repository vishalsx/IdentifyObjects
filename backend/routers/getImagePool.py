# from fastapi import APIRouter, Depends, Form, HTTPException
# from services.userauth import get_current_user, get_current_user_id
# from services.imagepool import get_images_from_pool

# router = APIRouter(
#     prefix="/pool",
#     tags=["imagepool"]
# )

# @router.post("/recommendations") # the endoint from frontendn is {base_url}/image/pool
# async def get_images_from_pool_endpoint(
#     current_user: dict = Depends(get_current_user)  # ✅ enforce auth
# ):
#     try:
#         results = await get_images_from_pool()
#         return results
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

# # 
from fastapi import APIRouter, Depends, HTTPException, Query
from services.userauth import get_current_user
from services.imagepool import get_images_from_pool
from typing import Optional

router = APIRouter(
    prefix="/pool",
    tags=["imagepool"]
)

@router.post("/recommendations")
async def get_images_from_pool_endpoint(
    search_query: Optional[str] = Query(None, description="Optional search text for fuzzy/synonym search"),
    limit : Optional[int] = Query(9, description="Number of images to return"),
    language: Optional[str] = Query(None, description="Optional language code for localization"),
    current_user: dict = Depends(get_current_user)
):
    try:
        print(f"\nSearch query: {search_query}, language : {language}, limit: {limit}")
        results = await get_images_from_pool(limit=limit, search_query=search_query, language=language)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
