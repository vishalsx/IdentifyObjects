"""
Backfill script to populate translation_summary for all objects
based on existing approved translations.

Run from the backend directory:
    python scripts/backfill_translation_summary.py
"""

import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGODB_DBNAME", "image_lexicon")

async def main():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    translations_collection = db["translations"]
    objects_collection = db["objects"]
    
    print("\n" + "="*60)
    print("🚀 TRANSLATION SUMMARY BACKFILL SCRIPT")
    print("="*60)
    logger.info(f"Connecting to database: {DB_NAME}")
    
    # Aggregation pipeline to group approved translations by object_id and org_id
    pipeline = [
        {"$match": {"translation_status": "Approved"}},
        {
            "$group": {
                "_id": {
                    "object_id": "$object_id",
                    "org_id": "$org_id"  # Will be null for global
                },
                "languages": {"$addToSet": "$requested_language"}
            }
        }
    ]
    
    print("\n📊 Phase 1: Aggregating approved translations...")
    cursor = translations_collection.aggregate(pipeline)
    
    # Build a map: object_id -> { global: [...], orgs: { org_id: [...] } }
    object_summaries = {}
    total_translation_records = 0
    global_translation_count = 0
    org_translation_count = 0
    unique_orgs = set()
    
    async for doc in cursor:
        total_translation_records += 1
        object_id = doc["_id"].get("object_id")
        org_id = doc["_id"].get("org_id")  # May be None or missing
        languages = doc.get("languages", [])
        
        if object_id not in object_summaries:
            object_summaries[object_id] = {"global": [], "orgs": {}}
        
        if org_id is None:
            # Extend global languages
            object_summaries[object_id]["global"].extend(languages)
            global_translation_count += len(languages)
        else:
            # Extend org-specific languages
            unique_orgs.add(org_id)
            if org_id not in object_summaries[object_id]["orgs"]:
                object_summaries[object_id]["orgs"][org_id] = []
            object_summaries[object_id]["orgs"][org_id].extend(languages)
            org_translation_count += len(languages)
    
    print(f"   ✅ Found {len(object_summaries)} unique objects with approved translations")
    print(f"   ✅ Total translation groups: {total_translation_records}")
    print(f"   ✅ Global translations: {global_translation_count}")
    print(f"   ✅ Org-specific translations: {org_translation_count} across {len(unique_orgs)} orgs")
    
    # Update each object
    print("\n📝 Phase 2: Updating object documents...")
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    for object_id, summary_data in object_summaries.items():
        try:
            # Deduplicate
            global_langs = list(set(summary_data["global"]))
            orgs_data = {}
            for org_id, langs in summary_data["orgs"].items():
                orgs_data[org_id] = {"translated_languages": list(set(langs))}
            
            translation_summary = {
                "global": {"translated_languages": global_langs},
                "orgs": orgs_data
            }
            
            result = await objects_collection.update_one(
                {"_id": object_id},
                {"$set": {"translation_summary": translation_summary}}
            )
            
            if result.modified_count > 0:
                updated_count += 1
            else:
                skipped_count += 1
                
            # Progress indicator
            processed = updated_count + skipped_count + error_count
            if processed % 100 == 0 and processed > 0:
                print(f"   ... Processed {processed}/{len(object_summaries)} objects")
                
        except Exception as e:
            error_count += 1
            logger.error(f"Error updating object {object_id}: {e}")
    
    # Final Summary
    print("\n" + "="*60)
    print("📋 BACKFILL SUMMARY")
    print("="*60)
    print(f"   Total objects processed:    {len(object_summaries)}")
    print(f"   ✅ Successfully updated:    {updated_count}")
    print(f"   ⏭️  Skipped (no change):     {skipped_count}")
    print(f"   ❌ Errors:                   {error_count}")
    print(f"   📊 Global languages added:  {global_translation_count}")
    print(f"   🏢 Org languages added:     {org_translation_count}")
    print(f"   🏢 Unique organizations:    {len(unique_orgs)}")
    print("="*60)
    print("✨ Backfill complete!\n")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(main())
