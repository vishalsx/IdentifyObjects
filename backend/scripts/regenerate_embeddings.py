import asyncio
import logging
import os
import sys

# Ensure backend directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.connection import objects_collection, translations_collection
from services.update_embeddings import get_text_embedding, build_embedding_text, translate_text

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("regenerate_embeddings")

async def regenerate_all():
    logger.info("Starting regeneration of embeddings...")
    
    # 1. Iterate over all objects
    cursor = objects_collection.collection.find({})
    count = 0
    
    async for obj in cursor:
        try:
            object_id = obj["_id"]
            logger.info(f"Processing Object: {object_id} - {obj.get('object_name_en')}")
            
            # --- Update Object Embedding ---
            # Fetch approved translations to build rich context
            translations = await translations_collection.collection.find({
                "object_id": object_id,
                "translation_status": "Approved",
                "org_id": "MY-ORG-001"
            }).to_list(length=None)
            
            embedding_text = build_embedding_text(obj, translations)
            if embedding_text:
                vector = get_text_embedding(embedding_text)
                if vector:
                    await objects_collection.collection.update_one(
                        {"_id": object_id},
                        {"$set": {
                            "embedding_text": embedding_text,
                            "embedding_vector": vector
                        }}
                    )
                    logger.info(f"  -> Object embedding updated (len: {len(vector)})")
                else:
                    logger.warning(f"  -> Failed to generate vector for object {object_id}")
            else:
                logger.warning(f"  -> No text for object {object_id}")

            # --- Update Translation Embeddings ---
            # For each translation, we combine its specific text with the object's base text
            obj_text_only = build_embedding_text(obj, None) # Object parts only
            
            trans_cursor = translations_collection.collection.find({"object_id": object_id})
            async for trans in trans_cursor:
                t_id = trans["_id"]
                try:
                    requested_language = trans.get("requested_language", "")
                    
                    # Translate object text if needed
                    if obj_text_only and requested_language:
                         translated_obj_text = await translate_text(obj_text_only, requested_language)
                    else:
                         translated_obj_text = obj_text_only
                    
                    text_parts = [
                        trans.get("object_name", ""),
                    ]
                    combined_text_trans = " ".join(filter(None, text_parts + [translated_obj_text])).strip()
                    
                    if combined_text_trans:
                        t_vector = get_text_embedding(combined_text_trans)
                        if t_vector:
                            await translations_collection.collection.update_one(
                                {"_id": t_id},
                                {"$set": {
                                    "embedding_text": combined_text_trans,
                                    "embedding_vector": t_vector
                                }}
                            )
                            logger.info(f"  -> Translation {t_id} ({trans.get('requested_language')}) updated")
                        else:
                            logger.warning(f"  -> Failed vector for translation {t_id}")
                except Exception as te:
                    logger.error(f"  -> Error processing translation {t_id}: {te}")
            

            count += 1
            # Rate limit protection: Sleep to respect Gemini API limits
            await asyncio.sleep(15)
            
        except Exception as e:
            logger.error(f"Error processing object {obj.get('_id')}: {e}")

    logger.info(f"✅ Regeneration complete. Processed {count} objects.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(regenerate_all())
