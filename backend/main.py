from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from utils import identify_and_translate

from db_crud import retrieve_object_id, save_to_mongo, compute_hash
from fastapi import Query
import json

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://snap-and-tell.streamlit.app/", "*"],  # Allow frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/identify-object")
async def identify_object_route(
    image: UploadFile = File(...),
    language: str = Form(...),
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    image_bytes = await image.read()

    try:
        
        result = await identify_and_translate(compute_hash(image_bytes), image_bytes, language ) 

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return JSONResponse(content=result)

#API to get object ID by unchanged English name
@app.get("/getobjectid/byname")
async def get_object_id_by_name(object_name_en: str = Query(..., description="Unchanged English object name")):
    object_id = await retrieve_object_id(object_name_en)
    return {"object_id": object_id}


@app.post("/update-object")
async def update_object(
    image: UploadFile,
    common_attributes: str = Form(...),
    language_attributes: str = Form(...)
):
    try:
        # Parse JSON strings into Python objects
        common_data = json.loads(common_attributes)
        language_data = json.loads(language_attributes)

        # Validate language attributes
        if not isinstance(language_data, list) or len(language_data) == 0:
            raise HTTPException(status_code=400, detail="language_attributes must be a non-empty list")

        # Convert image to bytes
        image_bytes = await image.read()

        response = await save_to_mongo(image.filename, image_bytes, common_data, language_data)

        return JSONResponse(content=response)

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in attributes")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Run the app with: uvicorn main:app --reload