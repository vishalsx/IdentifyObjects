import sys
import os
import asyncio
import argparse
import logging

# 1. Setup paths and environment FIRST
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

# Force load environment variables before importing anything that uses them
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

# 2. Now import DB components and services
try:
    from bson import ObjectId
    # Import the db and collections from connection
    # We will use .collection to bypass any OrgCollection filters
    from db.connection import db, translations_collection, objects_collection, MONGODB_DBNAME, MONGODB_URI
    from services.update_embeddings import update_translation_embedding_background, build_embedding_text
except ImportError as e:
    print(f"❌ Error importing modules: {e}")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("regenerate_translation_embeddings")

# Use raw collections for bulk update to see all documents
raw_translations = translations_collection.collection if hasattr(translations_collection, 'collection') else db["translations"]
raw_objects = objects_collection.collection if hasattr(objects_collection, 'collection') else db["objects"]

async def process_translation(trans, semaphore, stats):
    """Process a single translation document with rate limiting."""
    async with semaphore:
        translation_id = trans["_id"]
        object_id = trans.get("object_id")
        
        if not object_id:
            logger.warning(f"⚠️ Translation {translation_id} has no object_id. Skipping.")
            stats['errors'] += 1
            return

        try:
            # Fetch object metadata
            obj = await raw_objects.find_one({"_id": object_id})
            if not obj:
                logger.warning(f"⚠️ Object {object_id} not found for translation {translation_id}. Skipping.")
                stats['not_found'] += 1
                return

            # Strategy: Use build_embedding_text(obj, None) to get base object text (English)
            # update_translation_embedding_background will then translate this to the target language
            # and combine it with the translation's own fields before generating embedding.
            object_text = build_embedding_text(obj, None)
            
            await update_translation_embedding_background(object_text, str(translation_id))
            
            stats['updated'] += 1
            if stats['updated'] % 50 == 0:
                logger.info(f"✅ Progress: {stats['updated']} translations updated...")
                
            # Small sleep to respect rate limits further
            await asyncio.sleep(0.1) 

        except Exception as e:
            logger.error(f"❌ Error updating translation {translation_id}: {e}")
            stats['errors'] += 1

async def regenerate_translations(limit=None, concurrency=5):
    """
    Main loop to iterate through translations and update embeddings.
    """
    # Print DB info for debugging
    masked_uri = MONGODB_URI.split("@")[-1] if "@" in MONGODB_URI else MONGODB_URI
    logger.info(f"🔗 Database: {MONGODB_DBNAME}")
    logger.info(f"🔗 Connection: {masked_uri}")

    logger.info(f"🚀 Starting regeneration of translation embeddings (Concurrency: {concurrency}, Limit: {limit})...")
    
    stats = {
        'processed': 0,
        'updated': 0,
        'errors': 0,
        'not_found': 0
    }
    
    # Check count first
    total_in_db = await raw_translations.count_documents({})
    logger.info(f"📦 Total translations found in collection: {total_in_db}")

    if total_in_db == 0:
        logger.error(f"❌ No translations found in collection '{raw_translations.name}' on database '{db.name}'.")
        return

    # Query for translations
    query = {}
    cursor = raw_translations.find(query)
    if limit:
        cursor = cursor.limit(limit)
    
    semaphore = asyncio.Semaphore(concurrency)
    tasks = []
    
    async for trans in cursor:
        stats['processed'] += 1
        task = asyncio.create_task(process_translation(trans, semaphore, stats))
        tasks.append(task)
        
        # Batch tasks to avoid overwhelming memory
        if len(tasks) >= 100:
            await asyncio.gather(*tasks)
            tasks = []
            logger.info(f"Processed batch of 100. Total processed: {stats['processed']}")

    # Process remaining tasks
    if tasks:
        await asyncio.gather(*tasks)

    logger.info("\n" + "="*40)
    logger.info("📊 REGENERATION SUMMARY")
    logger.info("="*40)
    logger.info(f"Total translations processed: {stats['processed']}")
    logger.info(f"Successfully updated:        {stats['updated']}")
    logger.info(f"Objects not found:           {stats['not_found']}")
    logger.info(f"Errors encountered:          {stats['errors']}")
    logger.info("="*40 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Regenerate embeddings for all translations.")
    parser.add_argument("--limit", type=int, help="Limit the number of translations to process.")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent API calls.")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(regenerate_translations(limit=args.limit, concurrency=args.concurrency))
    except KeyboardInterrupt:
        logger.info("\n🛑 Script interrupted by user.")
    except Exception as e:
        logger.exception(f"\n❌ Script failed: {e}")
