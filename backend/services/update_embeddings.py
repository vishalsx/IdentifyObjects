import logging
import os
from bson import ObjectId
from dotenv import load_dotenv
# from pymongo import MongoClient
import google.generativeai as genai
from googleapiclient.discovery import build
from db.connection import objects_collection, translations_collection, languages_collection
# ----------------------------------------
# 🔧 Configuration
# ----------------------------------------
load_dotenv()
# MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# DB_NAME = os.getenv("DB_NAME", "your_db_name")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configure Gemini client
genai.configure(api_key=GEMINI_API_KEY)

async def get_language_code(language_name: str) -> str:
    """Map language names to ISO 639-1 codes using the languages collection."""
    if not language_name:
        return "en"
    
    clean_name = language_name.strip().title() # DB uses Title Case (e.g., "Hindi")
    try:
        lang_doc = await languages_collection.find_one({"language_name": clean_name})
        if lang_doc and lang_doc.get("isoCode"):
            return lang_doc["isoCode"]
        
        # Fallback to first 2 letters if not found in DB
        return language_name.strip().lower()[:2]
    except Exception as e:
        logger.warning(f"⚠️ Error fetching language code for {language_name}: {e}")
        return language_name.strip().lower()[:2]

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

# async def update_object_embeddings(object_id: ObjectId ):
#     """
#     Background task to update the embedding for an object document.
#     Trigger this after creation or update of an object.
#     """
#     try:
#         obj = await objects_collection.find_one(
#             {
#                 "_id": object_id,
#                 "image_status": "Approved"
#             },
#             {
#                 "object_name_en": 1,
#                 "metadata": 1
#             }
#         )
#         if not obj:
#             logger.warning(f"⚠️ Object {object_id} not found for embedding update.")
#             return
#         # --- Fetch translations for this object ---
#         translation = translations_collection.find(
#             {
#                 "object_id": object_id,
#                 "translation_status": "Approved"
#             }, 
#             {"object_name": 1}
#         )
#         translation = await translation.to_list(length=None)

#         if not translation:
#             logger.warning(f"⚠️ No approved translations found for Object {object_id}. Adding only ")


        
#         embedding_text = build_embedding_text(obj, translation)
#         if not embedding_text:
#             logger.warning(f"⚠️ Object {object_id} has no text to embed.")
#             return

#         embedding_vector = get_text_embedding(embedding_text)
#         if not embedding_vector:
#             logger.warning(f"⚠️ Failed to generate embedding for {object_id}.")
#             return

#         await objects_collection.update_one(
#             {"_id": object_id},
#             {"$set": {
#                 "embedding_text": embedding_text,
#                 "embedding_vector": embedding_vector
#             }}
#         )
#         logger.info(f"✅ Updated embedding for Object ID: {object_id}")

#     except Exception as e:
#         logger.exception(f"❌ Error updating embedding for object {object_id}: {e}")



async def translate_text(text: str, target_language: str) -> str:
    """Translate text to target language using Gemini."""
    if not text or not target_language or target_language.lower() == "english":
        return text
    
    try:
        # --- ORIGINAL GenAI IMPLEMENTATION (Commented out) ---
        # model = genai.GenerativeModel("models/gemini-flash-latest")
        # prompt = f"Translate the following English text to {target_language}. Provide only the translated text without any explanations.\n\nText: {text}"
        # response = await model.generate_content_async(prompt)
        # return response.text.strip()
        
        # --- NEW Google Translate API Implementation ---
        api_key = os.getenv("GOOGLE_API_KEY") # Use the same key as others
        if not api_key:
            logger.error("❌ GOOGLE_API_KEY not found in environment.")
            return text
            
        service = build('translate', 'v2', developerKey=api_key, cache_discovery=False)
        
        target_code = await get_language_code(target_language)
        
        # Run the blocking service call in a thread
        from starlette.concurrency import run_in_threadpool
        
        result = await run_in_threadpool(
            service.translations().list(
                q=[text],
                target=target_code
            ).execute
        )
        
        if result and 'translations' in result:
            return result['translations'][0]['translatedText']
        
        return text
    except Exception as e:
        logger.warning(f"⚠️ Google Translation failed for '{text}' to {target_language}: {e}")
        return text

