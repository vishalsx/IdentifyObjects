
import asyncio
import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv

# Add backend directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DBNAME = os.getenv("MONGODB_DBNAME", "alphatubplay")

def update_index():
    if not MONGODB_URI:
        print("❌ MONGODB_URI not found in environment variables.")
        return

    print(f"🔌 Connecting to MongoDB: {MONGODB_DBNAME}")
    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DBNAME]
    collection = db["translations"]

    index_name = "translations_vector_index"

    # Define the index definition
    index_definition = {
        "fields": [
            {
                "type": "vector",
                "path": "embedding_vector",
                "numDimensions": 768,
                "similarity": "cosine"
            },
            {
                "type": "filter",
                "path": "requested_language"
            },
            {
                "type": "filter",
                "path": "org_id"
            },
            {
                "type": "filter",
                "path": "created_by"
            },
            {
                "type": "filter",
                "path": "_id"
            }
        ]
    }

    print(f"🚀 Updating search index '{index_name}' on collection 'translations'...")
    
    try:
        # Check if index exists
        indexes = list(collection.list_search_indexes(index_name))
        
        if indexes:
            print(f"ℹ️ Index '{index_name}' exists. Updating...")
            try:
                collection.update_search_index(index_name, index_definition)
                print(f"✅ Successfully initiated update for index '{index_name}'.")
                print("   Note: Index updates occur in the background and may take a few minutes.")
            except Exception as e:
                print(f"❌ Failed to update index: {e}")
                # Fallback to drop and recreate if update not supported or fails weirdly
                # print("   Attempting to drop and recreate...")
                # collection.drop_search_index(index_name)
                # collection.create_search_index(model={"name": index_name, "definition": index_definition})
        else:
            print(f"ℹ️ Index '{index_name}' does not exist. Creating...")
            model = {"name": index_name, "definition": index_definition}
            collection.create_search_index(model=model)
            print(f"✅ Successfully initiated creation for index '{index_name}'.")

    except Exception as e:
        print(f"❌ Error managing search index: {e}")
        # Try raw command if helper methods fail
        try:
            print("   Attempting via raw database command 'updateSearchIndex'...")
            cmd = {
                "updateSearchIndex": "translations",
                "name": index_name,
                "definition": index_definition
            }
            db.command(cmd)
            print(f"✅ Successfully initiated update via raw command.")
        except Exception as e2:
            print(f"❌ Raw command also failed: {e2}")

if __name__ == "__main__":
    update_index()
