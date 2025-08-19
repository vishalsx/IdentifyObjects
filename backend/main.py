from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from utils import identify_and_translate

import motor.motor_asyncio  # Async MongoDB client
import asyncio
import base64
import datetime
import os   
from dotenv import load_dotenv
import os


app = FastAPI()

# MongoDB setup (replace URI & DB name as needed)

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
if not MONGODB_URI:
     # Log error but don't block the main response
    print(f"MongoDB URI error: MONGO_URI environment variable not set")

#os.environ["MONGO_URI"] = MONGO_URI


# Initialize MongoDB client
client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client["alphatubplay"]
collection = db["PublicPictures"]
counters_collection = db["counters"]



# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://snap-and-tell.streamlit.app/", "*"],  # Allow frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_next_sequence(name: str) -> int:
    counter = await counters_collection.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return counter["seq"]


# Async function to save identification details into MongoDB
async def save_to_mongo(image_name: str, language: str, image_bytes: bytes, result: dict):
    try:
        # Convert image to Base64
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Get running sequence number
        seq_number = await get_next_sequence("PublicPictures")

        document = {
            "sequence_number": seq_number,  # unique running number
            "image_name": image_name,
            "requested_language": language,
            "result": result,
            "image_base64": image_base64,
            "timestamp": datetime.datetime.utcnow()
        }
        await collection.insert_one(document)
    except Exception as e:
        # Log error but don't block the main response
        print(f"MongoDB insert error: {e}")
        

@app.post("/identify-object/")
async def identify_object_route(
    image: UploadFile = File(...),
    language: str = Form(...)
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    image_bytes = await image.read()

    try:
        
        result = identify_and_translate(image, image_bytes, language)
        if "error" in result:
            raise Exception(result["error"])
        
        # Call Mongo insert asynchronously (non-blocking)
        asyncio.create_task(save_to_mongo(image.filename, language, image_bytes, result))
    

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return JSONResponse(content=result)