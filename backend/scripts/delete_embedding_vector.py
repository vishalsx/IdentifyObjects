import sys
import os
import asyncio

# Add backend directory to sys.path to resolve imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from db.connection import objects_collection
except ImportError as e:
    print(f"❌ Error importing modules: {e}")
    sys.exit(1)

async def delete_embedding_vector():
    """
    Deletes the 'embedding_vector' attribute from all documents in the objects_collection.
    """
    print("\n🚀 Starting deletion of embedding_vector from all objects...\n")
    try:
        # Use raw collection to bypass organization filters and process all documents
        raw_objects = objects_collection.collection
        
        # Update all documents to unset the 'embedding_vector' field
        result = await raw_objects.update_many(
            {},  # Match all documents
            {"$unset": {"embedding_vector": ""}}  # Remove the 'embedding_vector' field
        )
        print(f"✅ Successfully removed 'embedding_vector' from {result.modified_count} documents.")
    except Exception as e:
        print(f"❌ An error occurred while deleting 'embedding_vector': {e}")

if __name__ == "__main__":
    try:
        asyncio.run(delete_embedding_vector())
    except KeyboardInterrupt:
        print("\n🛑 Script interrupted by user.")
    except Exception as e:
        print(f"\n❌ Script failed with error: {e}")