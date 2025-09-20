from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fileinfo import process_file_info

router = APIRouter(prefix="/fileinfo", tags=["fileinfo"])


@router.post("")
async def process_input(
    file: UploadFile = File(None),
    base64_str: str = Form(None),
    filename: str = Form(None),
    object_id: str = Form(None),
):
    try:
        result = await process_file_info(file, base64_str, filename, object_id)
        return JSONResponse(content=result)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
