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
MONGODB_DBNAME  = os.getenv("MONGODB_DBNAME", "alphatubplay")


# MongoDB setup
client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client[MONGODB_DBNAME]
objects_collection = db["objects"]
translations_collection = db["translations"]
counters_collection = db["counters"]
permission_rules_collection = db["permission_rules"]
roles_collection = db["roles"]
users_collection = db["users"]
languages_collection = db["languages"]
app = FastAPI()


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


# Fetch all permission rules
async def get_permission_rules_dict() -> dict:
    permission_rules_cursor = permission_rules_collection.find({})
    all_rules = await permission_rules_cursor.to_list(length=None)
    return {
        rule["_id"]: {k: v for k, v in rule.items() if k != "_id"}
        for rule in all_rules
    }

# Retrieve Metdata next state
async def get_permission_state_metadata(current_metadata_state: str, action: str) -> str:
    permission_rules = await get_permission_rules_dict()
    rule = permission_rules.get(action)
    if not rule:
        raise HTTPException(status_code=400, detail=f"Invalid action {action}")

    state_transitions = rule.get("stateTransitions", {})
    print("state Transition Metadata: ", state_transitions)
    
    # normalising the current_metadata_state

    normalized_current_metadata_state = None if current_metadata_state in (None, "", "null") else current_metadata_state

    # metadata transition
    metadata_next = None
    for t in state_transitions.get("metadata", []):
        if t["from"] == normalized_current_metadata_state:
            metadata_next = t["to"]
            break
    print ("Metadata next State : ", metadata_next)
    # if metadata_next is None:
    #     return "Invalid"
        # raise HTTPException(status_code=400, detail=f"Invalid Metadata state transition for action {action} from state {normalized_current_metadata_state}")
    return metadata_next

# Retrieve translations next state
async def get_permission_state_translations(current_translations_state: str, action: str) -> str:
    permission_rules = await get_permission_rules_dict()
    rule = permission_rules.get(action)
    if not rule:
        raise HTTPException(status_code=400, detail=f"Invalid action {action}")
    
    # normalising the current_metadata_state

    normalized_current_translation_state = None if current_translations_state in (None, "", "null") else current_translations_state

    state_transitions = rule.get("stateTransitions", {})

    print("state Transition Metadata: ", state_transitions)

    # Translations transition
    translations_next = None
    for t in state_transitions.get("language", []):
        if t["from"] == normalized_current_translation_state:
            translations_next = t["to"]
            break
    if translations_next is None:
        raise HTTPException(status_code=400, detail=f"Invalid Translation state transition for action {action} from state {normalized_current_translation_state}")
    return translations_next


# Always check if the number of non-rejects on trnalstion are 0 then the metadata also goes into rejection on its own.
async def reject_object_if_all_translatios_rejected(object_coll:any, translation_coll:any):
    pass
        

import traceback



async def manage_rejection(common_data: dict) -> dict | None:
    """
    Handles object-level rejection if all translations are rejected.
    Returns the updated object document, or None if no update was applied.
    """
    object_coll = await objects_collection.find_one(
        {"_id": ObjectId(common_data.get("object_id"))}
    )
    
    if not object_coll:
        print(f"⚠️ No object found with id {common_data.get('object_id')}")
        return None

    obj_id = object_coll["_id"]

    # Count translations not rejected
    count = await translations_collection.count_documents(
        {"object_id": obj_id, "translation_status": {"$ne": "Rejected"}}
    )
    print(f"Total docs with translation_status != 'Rejected': {count}")

    if count == 0:
        # All translations rejected → reject object too
        new_metadata_state = await get_permission_state_metadata(
            object_coll.get("image_status"),
            "RejectText"
        )
        if new_metadata_state is not None:
            result = await objects_collection.update_one(
                {"_id": obj_id},
                {
                    "$set": {
                        "image_status": "Rejected",
                        "metadata.updated_at": datetime.datetime.utcnow().isoformat(),
                        "metadata.updated_by": common_data.get("userid", "anonymous"),
                    }
                }
            )
            if result.modified_count > 0:
                updated_object = await objects_collection.find_one({"_id": obj_id})
                print(f"✅ Object {obj_id}: {updated_object.get('object_name_en')} marked as Rejected")
                return obj_id
            else:
                print(f"⚠️ No update applied for object {obj_id} (already Rejected?)")
    else:
        print(f"ℹ️ Object {obj_id} not rejected because {count} translations are still active.")

    return None