async def update_translation_embedding_background(object_text:str, translation_id: str):
    """
    Optional: Background task to create embeddings for translations if needed.
    """
    try:
        # Use .collection to bypass org filtering for system-level update
        trans = await translations_collection.collection.find_one({"_id": ObjectId(translation_id)})
        if not trans:
            logger.warning(f"⚠️ Translation {translation_id} not found.")
            return

        # if trans.get("latest_embedding"):
        #     logger.info(f"⏩ Skipping embedding update for Translation {translation_id} (already up to date).")
        #     return

        requested_language = trans.get("requested_language", "")
        
        # Translate the object description/text to the requested language
        if object_text and requested_language:
            translated_object_text = await translate_text(object_text, requested_language)
        else:
            translated_object_text = object_text

        text_parts = [
            trans.get("object_name", ""),
        ]
        
        # Combine using the translated object text
        combined_text = " ".join(filter(None, text_parts + [translated_object_text])).strip()

        embedding_vector = get_text_embedding(combined_text)
        if embedding_vector:
            await translations_collection.collection.update_one(
                {"_id": ObjectId(translation_id)},
                {"$set": {
                    "embedding_text": combined_text,
                    "embedding_vector": embedding_vector,
                    "latest_embedding": True
                }}
            )
            logger.info(f"✅ Updated embedding for Translation ID: {translation_id}")

    except Exception as e:
        logger.exception(f"❌ Error updating embedding for translation {translation_id}: {e}")



async def update_object_embeddings(object_id: ObjectId,  translation_id: ObjectId = None):
    """
    Background task to update the embedding for an object document.
    Trigger this after creation or update of an object.
    """
    try:
        # Use .collection to bypass org filtering for system-level update
        obj = await objects_collection.collection.find_one(
            {
                "_id": object_id
                # "image_status": "Approved"
            },
            {
                "object_name_en": 1,
                "metadata": 1,
                "latest_embedding": 1
            }
        )
        if not obj:
            logger.warning(f"⚠️ Object {object_id} not found for embedding update.")
            return

        # if not obj.get("latest_embedding"):
        # --- Fetch translations for this object ---
        translation_cursor = translations_collection.collection.find(
            {
                "object_id": object_id
                # "translation_status": "Approved"
            }, 
            {"object_name": 1}
        )
        translation = await translation_cursor.to_list(length=None)

        if not translation:
            logger.warning(f"⚠️ No approved translations found for Object {object_id}.")
        
        embedding_text = build_embedding_text(obj, translation)
        if not embedding_text:
            logger.warning(f"⚠️ Object {object_id} has no text to embed.")
            return

        embedding_vector = get_text_embedding(embedding_text)
        if not embedding_vector:
            logger.warning(f"⚠️ Failed to generate embedding for {object_id}.")
            return

        await objects_collection.collection.update_one(
            {"_id": object_id},
            {"$set": {
                "embedding_text": embedding_text,
                "embedding_vector": embedding_vector,
                "latest_embedding": True
            }}
        )
        logger.info(f"✅ Updated embedding for Object ID: {object_id}")
        # else:
        #     logger.info(f"⏩ Skipping object embedding update for {object_id} (already up to date).")

    except Exception as e:
        logger.exception(f"❌ Error updating embedding for object {object_id}: {e}")
    
    try:
        if translation_id:
            await update_translation_embedding_background(build_embedding_text(obj, None), translation_id)
            # logger.info(f"✅ Updated embedding for translation ID: {translation_id}")

    except Exception as e:
        logger.exception(f"❌ Error updating embedding for translation {translation_id}: {e}")


