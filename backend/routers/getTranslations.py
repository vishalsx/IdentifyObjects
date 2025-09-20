from fastapi import APIRouter, Depends
from userauth import get_current_user
from db.db_crud import get_objects_translations_collection
from bson import ObjectId

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
    translation_id: str,
    current_user: dict = Depends(get_current_user)
):
    response = await get_objects_translations_collection(translation_id)
    return convert_objectid(response)
