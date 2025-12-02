from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse
from services.db_crud import save_to_db, update_status_only
from services.userauth import get_current_user
import json, traceback
from bson import ObjectId
import datetime

router = APIRouter(prefix="/update", tags=["update"])


def clean_mongo_document(doc: dict) -> dict:
    clean_doc = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            clean_doc[k] = str(v)
        elif isinstance(v, datetime.datetime):
            clean_doc[k] = v.isoformat()
        elif isinstance(v, list):
            clean_doc[k] = [clean_mongo_document(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            clean_doc[k] = clean_mongo_document(v)
        else:
            clean_doc[k] = v
    return clean_doc


@router.post("/object")
async def update_object(
    image: UploadFile = File(None),
    image_hash: str = Form(None),
    common_attributes: str = Form(...),
    language_attributes: str = Form(...),
    permission_action: str = Form(...),
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user)
):
    try:
        common_data = json.loads(common_attributes)
        language_data = json.loads(language_attributes)
        response: list = []
        print("\n -----------Inside update object -------------------")
        if not isinstance(language_data, list) or not language_data:
            raise HTTPException(status_code=400, detail="language_attributes must be a non-empty list")
        if permission_action not in ["ApproveText", "RejectText"]:
            # image_base64 = await image_to_base64(image) if image else None
            image_filename = image.filename if image else None
            print (f"\n🔵🔵🔵Processing image file:{image_filename}\nImage Hash Received:{image_hash}")

            for lang_item in language_data:
                resp = await save_to_db(image_filename, image, image_hash, common_data, lang_item, permission_action, background_tasks)
                if resp:
                    response.extend(resp if isinstance(resp, list) else [resp])
        else:
            for lang_item in language_data:
                resp = await update_status_only(common_data, lang_item, permission_action)
                if resp:
                    response.extend(resp if isinstance(resp, list) else [resp])

        safe_response = [clean_mongo_document(r) if isinstance(r, dict) else r for r in response]
        return JSONResponse(content=safe_response)

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in attributes")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
