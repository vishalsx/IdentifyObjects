import logging
from bson import ObjectId
from db.connection import translations_collection, objects_collection

logger = logging.getLogger(__name__)

async def update_translation_summary(object_id: str, translation_id: str):
    """
    Updates the translation_summary in the objects_collection based on an approved translation.
    
    Structure:
    "translation_summary": {
      "global": { "translated_languages": ["English", "Hindi"] },
      "orgs": {
        "ORG-ID": { "translated_languages": ["German"] }
      }
    }
    """
    try:
        # Ensure ObjectIds
        if isinstance(object_id, str):
            object_id = ObjectId(object_id)
        if isinstance(translation_id, str):
            translation_id = ObjectId(translation_id)

        # 1. Fetch Translation
        translation = await translations_collection.find_one({"_id": translation_id})
        if not translation:
             logger.warning(f"Translation {translation_id} not found")
             return

        # 2. Check Status
        # Only proceed if Approved
        if translation.get("translation_status") != "Approved":
             logger.info(f"Translation {translation_id} status is '{translation.get('translation_status')}', not 'Approved'. Skipping summary update.")
             return

        requested_language = translation.get("requested_language")
        org_id = translation.get("org_id")

        if not requested_language:
             logger.warning(f"Translation {translation_id} missing requested_language")
             return
             
        # 3. Construct Update
        if org_id:
             # Update for Org
             # Path: translation_summary.orgs.<org_id>.translated_languages
             update_path = f"translation_summary.orgs.{org_id}.translated_languages"
        else:
             # Update for Global
             # Path: translation_summary.global.translated_languages
             update_path = "translation_summary.global.translated_languages"

        update_query = {
             "$addToSet": {
                 update_path: requested_language
             }
        }

        # 4. Execute Update
        # This will create the nested structure if it doesn't exist
        result = await objects_collection.update_one(
            {"_id": object_id},
            update_query
        )
        
        logger.info(f"Updated translation summary for object {object_id}: {result.modified_count} modified. Added '{requested_language}' to '{update_path}'.")
        
    except Exception as e:
        logger.exception(f"Error updating translation summary for object {object_id}: {e}")
