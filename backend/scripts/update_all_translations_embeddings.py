import sys
import os
import asyncio
from bson import ObjectId

# Add backend directory to sys.path to resolve imports
# Assuming script is run from backend/ directory or its parent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from db.connection import translations_collection, objects_collection
    from services.update_embeddings import update_translation_embedding_background, build_embedding_text
except ImportError as e:
    print(f"❌ Error importing modules: {e}")
    print("Please ensure you are running the script from the 'backend' directory or that the python path is set correctly.")
    sys.exit(1)

async def update_all_translations():
    """
    Standalone script to go through all documents of translations collection,
    get the corresponding object collection document, and update embeddings.
    """
    print("\n🚀 Starting bulk update of translation embeddings...\n")
    
    # Use raw collections to bypass organization filters and process all documents
    raw_translations = translations_collection.collection
    raw_objects = objects_collection.collection
    
    total_processed = 0
    total_updated = 0
    total_errors = 0
    not_found_objects = 0
    
    # Iterate through all translations
    cursor = raw_translations.find({})
    
    async for trans in cursor:
        total_processed += 1
        translation_id = trans["_id"]
        object_id = trans.get("object_id")
        
        if not object_id:
            print(f"⚠️ Translation {translation_id} has no object_id. Skipping.")
            total_errors += 1
            continue
            
        try:
            # 1. Extract metadata and fields from object collection
            # Query the raw objects collection to ensure we find the object regardless of org_id
            obj = await raw_objects.find_one({"_id": object_id})
            
            if not obj:
                print(f"⚠️ Object {object_id} not found for translation {translation_id}. Skipping.")
                not_found_objects += 1
                continue
            
            # 2. Get combined text for object fields using build_embedding_text
            # Passing None to build_embedding_text ensures only object fields are included:
            # (object_name_en, metadata.object_category, metadata.field_of_study, metadata.tags[])
            object_text = build_embedding_text(obj, None)
            
            # 3. Call update_translation_embedding_background
            # This function will append the translation's own fields (language, object_name) 
            # and update the embedding_text and embedding_vector in translation_collection.
            await update_translation_embedding_background(object_text, str(translation_id))
            
            total_updated += 1
            if total_updated % 10 == 0:
                print(f"✅ Progress: {total_processed} processed, {total_updated} updated...")
                
        except Exception as e:
            print(f"❌ Error updating translation {translation_id}: {e}")
            total_errors += 1

    print("\n" + "="*40)
    print("📊 UPDATE SUMMARY")
    print("="*40)
    print(f"Total translations processed: {total_processed}")
    print(f"Successfully updated:        {total_updated}")
    print(f"Objects not found:           {not_found_objects}")
    print(f"Errors encountered:          {total_errors}")
    print("="*40 + "\n")

if __name__ == "__main__":
    try:
        asyncio.run(update_all_translations())
    except KeyboardInterrupt:
        print("\n🛑 Script interrupted by user.")
    except Exception as e:
        print(f"\n❌ Script failed with error: {e}")