async def update_status_only(common_data: dict, language_row: dict, permission_action: str) -> dict:
    print("🔹 Common data object_id:", common_data.get("object_id"))
    print("🔹 Language row translation_id:", language_row.get("translation_id"))
    print("🔹 Permission Action:", permission_action)

    new_metadata_state = None
    new_translation_state = None
    translation_coll = None
    object_response = None
    obj_id = None
    try:
        # --- TRANSLATION STATUS UPDATE ---
        translation_id = language_row.get("translation_id")
        if translation_id:
            translation_coll = await translations_collection.find_one(
                {"_id": ObjectId(translation_id)}
            )

            if translation_coll:
                print("✅ Found translation:", translation_coll.get("_id"))
                new_translation_state = await get_permission_state_translations(
                    translation_coll.get("translation_status"), 
                    permission_action
                )
                print("➡️ New Translation State:", new_translation_state)
                print("\nUserid received from common_data:", common_data.get("userid"))

                if translation_coll.get("locked_by") == common_data.get("userid"):
                    await translations_collection.update_one(
                        {"_id": translation_coll["_id"]},
                        {
                            "$set": {
                                "locked_by": None,
                                "translation_status": new_translation_state,
                                "updated_at": datetime.datetime.utcnow().isoformat(),
                                "updated_by": common_data.get("userid", "anonymous"),
                            }
                        }
                    )

        # --- OBJECT STATUS UPDATE / REJECTION HANDLING ---
        if permission_action == 'RejectText':
            obj_id = await manage_rejection(common_data)
            
        else:
            object_coll = await objects_collection.find_one(
                {"_id": ObjectId(common_data.get("object_id"))}
            )
            if object_coll:
                obj_id = object_coll["_id"]
                print("✅ Found object:", obj_id)

                new_metadata_state = await get_permission_state_metadata(
                    object_coll.get("image_status"), 
                    permission_action 
                )

                if new_metadata_state is not None:
                    print("➡️ New Object State:", new_metadata_state)
                    await objects_collection.update_one(
                        {"_id": obj_id},
                        {
                            "$set": {
                                "image_status": new_metadata_state,
                                "metadata.updated_at": datetime.datetime.utcnow().isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous"),
                            }
                        }
                    )

        # ✅ Build a clean response dict
        return {
            "status": "success",
            "translation_id": str(translation_id),
            "object_id": str(obj_id)
        }

    except Exception as e:
        print(f"❌ Processing error in update_status_only: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Processing error in change states!!")




