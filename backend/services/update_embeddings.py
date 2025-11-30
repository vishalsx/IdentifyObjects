import logging
import os
from bson import ObjectId
from dotenv import load_dotenv
# from pymongo import MongoClient
import google.generativeai as genai
from db.connection import objects_collection, translations_collection
# ----------------------------------------
# 🔧 Configuration
# ----------------------------------------
load_dotenv()
# MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# DB_NAME = os.getenv("DB_NAME", "your_db_name")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configure Gemini client
genai.configure(api_key=GEMINI_API_KEY)

def build_embedding_text(obj: dict, translations: list) -> str:
    """Construct text for embedding from object fields."""
    parts = []

    # ✅ FIXED: Use extend() not extend =
    parts.extend([
        obj.get("object_name_en", ""),
        obj.get("metadata", {}).get("object_category", ""),
        obj.get("metadata", {}).get("field_of_study", ""),
        " ".join(obj.get("metadata", {}).get("tags", []))
    ])

    # Add translations (object_name_translated, tags_translated, etc.)
    if translations:
        for t in translations:
            translated_name = t.get("object_name", "")
            if translated_name:
                parts.append(translated_name)

    # Filter out empty strings and join
    combined_text = " ".join(filter(None, parts)).strip()
    
    # Log for debugging
    logger.debug(f"Embedding text built: {combined_text[:100]}...")
    
    return combined_text



def get_text_embedding(text: str):
    """Fetch embedding vector from Gemini API."""
    if not text:
        return None
    try:
        resp = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text
        )
        return resp.get("embedding")
    except Exception as e:
        logger.error(f"❌ Embedding generation failed: {e}")
        return None

# ----------------------------------------
# 🚀 Background Task
# ----------------------------------------

async def update_object_embeddings(object_id: ObjectId ):
    """
    Background task to update the embedding for an object document.
    Trigger this after creation or update of an object.
    """
    try:
        obj = await objects_collection.find_one(
            {
                "_id": object_id,
                "image_status": "Approved"
            },
            {
                "object_name_en": 1,
                "metadata": 1
            }
        )
        if not obj:
            logger.warning(f"⚠️ Object {object_id} not found for embedding update.")
            return
        # --- Fetch translations for this object ---
        translation = translations_collection.find(
            {
                "object_id": object_id,
                "translation_status": "Approved"
            }, 
            {"object_name": 1}
        )
        translation = await translation.to_list(length=None)

        if not translation:
            logger.warning(f"⚠️ No approved translations found for Object {object_id}. Adding only ")


        
        embedding_text = build_embedding_text(obj, translation)
        if not embedding_text:
            logger.warning(f"⚠️ Object {object_id} has no text to embed.")
            return

        embedding_vector = get_text_embedding(embedding_text)
        if not embedding_vector:
            logger.warning(f"⚠️ Failed to generate embedding for {object_id}.")
            return

        await objects_collection.update_one(
            {"_id": object_id},
            {"$set": {
                "embedding_text": embedding_text,
                "embedding_vector": embedding_vector
            }}
        )
        logger.info(f"✅ Updated embedding for Object ID: {object_id}")

    except Exception as e:
        logger.exception(f"❌ Error updating embedding for object {object_id}: {e}")


# def update_translation_embedding_background(translation_id: str):
#     """
#     Optional: Background task to create embeddings for translations if needed.
#     """
#     try:
#         trans = translations_collection.find_one({"_id": ObjectId(translation_id)})
#         if not trans:
#             logger.warning(f"⚠️ Translation {translation_id} not found.")
#             return

#         text_parts = [
#             trans.get("translated_text", ""),
#             trans.get("requested_language", ""),
#             trans.get("object_name_en", "")
#         ]
#         combined_text = " ".join(filter(None, text_parts)).strip()

#         embedding_vector = get_text_embedding(combined_text)
#         if embedding_vector:
#             translations_collection.update_one(
#                 {"_id": ObjectId(translation_id)},
#                 {"$set": {
#                     "embedding_text": combined_text,
#                     "embedding_vector": embedding_vector
#                 }}
#             )
#             logger.info(f"✅ Updated embedding for Translation ID: {translation_id}")

#     except Exception as e:
#         logger.exception(f"❌ Error updating embedding for translation {translation_id}: {e}")
