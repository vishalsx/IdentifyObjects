from fastapi import HTTPException, BackgroundTasks, UploadFile
from bson import ObjectId
import hashlib
from datetime import datetime, timezone
import traceback
import base64
import io
import os
from PIL import Image
from dotenv import load_dotenv
from typing import List, Dict, Any
from services.fileinfo import process_file_info
from utils.common import compute_hash, insert_into_audit, get_permission_state_metadata, get_permission_state_translations, get_next_sequence, make_thumbnail_from_base64
from storage.imagestore import store_image, retrieve_image
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

async def get_image_store_from_hash(image_hash:str) -> dict:
    # This function returns the image store image URL from the given image hash    
    object_coll =  await objects_collection.find_one( {"image_hash": image_hash} )
    if object_coll:
        return object_coll.get("image_store", "")
    return None


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
async def save_to_db(image_name: str,image: UploadFile, common_data: any, lang_row: any, permission_action: str, background_tasks: BackgroundTasks):
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
                                "object_name_en": (common_data.get("object_name_en", existing_object.get("object_name_en", ""))).title(),
                                "image_status": new_metadata_state,
                                "metadata.tags": common_data.get("tags", existing_object.get("metadata", {}).get("tags", [])),
                                "metadata.object_category": (common_data.get("object_category", existing_object.get("metadata", {}).get("object_category", "Other"))).title(),
                                "metadata.field_of_study": (common_data.get("field_of_study", existing_object.get("metadata", {}).get("field_of_study", "Other"))).title(),
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
                
                image_store = await store_image(image,background_tasks) #Saved into Store now.
                # image_base64 = image 
                # Create new object document
                print ("Creating new object document: with Image Store", image_store)
                
                
                file_info = await process_file_info(image,None,image_name,None) #fileinfo at the time of creation

                object_document = {
                "sequence_number": await get_next_sequence(MONGODB_DBNAME), 
                "image_name": image_name,
                "image_hash": image_hash,
                # "image_base64": image_base64, #this needs to be to saved through storage
                "image_store" : image_store,
                "object_name_en": (common_data.get("object_name_en", "")).title(),
                "image_status": await get_permission_state_metadata (common_data.get("image_status"), permission_action ),
               
                "metadata": {
                    "tags":common_data.get("tags", []),
                    "object_category": (common_data.get("object_category", "Other")).title(),
                    "field_of_study": (common_data.get("field_of_study", "Other")).title(),
                    "age_appropriate": common_data.get("age_appropriate", ""),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": common_data.get("userid", "anonymous"),
                },
                "file_info": file_info,
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
                {"object_id": obj_id, "requested_language": (lang_row.get("language", "Unknown")).title()},
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
                        "requested_language": (lang_row.get("language", "Unknown")).title(),
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
    "requested_language": (lang_row.get("language", "Unknown")).title()
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
    obj = await objects_collection.find_one({"object_name_en": object_name_en.title()})
    if not obj:
        raise HTTPException(status_code=404, detail="Object not found")
    return str(obj["_id"])

# Function to retrieve data by image hash

def map_object_collection(object_coll: any):
    
    if object_coll:
        object_coll_mapped = {
            "object_category": (object_coll.get("metadata", {}).get("object_category", "")).title(),
            "tags": object_coll.get("metadata", {}).get("tags", []),
            "field_of_study": (object_coll.get("metadata", {}).get("field_of_study", "")).title(),
            "age_appropriate": object_coll.get("metadata", {}).get("age_appropriate", ""),
            "object_name_en": (object_coll.get("object_name_en", "")).title(),
            "image_status": object_coll.get("image_status", ""),  # metadata object status
            "object_id": str(object_coll.get("_id")),  # object id required for correct record updation at backend
            "flag_object": True
        }
        return object_coll_mapped
    return {"error": "No object data found.."}
    
def map_translation_collection(translation_coll: any):
    if translation_coll:
        translation_coll_mapped = {
            "requested_language": (translation_coll.get("requested_language", "")).title(),
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

# def create_return_file_info(obj_coll: any ) -> dict:
#     if obj_coll and "file_info" in obj_coll:
        
#         file_info_return = obj_coll.get("file_info", {})
#         file_info_return.update({"created_by": obj_coll.get("metadata", {}).get("created_by", {})}) #merging file info from metadata if any
#         file_info_return.update(obj_coll.get("metadata", {}).get("created_at", {})) #merging file info from metadata if any
#         file_info_return.update(obj_coll.get("metadata", {}).get("updated_by", {})) #merging file info from metadata if any
#         file_info_return.update(obj_coll.get("metadata", {}).get("updated_at", {})) #merging file info from metadata if any
        
#     return {file_info_return}

def create_return_file_info(obj_coll: dict) -> dict:
    file_info = obj_coll.get("file_info", {}) or {}
    metadata = obj_coll.get("metadata", {}) or {}

    # Handle case where metadata is a list of dicts
    if isinstance(metadata, list):
        merged_metadata = {}
        for m in metadata:
            if isinstance(m, dict):
                merged_metadata.update(m)
        metadata = merged_metadata

    # Extract values safely
    new_filename = file_info.get("filename")
    size = file_info.get("size")
    dimensions = file_info.get("dimensions", "")
    mime_type = file_info.get("mime_type")

    response = {
        "filename": new_filename,
        "size": f"{size}" if size else None,
        "dimensions": dimensions,
        "mime_type": mime_type,
        "created_by": metadata.get("created_by"),
        "created_at": metadata.get("created_at"),
        "updated_by": metadata.get("updated_by"),
        "updated_at": metadata.get("updated_at"),
    }

    return response



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
            "object_category": (obj.get("metadata", {}).get("object_category", "")).title(),
            "tags": obj.get("metadata", {}).get("tags", []),
            "field_of_study": (obj.get("metadata", {}).get("field_of_study", "")).title(),
            "age_appropriate": obj.get("metadata", {}).get("age_appropriate", ""),
            "object_name_en": (obj.get("object_name_en", "")).title(),
            "image_status": obj.get("image_status", ""),  # metadata object status
            "object_id": str(obj_id),  # Convert ObjectId to string for JSON serialization
            "flag_object": True,
            # "file_info": create_return_file_info(obj),
        }
        response.update(create_return_file_info(obj))
        print("\nBase response from object:", response)
        #Appending fileinfo for the identified object
        # response.update(await process_file_info(None,None,obj.get("image_name"),obj_id)) #?? why to send filename?
        # response.update(await process_file_info(obj_id))
        # ✅ Add translation details if language is provided and exists
        print (f"\nLooking for translation object in {language} for {str(obj_id)}")
        if language:
            translation = await translations_collection.find_one({
                "object_id": obj_id,
                "requested_language": language.title()
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
    print("\n Inside translations_collection with object_id of translations:", object_id)
    # object_coll = await objects_collection.find_one({"_id": ObjectId(object_id)})
    object_coll = await objects_collection.find_one({"_id": ObjectId(object_id)})
    
    return {
        "common_data": object_coll,
        "translations": translation_coll,
        "flag_object": True,
        "flag_translation": True,
        # "file_info": await process_file_info(None,None,None,object_coll["_id"]),
        "file_info": create_return_file_info(object_coll),
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
    print("\nThumbnail translations constructed.. ")
    
    results = []

    for t in translations:
        obj_id = t.get("object_id")
        if not obj_id:
            continue
        
        # 5. Fetch corresponding object
        
        obj_doc = await objects_collection.find_one(
            # {"_id": obj_id}, {"image_hash": 1, "image_base64.": 1, "image_name":1} 
            {"_id": obj_id}, {"image_hash": 1, "image_store":1, "image_name":1, "file_info":1,"metadata":1} 
        )
        if not obj_doc:
            continue
        
        # Calculate image_base64 from the store now.
        image_store = obj_doc.get("image_store", "")
        image_base64 = await retrieve_image (image_store)
        

        # thumbnail_b64 = make_thumbnail_from_base64(obj_doc.get("image_base64", ""))
        thumbnail_b64 = make_thumbnail_from_base64(image_base64)

        results.append({
            "object": {
                "image_hash": obj_doc.get("image_hash"),
                "image_base64": image_base64 or "",
                "thumbnail": (
                    thumbnail_b64.decode("utf-8") if isinstance(thumbnail_b64, bytes) 
                    else (thumbnail_b64 or "")
                ),
            },
            "translation": {
                "translation_id": str(t["_id"]),
                "requested_language": t.get("requested_language"),
                "translation_status": t.get("translation_status"),
            },
            "permissions": list(user_permissions or []),
            "file_info": create_return_file_info(obj_doc) or {},
        })

    print(f"\n Thumbnail Structure for frist object\nImage hash: {results[0]['object']['image_hash']}\nImage Base64: {results[0]['object']['image_base64'][:10]}...\nThumbnail: {results[0]['object']['thumbnail'][:10]}...\nTranslation ID: {results[0]['translation']['translation_id']}\nRequested Language: {results[0]['translation']['requested_language']}\nTranslation Status: {results[0]['translation']['translation_status']}\nPermissions: {results[0]['permissions']}\nFile Info: {results[0]['file_info']}\n") if results else print("No results found.")
    return results




