from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId  #pip install pymongo or #pip install bson
import motor.motor_asyncio
import hashlib
import datetime
import os   
import base64
from dotenv import load_dotenv


load_dotenv()  # Load environment variables from .env file

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DBNAME  = os.getenv("MONGODB_DBNAME", "PublicObjects")


# MongoDB setup
client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client[MONGODB_DBNAME]
objects_collection = db["objects"]
translations_collection = db["translations"]
counters_collection = db["counters"]

app = FastAPI()
print(f"Connected to MongoDB at {MONGODB_URI}, using database '{MONGODB_DBNAME}'")

async def get_next_sequence(name: str) -> int:
    counter = await counters_collection.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return counter["seq"]


def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Create Object (with Deduplication + First Translation)
async def save_to_mongo(image_name: str, image_bytes: bytes, common_data: any, language_data: any):
    image_hash = compute_hash(image_bytes)
    
    # Check for existing object by image hash only. name can be duplicate
    existing_object = await objects_collection.find_one( {"image_hash": image_hash} )
       
    try:
        obj_id = None
        if existing_object:
            obj_id = existing_object["_id"]
            # Update basic information and metadata if needed. DO not update image hash..
            await objects_collection.update_one(
                {"_id": obj_id},
                {
                    "$set": {
                        "object_name_en": common_data.get("object_name_en", existing_object.get("object_name_en", "")),
                        "metadata.tags": common_data.get("tags", existing_object.get("metadata", {}).get("tags", [])),
                        "metadata.object_category": common_data.get("object_category", existing_object.get("metadata", {}).get("object_category", "")),
                        "metadata.updated_at": datetime.datetime.utcnow().isoformat(),
                        "metadata.updated_by": common_data.get("userid", "anonymous"),
                    }
                }
            )


            #Check for existing translation for each row in language_data
            for lang_row in language_data:
                await translations_collection.update_one(
                {
                    "object_id": obj_id,
                    "requested_language": lang_row.get("language", "Unknown")
                },
                {
                    "$set": {
                    "object_name": lang_row.get("object_name", ""),
                    "object_description": lang_row.get("object_description", ""),
                    "object_hint": lang_row.get("object_hint", "")
                    },
                    "$setOnInsert": {
                    "object_id": obj_id,
                    "requested_language": lang_row.get("language", "Unknown")
                    }
                },
                    upsert=True
                )
        else: #its the first time for an object to be added to MongoDB
            try:
                # Convert image to Base64
                image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                # Create new object document
                object_document = {
                "sequence_number": await get_next_sequence(MONGODB_DBNAME),  # unique running number
                "image_name": image_name,
                "image_hash": image_hash,
                "image_base64": image_base64,
                "object_name_en": common_data.get("object_name_en", ""),
                "image_status": "Active",
                "metadata": {
                    "tags":common_data.get("tags", []),
                    "object_category": common_data.get("object_category", ""),
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "created_by": common_data.get("userid", "anonymous"),
                }
                }
                new_object = await objects_collection.insert_one(object_document)
                obj_id = new_object.inserted_id
                # Create initial translation document   
                try:
                    for lang_row in language_data:
                        await translations_collection.update_one(
                        {
                            "object_id": obj_id,
                            "requested_language": lang_row.get("language", "Unknown")
                        },
                        {
                            "$set": {
                            "object_name": lang_row.get("object_name", ""),
                            "object_description": lang_row.get("object_description", ""),
                            "object_hint": lang_row.get("object_hint", "")
                            },
                            "$setOnInsert": {
                            "object_id": obj_id,
                            "requested_language": lang_row.get("language", "Unknown")
                            }
                        },
                    upsert=True
                        )              
                except Exception as e:
                    print(f"MongoDB insert error: {e}") # translation already exists or other error
                    raise HTTPException(status_code=500, detail="Failed to save translation to database")
            except Exception as e:
                # Log error but don't block the main response
                print(f"MongoDB insert error: {e}")
                raise HTTPException(status_code=500, detail="Failed to save object to database")
    except Exception as e:
        print(f"Processing error: {e}")
        raise HTTPException(status_code=500, detail="Processing error")
    return {"status": "success", "object_id": str(obj_id)}
    
# --- API to Retrieve immutable _id by object_name_en ---
async def retrieve_object_id(object_name_en: str) -> str:
    obj = await objects_collection.find_one({"object_name_en": object_name_en})
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return str(obj["_id"])

#Function to retriev data by image hash
async def get_existing_data_imagehash(imagehash: str, language: str):
    # obj_id = None
    print(f"Searching for image hash: {imagehash} with language: {language}")
    obj = await objects_collection.find_one({"image_hash": imagehash})
    if obj:
        obj_id = obj["_id"]  # ✅ don’t wrap with ObjectId()
        print(f"ObjectId found : {obj_id} ({type(obj_id)})")

        # Fetch translation if language is specified
        if language:
            translation = await translations_collection.find_one({
                "object_id": obj_id,
                "requested_language": language
            })
            if translation:
                return {
                    "existing_object_category": obj.get("metadata", {}).get("object_category", ""),
                    "existing_object_tags": obj.get("metadata", {}).get("tags", []),
                    "existing_object_name_en": obj.get("object_name_en", ""),
                    "existing_object_name": translation.get("object_name", ""),
                    "existing_object_description": translation.get("object_description", ""),
                    "existing_object_hint": translation.get("object_hint", "")
                    }
    
    return {"message": "No existing object found with the provided image hash.",
            "error": "No existing object found"}
    
            

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()