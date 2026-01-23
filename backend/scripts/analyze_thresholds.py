import asyncio
import logging
import os
import sys

# Ensure backend directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from db.connection import translations_collection, objects_collection
from services.update_embeddings import get_text_embedding

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("analyze_thresholds")

async def analyze_query(query_text, lang_label):
    logger.info(f"\n--- Analyzing '{query_text}' ({lang_label}) ---")
    
    vector = get_text_embedding(query_text)
    if not vector:
        logger.error("Failed to generate embedding.")
        return

    pipeline = [
        {
            "$vectorSearch": {
                "index": "translations_vector_index",
                "path": "embedding_vector",
                "queryVector": vector,
                "numCandidates": 50,
                "limit": 5
            }
        },
        {
            "$set": {
                "score": {"$meta": "vectorSearchScore"}
            }
        },
        {
            "$project": {
                "score": 1,
                "embedding_text": 1,
                "object_name": 1,
                "requested_language": 1
            }
        }
    ]

    try:
        cursor = translations_collection.collection.aggregate(pipeline)
        results = await cursor.to_list(length=5)
        
        if not results:
            logger.info("No results found.")
            return

        for i, res in enumerate(results):
            score = res.get('score', 0)
            text = res.get('embedding_text', 'N/A')[:50] + "..."
            name = res.get('object_name', 'N/A')
            lang = res.get('requested_language', 'N/A')
            logger.info(f"Rank {i+1}: Score={score:.4f} | Name={name} ({lang}) | Text context={text}")

    except Exception as e:
        logger.error(f"Search failed: {e}")

async def main():
    logger.info("Starting Similarity Threshold Analysis...")
    
    # English Queries
    await analyze_query("Apple", "English")
    await analyze_query("Dog", "English")
    await analyze_query("Science", "English")
    
    # Hindi Queries
    await analyze_query("सेब", "Hindi - Apple") # Apple
    await analyze_query("कुत्ता", "Hindi - Dog") # Dog
    await analyze_query("विज्ञान", "Hindi - Science") # Science
    await analyze_query("जानवर", "Hindi - Animal") # Animal

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
