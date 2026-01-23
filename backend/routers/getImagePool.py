from fastapi import APIRouter, Depends, HTTPException, Query
from services.userauth import get_current_user
from services.imagepool import get_images_from_pool
from typing import Optional

router = APIRouter(
    prefix="/pool",
    tags=["imagepool"]
)
#authenticated endpoint
@router.post("/recommendations")
async def get_images_from_pool_endpoint(
    search_query: Optional[str] = Query(None, description="Optional search text for fuzzy/synonym search"),
    limit : int = Query(27, description="Number of images to return"),
    skip: int = Query(0, description="Number of images to skip (mainly for search)"),
    last_object_id: Optional[str] = Query(None, description="Last object ID from previous page (for cursor-based pagination)"),
    use_vector_search: bool = Query(False, description="Use vector search (True) or simple fuzzy search (False)"),
    language: Optional[str] = Query(None, description="Optional language code for localization"),
    current_user: dict = Depends(get_current_user)
):
    try:
        print(f"\nAuthenticated API: Search query: {search_query}, language : {language}, limit: {limit}, skip: {skip}, last_id: {last_object_id}")
        results = await get_images_from_pool(
            limit=limit, 
            search_query=search_query, 
            language=language, 
            skip=skip, 
            last_object_id=last_object_id,
            use_vector_search=use_vector_search
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

#copy of the above but for public access (no auth)
@router.post("/recommendations/public")
async def get_images_from_pool_public_endpoint(
    search_query: Optional[str] = Query(None, description="Optional search text for fuzzy/synonym search"),
    limit : int = Query(27, description="Number of images to return"),
    skip: int = Query(0, description="Number of images to skip (mainly for search)"),
    last_object_id: Optional[str] = Query(None, description="Last object ID from previous page (for cursor-based pagination)"),
    use_vector_search: bool = Query(False, description="Use vector search (True) or simple fuzzy search (False)"),
    language: Optional[str] = Query(None, description="Optional language code for localization")
):
    try:
        print(f"\nPublic API: Search query: {search_query}, language : {language}, limit: {limit}, skip: {skip}, last_id: {last_object_id}")
        results = await get_images_from_pool(
            limit=limit, 
            search_query=search_query, 
            language=language, 
            skip=skip, 
            last_object_id=last_object_id,
            use_vector_search=use_vector_search
        )
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")