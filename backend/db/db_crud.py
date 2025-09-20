from fastapi import HTTPException
from bson import ObjectId
from typing import Optional
import hashlib
from datetime import datetime, timezone
import traceback
import base64
import io
import os
from PIL import Image
from dotenv import load_dotenv
from typing import List, Dict, Any
from fileinfo import process_file_info
from common import compute_hash, insert_into_audit, get_permission_state_metadata, get_permission_state_translations, get_next_sequence
load_dotenv()

# ✅ Import MongoDB collections from central connection
from db.connection import (
    objects_collection,
    translations_collection,
    counters_collection,
    permission_rules_collection,
    roles_collection,
    users_collection,
    languages_collection,
    MONGODB_DBNAME,
)


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
            new_value = {
                        "image_status": "Rejected",
                        "metadata.updated_at": datetime.now(timezone.utc).isoformat(),
                        "metadata.updated_by": common_data.get("userid", "anonymous"),
                    }
            # build audit entry
            audit_entry = await insert_into_audit (
                objects_collection,
                {"_id": obj_id},
                common_data.get("userid", "anonymous"),
                'RejectText',
                new_value
                )

            result = await objects_collection.update_one(
                {"_id": obj_id},
                {
                "$set": new_value,
                "$push": {"audit_trail": audit_entry}  
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
                locked_by = translation_coll.get("locked_by")
                if locked_by == common_data.get("userid") or locked_by is None:
                    new_value = {
                                "locked_by": None,
                                "translation_status": new_translation_state,
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                                "updated_by": common_data.get("userid", "anonymous"),
                            }
                    
                    audit_entry = await insert_into_audit(
                        translations_collection,
                        {"_id": translation_coll["_id"]},
                        common_data.get("userid", "anonymous"),
                        permission_action,
                        new_value
                    )

                    await translations_collection.update_one(
                        {"_id": translation_coll["_id"]},
                        {
                            "$set": new_value,
                            "$push": {"audit_trail": audit_entry}
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

                    new_value = {
                                "image_status": new_metadata_state,
                                "metadata.updated_at":datetime.now(timezone.utc).isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous"),
                            }

                    audit_entry = await insert_into_audit(
                        objects_collection,
                        {"_id": obj_id},
                        common_data.get("userid", "anonymous"),
                        permission_action,
                        new_value
                    )
                        
                    await objects_collection.update_one(    
                        {"_id": obj_id},
                        {
                            "$set": new_value,
                            "$push": {"audit_trail":audit_entry}
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
async def save_to_db(image_name: str,image: str, common_data: any, lang_row: any, permission_action: str):
    obj_id = None
    translation_id = None
    
    #Following code is for rest of the actions

    if not image:
     #try to ge the record based on translation id
        obj_id = ObjectId(common_data.get("object_id"))
        print("Looking for Objects presence..",obj_id)
        existing_object = await objects_collection.find_one( {"_id": obj_id} )
    else:                                                    #try to get the record based on image hash
        image_hash = await compute_hash(image)
        print("\nPermission Action from frontend: ", permission_action)
        # Check for existing object by image hash only. name can be duplicate
        existing_object = await objects_collection.find_one( {"image_hash": image_hash} )
       
    try:
       
        new_metadata_state = None
        if existing_object:
            obj_id = existing_object["_id"]
            
            
            new_metadata_state = await get_permission_state_metadata (existing_object.get("image_status"), permission_action )
            if new_metadata_state is not None: # Do not update metadata if new transition is None
                if lang_row.get("language", "Unknown").lower() == "english": #update the entire Metadata if send for English, else just update the status

                   new_value = {
                                "object_name_en": common_data.get("object_name_en", existing_object.get("object_name_en", "")),
                                "image_status": new_metadata_state,
                                "metadata.tags": common_data.get("tags", existing_object.get("metadata", {}).get("tags", [])),
                                "metadata.object_category": common_data.get("object_category", existing_object.get("metadata", {}).get("object_category", "")),
                                "metadata.field_of_study": common_data.get("field_of_study", existing_object.get("metadata", {}).get("field_of_study", "")),
                                "metadata.age_appropriate": common_data.get("age_appropriate", existing_object.get("metadata", {}).get("age_appropriate", "")),
                                "metadata.updated_at": datetime.now(timezone.utc).isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous"),
                        }
                   print ("New Value:", new_value)
                   audit_entry = await insert_into_audit(
                       objects_collection,
                       {"_id": obj_id},
                       common_data.get("userid", "anonymous"),
                       permission_action,
                       new_value
                   )
                   
                   await objects_collection.update_one(
                        {"_id": obj_id},
                        {
                        "$set": new_value,
                        "$push": {"audit_trail": audit_entry}
                    }
                    )

                else: #just update the state transition if not english
                    print("\nUpdating only status of Object as the language is not English.")
                    
                    
                    new_value = {
                                
                                "image_status": await get_permission_state_metadata (existing_object.get("image_status"), permission_action ),
                                "metadata.updated_at": datetime.now(timezone.utc).isoformat(),
                                "metadata.updated_by": common_data.get("userid", "anonymous")
                            }
                    audit_entry = await insert_into_audit(
                        objects_collection,
                        {"_id": obj_id},
                        common_data.get("userid", "system"),
                        permission_action,
                        new_value
                    )
                    
                    await objects_collection.update_one(
                        {"_id": obj_id},
                        {
                            "$set": new_value,
                            "$push":{"audit_trail": audit_entry}
                        }
                    )

                    
        else: #its the first time for an object to be added to MongoDB. 
            try:
                # Convert image to Base64
                # image_base64 = base64.b64encode(image_bytes).decode("utf-8")
                image_base64 = image
                # Create new object document
                print ("Creating new object document, common data.")
                
              
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
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": common_data.get("userid", "anonymous"),
                }
                }
                # object_document.update( copy_objects_collection("create", common_data, {}) ) #Creating rest of the attributes

                new_object = await objects_collection.insert_one(object_document)
                obj_id = new_object.inserted_id
                # Create initial translation document   
            except Exception as e:
                # Log error but don't block the main response
                print(f"MongoDB insert error on Object collection: {e}")
                raise HTTPException(status_code=500, detail="Failed to save object to database")

        try:
            # In any case insert the translation data whether exists or doesn't
            # assumption is that obj_id of objects collection is always present at this stage.
            new_value = {
                    "object_name": lang_row.get("object_name", ""),
                    "object_description": lang_row.get("object_description", ""),
                    "object_hint": lang_row.get("object_hint", ""),
                    "object_short_hint": lang_row.get("object_short_hint", ""),
                    "translation_status": await get_permission_state_translations (lang_row.get("translation_status"), permission_action ), #will be Null if new
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "updated_by": common_data.get("userid", "anonymous"),
                    "locked_by": None,
                    "last_skipped": None,                       
                    }
            audit_entry = await insert_into_audit(
                translations_collection,
                {"object_id": obj_id, "requested_language": lang_row.get("language", "Unknown")},
                common_data.get("userid", "anonymous"),
                permission_action,
                new_value
            )            


            await translations_collection.update_one(
                {"object_id": obj_id, "requested_language": lang_row.get("language", "Unknown")},
                {
                    "$set": new_value,
                    "$setOnInsert": {
                        "object_id": obj_id,
                        "requested_language": lang_row.get("language", "Unknown"),
                        "created_at": datetime.now(timezone.utc).isoformat(),  
                        "created_by": common_data.get("userid", "anonymous"),
                    },
                    "$push": {"audit_trail": audit_entry}
                },
                upsert=True
            )

        except Exception as e:
            print(f"MongoDB insert error on translation collection: {e}") # Not sure why error.
            raise HTTPException(status_code=500, detail="Failed to save translation to database")
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
            "translation_id": str(translation_id),
            "flag_translation": True
            }

# --- API to Retrieve immutable _id by object_name_en ---
async def retrieve_object_id(object_name_en: str) -> str:
    obj = await objects_collection.find_one({"object_name_en": object_name_en})
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return str(obj["_id"])

# Function to retrieve data by image hash

def map_object_collection(object_coll: any):
    
    if object_coll:
        object_coll_mapped = {
            "object_category": object_coll.get("metadata", {}).get("object_category", ""),
            "tags": object_coll.get("metadata", {}).get("tags", []),
            "field_of_study": object_coll.get("metadata", {}).get("field_of_study", ""),
            "age_appropriate": object_coll.get("metadata", {}).get("age_appropriate", ""),
            "object_name_en": object_coll.get("object_name_en", ""),
            "image_status": object_coll.get("image_status", ""),  # metadata object status
            "object_id": str(object_coll.get("_id")),  # object id required for correct record updation at backend
            "flag_object": True
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
            "translation_id": str(translation_coll.get("_id")),  # translation id required for correct record updation at backend
            "flag_translation": True,
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
        #Appending fileinfo for the identified object
        response.update(await process_file_info(None,None,obj.get("image_filename"),obj_id))
        # ✅ Add translation details if language is provided and exists
        print (f"\nLooking for translation object in {language} for {str(obj_id)}")
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
                    "flag_translation": translation_found,
                })
        return response

    return {
        "message": "Neither Object or translation found.",
        "error": "Either Object or Translation not foundd"
    }

    
async def get_objects_translations_collection(translation_id) -> dict:
    translation_coll = await translations_collection.find_one({"_id": ObjectId(translation_id)})
    object_id = translation_coll.get("object_id")
    object_coll = await objects_collection.find_one({"_id": ObjectId(object_id)})
    
    return {
        "common_data": object_coll,
        "translations": translation_coll,
        "flag_object": True,
        "flag_translation": True,
        "file_info": await process_file_info(None,None,None,object_coll["_id"]),
    }

async def mark_translation_doc_unlocked (translation_id: str, userid:str ):
    try:
        # Update the document in translation_collection
        new_value = {"locked_by": None,
                    "last_skipped": userid,
                    }
        audit_entry = await insert_into_audit(
            translations_collection,
            {"_id": ObjectId(translation_id)},
            userid,
            'SkipToNext',
            new_value
        )
        await translations_collection.update_one(
            {"_id": ObjectId(translation_id)},
            {"$set":new_value,
             "$push": {"audit_trail": audit_entry}
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



# async def previous_work_items (userid: str):
#     # this function returns last 4 items which the user had worked upon.
#     #It needs to be extended to all items based on the the sort key sent from frontend
#     # Returns imagehash, image(only thumbnail), translation status

# from datetime import datetime, timezone
# from typing import List, Dict, Any
# from motor.motor_asyncio import AsyncIOMotorCollection


async def get_recent_translations(userid: str):
    """
    Return top 3 active translations for a user, joined with their objects.
    """

    # 1. Extract roles assigned to this user
    user_doc = await users_collection.find_one({"username": userid}, {"roles": 1})
    if not user_doc or "roles" not in user_doc:
        return []

    roles = user_doc["roles"]

    # 2. Get permissions from roles
    role_docs = roles_collection.find({"_id": {"$in": roles}}, {"permissions": 1})
    user_permissions: List[str] = []
    async for role_doc in role_docs:
        user_permissions.extend(role_doc.get("permissions", []))

    # 3. For each permission, fetch its rules and collect "from" states
    allowed_states: set[str] = set()
    rules_cursor = permission_rules_collection.find(
        {"_id": {"$in": user_permissions}, "transitionType": "StateChange"}
    )

    async for rule in rules_cursor:
        transitions = rule.get("stateTransitions", {}).get("language", [])
        for t in transitions:
            if "from" in t:
                allowed_states.add(t["from"])
            if "to" in t:
                allowed_states.add(t["to"])    

    print(f"Allowed states for {userid}: {allowed_states}")

    # # 4. Find top 3 active translations for this user, where status is in allowed states
    # query = {
    #     "$and": [
    #         {"translation_status": {"$in": list(allowed_states)}},
    #         {"$or": [{"metadata.created_by": userid}, {"audit_trail.user_id": userid}]},
    #     ]
    # }

    # translations_cursor = translations_collection.find(query).sort("updated_at", -1).limit(3)
    # translations = [doc async for doc in translations_cursor]

    # 4. Aggregation pipeline for per-translation last_activity
    pipeline = [
        {
            "$match": {
                "translation_status": {"$in": list(allowed_states)},
                "$or": [
                    {"metadata.created_by": userid},
                    {"audit_trail.user_id": userid}
                ]
            }
        },
        {
            "$addFields": {
                "created_ts": {
                    "$cond": [
                        {"$ifNull": ["$created_at", False]},
                        {"$toDate": "$created_at"},
                        None
                    ]
                },
                "audit_ts_array": {
                    "$map": {
                        "input": {"$ifNull": ["$audit_trail", []]},
                        "as": "a",
                        "in": {
                            "$cond": [
                                {"$ifNull": ["$$a.timestamp", False]},
                                {"$toDate": "$$a.timestamp"},
                                None
                            ]
                        }
                    }
                }
            }
        },
        {
            "$addFields": {
                "last_audit_ts": {
                    "$cond": [
                        {"$gt": [{"$size": {"$ifNull": ["$audit_ts_array", []]}}, 0]},
                        {"$max": "$audit_ts_array"},
                        None
                    ]
                }
            }
        },
        {
            "$addFields": {
                "last_activity": {
                    "$cond": {
                        "if": {
                            "$and": [
                                {"$ifNull": ["$last_audit_ts", False]},
                                {"$ifNull": ["$created_ts", False]}
                            ]
                        },
                        "then": {
                            "$cond": [
                                {"$gt": ["$last_audit_ts", "$created_ts"]},
                                "$last_audit_ts",
                                "$created_ts"
                            ]
                        },
                        "else": {"$ifNull": ["$last_audit_ts", "$created_ts"]}
                    }
                }
            }
        },
        {"$sort": {"last_activity": -1}},   # newest first
        {"$limit": 3}                       # only top 3
    ]

    translations = await translations_collection.aggregate(pipeline).to_list(length=3)
    print("\nDeduped translations: ", translations)
    
    results = []

    for t in translations:
        obj_id = t.get("object_id")
        if not obj_id:
            continue

        # 5. Fetch corresponding object
        obj_doc = await objects_collection.find_one(
            {"_id": obj_id}, {"image_hash": 1, "image_base64": 1, "image_name":1}
        )
        if not obj_doc:
            continue

        thumbnail_b64 = make_thumbnail_from_base64(obj_doc.get("image_base64", ""))
        # 6. Build return payload
        file_info = await process_file_info(
            file=None,
            base64_str=None,
            filename=obj_doc.get("image_name"),
            object_id=obj_id
        )
        print("\nFile Info: ", file_info)
        results.append({
            "object": {
                "image_hash": obj_doc.get("image_hash"),
                "image_base64": obj_doc.get("image_base64"),
                "thumbnail": thumbnail_b64,
            },
            "translation": {
                "translation_id": str(t["_id"]),
                "requested_language": t.get("requested_language"),
                "translation_status": t.get("translation_status"),
            },
            "permissions": list(user_permissions),
            "file_info": file_info   # merged here instead of separate append
        })

    return results


def make_thumbnail_from_base64(image_base64: str, size=(128, 128)) -> str:
    """
    Convert base64 image to thumbnail and return new base64 string.
    """
    try:
        # Decode base64
        image_data = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_data))

        # Convert to RGB (in case it's PNG with alpha, etc.)
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")

        # Create thumbnail
        image.thumbnail(size)

        # Save back to base64
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=70)
        thumbnail_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return thumbnail_b64
    except Exception as e:
        print(f"⚠️ Error creating thumbnail: {e}")
        return image_base64  # fallback to original
