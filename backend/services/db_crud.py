from fastapi import HTTPException, BackgroundTasks, UploadFile
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from datetime import datetime, timezone
import traceback
from dotenv import load_dotenv
from services.fileinfo import process_file_info, create_return_file_info
from utils.common import compute_hash, insert_into_audit, get_permission_state_metadata, get_permission_state_translations, get_next_sequence
from storage.imagestore import store_image
from services.update_embeddings import update_object_embeddings


load_dotenv()

# ✅ Import MongoDB collections from central connection
from db.connection import (
    objects_collection,
    translations_collection,
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
            # obj_id = await manage_rejection(common_data)
            pass
            
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
async def save_to_db(image_name: str,image: UploadFile, image_hash:str , common_data: any, lang_row: any, permission_action: str, background_tasks: BackgroundTasks):
    obj_id = None
    translation_id = None
    print ("\n🔶🔶🔶 Common data received fro frontend", common_data)
    print ("\n🔶🔶🔶 language row", lang_row)
    if not image:
     #try to ge the record based on translation id
        obj_id = ObjectId(common_data.get("object_id"))
        print("Looking for Objects presence..",obj_id)
        existing_object = await objects_collection.find_one( {"_id": obj_id} )
    
    elif image_hash:
        print ("\nImage Hash provided from frontend: ", image_hash)
        # Check for existing object by image hash only. name can be duplicate
        existing_object = await objects_collection.find_one( {"image_hash": image_hash} )

    else:                                                    #try to get the record based on image hash
        image_hash = await compute_hash(image)
        print ("\nImage Hash computed: ", image_hash)
        # print("\nPermission Action from frontend: ", permission_action)
        # Check for existing object by image hash only. name can be duplicate
        existing_object = await objects_collection.find_one( {"image_hash": image_hash} )
       
    was_created = False
    if not existing_object:
        # Atomic Upsert Attempt
        try:
            print ("\nObject not found. Attempting Atomic Upsert for hash:", image_hash)
            # Prepare data for insertion
            if image:
                 image_store = await store_image(image, background_tasks)
                 file_info = await process_file_info(image,None,image_name,None)
            else:
                 # Should not happen based on logic above, but handle safely
                 image_store = ""
                 file_info = {}

            seq_num = await get_next_sequence(MONGODB_DBNAME)
            
            # Initial Status
            initial_status = await get_permission_state_metadata(None, permission_action)
            
            object_document = {
                "sequence_number": seq_num, 
                "image_name": image_name,
                "image_hash": image_hash,
                "image_store" : image_store,
                "object_name_en": (common_data.get("object_name_en", "")).title(),
                "image_status": initial_status,
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

            # Atomic Find-One-And-Update with Upsert
            existing_object = await objects_collection.find_one_and_update(
                {"image_hash": image_hash},
                {"$setOnInsert": object_document},
                upsert=True,
                return_document=ReturnDocument.AFTER
            )
            
            # Check if we were the creator
            if existing_object.get("sequence_number") == seq_num:
                was_created = True
                obj_id = existing_object["_id"]
                print(f"✅ Created new object {obj_id} via upsert.")
            else:
                print(f"⚠️ Race condition encountered. Object {existing_object.get('_id')} was created by another process.")
        
        except DuplicateKeyError:
             print("⚠️ DuplicateKeyError caught during upsert. Fetching existing object.")
             existing_object = await objects_collection.find_one( {"image_hash": image_hash} )
        except Exception as e:
             print(f"❌ Error during atomic upsert: {e}")
             raise HTTPException(status_code=500, detail="Failed to save object to database")

    # Proceed with Logic
    if existing_object:
        obj_id = existing_object["_id"]
        
        # Only run update logic if we didn't just create it (or if logic dictates updates on creation too, but typically creation sets initial state)
        if not was_created:
            new_metadata_state = await get_permission_state_metadata (existing_object.get("image_status"), permission_action )
            if new_metadata_state is not None: # Do not update metadata if new transition is None
                if lang_row.get("language", "Unknown").lower() == "english": #update the entire Metadata if send for English, else just update the status
                    print("\n🔴🔴🔴Updating full Object metadata as the language is English.", common_data)
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
                    # print ("New Value:", new_value)
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

    try:
        # In any case upsert the translation data whether exists or doesn't
        # assumption is that obj_id of objects collection is always present at this stage.
        
        # Getting real translation status from the database rather than relying on what is passed from frontend
        tc = await translations_collection.find_one(
            { "object_id": obj_id, "requested_language": (lang_row.get("language", "Unknown")).title()},
            {
                "_id": 1,
                "translation_status": 1
            }
        )
        if tc:
            current_translation_status = tc.get("translation_status")
        else:
            current_translation_status = None
        
        new_value = {
                "object_name": lang_row.get("object_name", ""),
                "object_description": lang_row.get("object_description", ""),
                "object_hint": lang_row.get("object_hint", ""),
                "object_short_hint": lang_row.get("object_short_hint", ""),
                "quiz_qa": lang_row.get("quiz_qa", []),  # list of {question, answer}
                "translation_status": await get_permission_state_translations (current_translation_status, permission_action ),    #use current status from DB to get new status
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
       
        # Update the language Key in objects_collection if its a new language translation
        # This would help in filtering objects by available languages
        # update_one for objects collection is already org aware. Will modfy the objectd document if if global.
        
        try:
            new_lang = lang_row.get("language", "Unknown").title()
            language_key = f"object_votes_summary.language_scores.raw_net_votes.{new_lang}"
            query_filter = {
                "_id": obj_id,
                language_key: {"$exists": False} 
            }
            update_data = {
                "$set": {
                    language_key: 0
                }
            }
            key_update = await objects_collection.update_one(
                query_filter, # Use the conditional filter
                update_data,
                upsert=False 
            )
            print(f"\nConditional raw_net_votes update result: matched {key_update.matched_count}, modified {key_update.modified_count}")
        except Exception as e:
            print(f"Error conditionally updating raw_net_votes for object {obj_id}: {e}")

    except Exception as e:
        print(f"MongoDB insert error on translation collection: {e}") # Not sure why error.
        raise HTTPException(status_code=500, detail="Failed to save translation to database")

    #just get the fking translation id before returning... 
    existing_doc = await translations_collection.find_one(
    {
    "object_id": obj_id,
    "requested_language": (lang_row.get("language", "Unknown")).title()
    },
    {"_id": 1}
    )
    translation_id = existing_doc["_id"] if existing_doc else None  

    # Update vector embedding of Object data alongwith translations data for multilingual vector search
    try: # Trigger background task to update embedding
            background_tasks.add_task(update_object_embeddings,obj_id,translation_id)  
    except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to schedule background embeddings: {str(e)}")


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
            "quiz_qa": translation_coll.get("quiz_qa", []),  # list of {question, answer}
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
                    "quiz_qa": translation.get("quiz_qa", []),  # list of {question, answer}
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