# Create Object (with Deduplication + First Translation)
async def save_to_db(image_name: str,image_bytes: bytes, common_data: any, lang_row: any, permission_action: str):
    obj_id = None
    translation_id = None
    
    #Following code is for rest of the actions

    if not image_bytes:
     #try to ge the record based on transaction id
        obj_id = ObjectId(common_data.get("object_id"))
        print("Looking for Objects presence..",obj_id)
        existing_object = await objects_collection.find_one( {"_id": obj_id} )
    else:                                                    #try to get the record based on image hash
        image_hash = compute_hash(image_bytes)
        print("\nPermission Action from frontend: ", permission_action)
        # Check for existing object by image hash only. name can be duplicate
        existing_object = await objects_collection.find_one( {"image_hash": image_hash} )
       
    try:
        obj_id = None
        new_metadata_state = None
        if existing_object:
            obj_id = existing_object["_id"]
            
            
            new_metadata_state = await get_permission_state_metadata (existing_object.get("image_status"), permission_action )
            if new_metadata_state is not None: # Do not update metadata if new transition is None
                if lang_row.get("language", "Unknown").lower() == "english": #update the entire Metadata if send for English, else just update the status
                    await objects_collection.update_one(
                        {"_id": obj_id},
                        {
                        #"$set": copy_objects_collection("update",common_data, existing_object)
                        "$set": {
                                "object_name_en": common_data.get("object_name_en", existing_object.get("object_name_en", "")),
                                "image_status": new_metadata_state,
                                "metadata.tags": common_data.get("tags", existing_object.get("metadata", {}).get("tags", [])),
                                "metadata.object_category": common_data.get("object_category", existing_object.get("metadata", {}).get("object_category", "")),
                                "metadata.field_of_study": common_data.get("field_of_study", existing_object.get("metadata", {}).get("field_of_study", "")),
                                "metadata.age_appropriate": common_data.get("age_appropriate", existing_object.get("metadata", {}).get("age_appropriate", "")),
                                "metadata.updated_at": datetime.datetime.utcnow().isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous"),
                        }
                    }
                    )
                else: #just update the state transition if not english
                    print("\nUpdating only status of Object as the language is not English.")
                    await objects_collection.update_one(
                        {"_id": obj_id},
                        {
                            #"$set": copy_objects_collection("update",common_data, existing_object)
                            "$set": {
                                
                                "image_status": await get_permission_state_metadata (existing_object.get("image_status"), permission_action ),
                                "metadata.updated_at": datetime.datetime.utcnow().isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous")
                            }
                        }
                    )

            #Check for existing translation for each row in language_data
            # for lang_row in language_data:
            existng_translation = await translations_collection.find_one(
                {
                    "object_id": obj_id,
                    "requested_language": lang_row.get("language", "Unknown")
                }
            )
            existing_translation_status = None
            if existng_translation:
                existing_translation_status = existng_translation.get("translation_status")
                print(f"Translation existing for language {lang_row.get('language', 'Unknown')} with status {existing_translation_status}")
                translation_id = existng_translation["_id"]
            
            await translations_collection.update_one(
            {
                "object_id": obj_id,
                "requested_language": lang_row.get("language", "Unknown")
            },
            {
                "$set": {
                "object_name": lang_row.get("object_name", ""),
                "object_description": lang_row.get("object_description", ""),
                "object_hint": lang_row.get("object_hint", ""),
                "object_short_hint": lang_row.get("object_short_hint", ""),
                "translation_status": await get_permission_state_translations (existing_translation_status, permission_action ),
                "updated_at": datetime.datetime.utcnow().isoformat(),
                "updated_by": common_data.get("userid", "anonymous"),
                "locked_by": None,
                },
                "$setOnInsert": {
                "object_id": obj_id,
                "requested_language": lang_row.get("language", "Unknown"),
                }
            },
                upsert=True
            )
        else: #its the first time for an object to be added to MongoDB
            try:
                # Convert image to Base64
                image_base64 = base64.b64encode(image_bytes).decode("utf-8")

                # Create new object document
                print ("Creating new object document, common data value: ", common_data)
                
              
                object_document = {
                "sequence_number": await get_next_sequence(MONGODB_DBNAME), 
                "image_name": image_name,
                "image_hash": image_hash,
                "image_base64": image_base64,
                "object_name_en": common_data.get("object_name_en", ""),
                "image_status": await get_permission_state_metadata (common_data.get("image_status"), permission_action ),
               
                "metadata": {
                    "tags":common_data.get("tags", []),
                    "object_category": common_data.get("object_category", ""),
                    "field_of_study": common_data.get("field_of_study", ""),
                    "age_appropriate": common_data.get("age_appropriate", ""),
                    "created_at": datetime.datetime.utcnow().isoformat(),
                    "created_by": common_data.get("userid", "anonymous"),
                }
                }
                # object_document.update( copy_objects_collection("create", common_data, {}) ) #Creating rest of the attributes

                new_object = await objects_collection.insert_one(object_document)
                obj_id = new_object.inserted_id
                # Create initial translation document   
                try:
                    # for lang_row in language_data:
                    await translations_collection.update_one(
                    {
                        "object_id": obj_id,
                        "requested_language": lang_row.get("language", "Unknown")
                    },
                    {
                        "$set": {
                        "object_name": lang_row.get("object_name", ""),
                        "object_description": lang_row.get("object_description", ""),
                        "object_hint": lang_row.get("object_hint", ""),
                        "object_short_hint": lang_row.get("object_short_hint", ""),
                        "translation_status": await get_permission_state_translations (lang_row.get("translation_status"), permission_action ),
                        "updated_at": datetime.datetime.utcnow().isoformat(),
                        "updated_by": common_data.get("userid", "anonymous"),
                        "locked_by": None
                        },
                        "$setOnInsert": {
                        "object_id": obj_id,
                        "requested_language": lang_row.get("language", "Unknown"),
                        
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
   
   
    #just get the fking translation id before returning...
    
    existing_doc = await translations_collection.find_one(
    {
    "object_id": obj_id,
    "requested_language": lang_row.get("language", "Unknown")
    },
    {"_id": 1}
    )
    translation_id = existing_doc["_id"] if existing_doc else None  
    print("Inserted find_one translation id:", translation_id)


    return {"status": "success", 
            "object_id": str(obj_id),
            "translation_id": str(translation_id)}




# --- API to Retrieve immutable _id by object_name_en ---
async def retrieve_object_id(object_name_en: str) -> str:
    obj = await objects_collection.find_one({"object_name_en": object_name_en})
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return str(obj["_id"])

# Function to retrieve data by image hash

def map_object_colletion(object_coll: any):
    
    if object_coll:
        object_coll_mapped = {
            "object_category": object_coll.get("metadata", {}).get("object_category", ""),
            "tags": object_coll.get("metadata", {}).get("tags", []),
            "field_of_study": object_coll.get("metadata", {}).get("field_of_study", ""),
            "age_appropriate": object_coll.get("metadata", {}).get("age_appropriate", ""),
            "object_name_en": object_coll.get("object_name_en", ""),
            "image_status": object_coll.get("image_status", ""),  # metadata object status
            "object_id": str(object_coll.get("_id"))  # object id required for correct record updation at backend
        }
        return object_coll_mapped
    return {"error": "No object data found.."}
    
def map_translation_collection(translation_coll: any):
    if translation_coll:
        translation_coll_mapped = {
            "requested_language": translation_coll.get("requested_language", ""),
            "object_name": translation_coll.get("object_name", ""),
            "object_description": translation_coll.get("object_description", ""),
            "object_hint": translation_coll.get("object_hint", ""),
            "object_short_hint": translation_coll.get("object_short_hint", ""),
            "translation_status": translation_coll.get("translation_status", ""),
            "translation_id": str(translation_coll.get("_id"))  # translation id required for correct record updation at backend
        }
        return translation_coll_mapped
    return {"error": "No translation data found.."}


#getting rid of existing challenges. returning normal object names
async def get_existing_data_imagehash(imagehash: str, language: str): 
    print(f"Searching for image hash: {imagehash} with language: {language}")
    obj = await objects_collection.find_one({"image_hash": imagehash})


    if obj:
      
        obj_id = obj["_id"]  # ✅ ObjectId already stored correctly
        print(f"ObjectId found : {obj_id} ({type(obj_id)})")
        # print(f"Object found with image hash: {imagehash}")
        print(f"Object metadata: {obj.get('metadata', {})}")
        print(f"Object image status: {obj.get('image_status', '')}")

        # ✅ Base response from objects collection
        response = {
            "object_category": obj.get("metadata", {}).get("object_category", ""),
            "tags": obj.get("metadata", {}).get("tags", []),
            "field_of_study": obj.get("metadata", {}).get("field_of_study", ""),
            "age_appropriate": obj.get("metadata", {}).get("age_appropriate", ""),
            "object_name_en": obj.get("object_name_en", ""),
            "image_status": obj.get("image_status", ""),  # metadata object status
            "object_id": str(obj_id),  # Convert ObjectId to string for JSON serialization
            "flag_object": True,
        }

        # ✅ Add translation details if language is provided and exists
        if language:
            translation = await translations_collection.find_one({
                "object_id": obj_id,
                "requested_language": language
            })
            if translation:
                translation_found = True
                print(f"Translation found for language {language}: {translation}")
                response.update({
                    "translated_to":language,
                    "object_name": translation.get("object_name", ""),
                    "object_description": translation.get("object_description", ""),
                    "object_hint": translation.get("object_hint", ""),
                    "object_short_hint": translation.get("object_short_hint", ""),
                    "translation_status": translation.get("translation_status", ""),  # translation status
                    "translation_id": str(translation.get("_id")),  # translation id required for correct record updation at backend
                    "flag_translation": True,
                })

        return response

    return {
        "message": "Neither Object or translation found.",
        "error": "Either Object or Translation not foundd"
    }

# @app.on_event("startup")
# def startup_db_client():
#     global db
#     client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
#     db = client[MONGODB_DBNAME]
#     print(f"Connected to MongoDB at {MONGODB_URI}, using database '{MONGODB_DBNAME}'")

    
async def get_objects_translations_collection(translation_id) -> dict:
    translation_coll = await translations_collection.find_one({"_id": ObjectId(translation_id)})
    object_id = translation_coll.get("object_id")
    object_coll = await objects_collection.find_one({"_id": ObjectId(object_id)})
    
    return {
        "common_data": object_coll,
        "translations": translation_coll,
        "flag_object": True,
        "flag_translation": True
    }

async def mark_translation_doc_unlocked (translation_id: str, userid:str ):
    try:
        # Update the document in translation_collection
        result = translations_collection.update_one(
            {"_id": ObjectId(translation_id)},
            {"$set":{"locked_by": None,
                    "last_skipped": userid,
                    }
            }
        )

        return {
            "status": "success",
            "message": f"Translation {translation_id} unlocked successfully",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error unlocking translation: {str(e)}")



async def get_language_details(language: str):
    lang_doc = await languages_collection.find_one({"language_name": language})
    print("Language retrieved:", lang_doc)
    if lang_doc:
        return lang_doc
    else:
        return {"error":f"Specified language not found {language}"} 


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()