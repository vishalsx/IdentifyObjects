import asyncio
import logging
import os
from typing import List, Dict, Any
from bson import ObjectId
from starlette.concurrency import run_in_threadpool
import unicodedata
from rapidfuzz import fuzz

from db.connection import users_collection, translations_collection, objects_collection
from storage.imagestore import retrieve_image
from utils.common import make_thumbnail_from_base64
from services.userauth import get_current_user_id, get_organisation_id
from services.fileinfo import create_return_file_info
# Assuming these services handle org-aware filtering within their logic
from pymongo.errors import OperationFailure
from langdetect import detect, LangDetectException


import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --- CONSTANTS FOR DYNAMIC CURSOR STRATEGY ---
FETCH_SIZE_N = 4     # The fixed number of objects to fetch in each database query iteration for NON-SEARCH path.
MAX_ITERATIONS = 10  # Safety break for the iteration loop (prevents infinite loop if DB is corrupted).
# --- CONSTANTS FOR SEARCH STRATEGY ---
# Multiplier used for the search buffer. If DISPLAY_LIMIT_M is 9, we fetch 9 * 3 = 27 search results initially.
SEARCH_BUFFER_MULTIPLIER = 3
# ---------------------------------------------------

# Configuration based on environment variables
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.85))
SIMILARITY_THRESHOLD_NON_EN = float(os.getenv("SIMILARITY_THRESHOLD_NON_EN", 0.7))

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")

async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        return await x
    return x

def _format_votes_human(n: int) -> str:
    """Formats large vote numbers into K, M, B strings."""
    try:
        if n < 1000:
            return str(n)
        elif n < 1_000_000:
            value = n / 1000
            formatted = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{formatted}K"
        elif n < 1_000_000_000:
            value = n / 1_000_000
            formatted = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{formatted}M"
        else:
            value = n / 1_000_000_000
            formatted = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{formatted}B"
    except Exception as e:
        logger.error(f"Error formatting votes ({n}): {e}")
        return str(n)


def _similar(a: str, b: str) -> float:
    """Compare two strings with fuzzy matching using rapidfuzz."""
    if not a or not b:
        return 0.0
    a_norm = unicodedata.normalize("NFKC", a.strip().lower())
    b_norm = unicodedata.normalize("NFKC", b.strip().lower())

    # Use partial_ratio for substring matching
    partial = fuzz.partial_ratio(a_norm, b_norm) / 100.0
    # Use token_sort_ratio for word order independence
    token_sort = fuzz.token_sort_ratio(a_norm, b_norm) / 100.0
    # Return the higher score for better matching
    return max(partial, token_sort)

def is_english(text: str) -> bool:
    """Detect if text is in English using langdetect."""
    if not text or not text.strip():
        return True
    try:
        return detect(text) == 'en'
    except LangDetectException:
        # Default to English if detection fails
        return True

def get_gemini_embedding(text: str) -> list[float] | None:
    """Return Gemini embedding vector for a string."""
    try:
        resp = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=text
        )
        return resp["embedding"]
    except Exception as e:
        logger.warning(f"⚠️ Error generating Gemini embedding: {e}")
        return None

async def _vector_search(query_vector: list, org_id: str | None, threshold: float = 0.65, limit: int = 50) -> List[Dict[str, Any]]:
    """Perform vector search with given threshold, filtered by org_id (Private OR Global) and 'Approved' status."""
    logger.info(f"🔎 Starting vector search for org_id: {org_id} with threshold: {threshold:.2f}, limit: {limit}")
    try:
        # 1. Build the flexible org filter: Private OR Global
        if org_id:
            # Org objects OR Global objects (org_id missing or null)
            org_query = {
                "$or": [
                    {"org_id": org_id},
                    {"org_id": {"$exists": False}},
                    {"org_id": None}
                ]
            }
        else:
            # Only Global objects (org_id missing or null)
            org_query = {
                "$or": [
                    {"org_id": {"$exists": False}},
                    {"org_id": None}
                ]
            }

        # 2. Combine with search score and image status match criteria
        match_stage = {
            "$match": {
                "$and": [
                    org_query,
                    {"score": {"$gte": threshold}},
                    {"image_status": "Approved"} # CRITICAL: Only Approved objects
                ]
            }
        }
        logger.debug(f"Vector Search Match Stage: {match_stage}")

        pipeline = [
            {
                "$vectorSearch": {
                    "queryVector": query_vector,
                    "path": "embedding_vector",
                    "numCandidates": 300,
                    "limit": limit, # Use dynamic limit
                    "index": "poolsearch_embedding_index",
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "object_name_en": 1,
                    "metadata": 1,
                    "image_store": 1,
                    "embedding_text": 1,
                    "score": {"$meta": "vectorSearchScore"},
                    "org_id": 1, # Include org_id in project for matching
                    "image_hash": 1
                }
            },
            match_stage, 
            {"$sort": {"score": -1}},
            {"$limit": limit}, # Use dynamic limit
        ]

        results_cursor = objects_collection.aggregate(pipeline)
        raw_results = await results_cursor.to_list(length=limit)
        
        # Deduplicate by image_hash, preferring records with org_id
        unique_map = {}
        for obj in raw_results:
            img_hash = obj.get("image_hash")
            # If image_hash is missing, use _id as unique key to preserve the object
            key = img_hash if img_hash else str(obj["_id"])
            
            if key not in unique_map:
                unique_map[key] = obj
            else:
                # If we already have this hash, check if the new one is 'better' (has org_id)
                # If current obj has org_id and the stored one doesn't, swap it.
                if obj.get("org_id") and not unique_map[key].get("org_id"):
                    unique_map[key] = obj
        
        results = list(unique_map.values())

        print ("\n Vector search results: ", results)
        if results:
            avg_score = sum(r.get("score", 0) for r in results) / len(results)
            logger.info(
                f"✅ Vector search: {len(results)} results "
                f"(avg: {avg_score:.3f}, threshold: {threshold:.3f})"
            )
        else:
            logger.info("❌ Vector search returned 0 results.")
        
        return results
        
    except OperationFailure as oe:
        logger.warning(f"⚠️ Vector search not supported (no index): {oe}")
        return []
    except Exception as e:
        logger.warning(f"⚠️ Vector search failed: {e}")
        return []

async def _text_search(search_query: str, org_id: str | None, limit: int = 50) -> List[Dict[str, Any]]:
    """MongoDB text search for fast keyword matching, filtered by org_id (Private OR Global) and 'Approved' status."""
    logger.info(f"🔎 Starting text search for query: '{search_query[:30]}...' and org_id: {org_id}, limit: {limit}")
    try:
        # 1. Build the flexible org filter: Private OR Global
        # if org_id:
        #     org_query = {
        #         "$or": [
        #             {"org_id": org_id},
        #             {"org_id": {"$exists": False}},
        #             {"org_id": None}
        #         ]
        #     }
        # else:
        #     org_query = {
        #         "$or": [
        #             {"org_id": {"$exists": False}},
        #             {"org_id": None}
        #         ]
        #     }

        # 2. Combine text search, status, and org filter
        query_filter = {
            "$text": {"$search": search_query},
            "image_status": "Approved"# Only Approved objects
            # "$and": [org_query] # Inject the flexible org filter
        }
        logger.debug(f"Text Search Query Filter: {query_filter}")
        print(f"\n\n*******Inside text search: Filter used: {query_filter}")
        cursor = objects_collection.find(
            query_filter,
            {
                "_id": 1,
                "object_name_en": 1,
                "metadata": 1,
                "image_store": 1,
                "embedding_text": 1,
                "image_hash": 1,
                "org_id": 1,
                "score": {"$meta": "textScore"}
            }
        ).sort([("score", {"$meta": "textScore"})]).limit(limit) # Use dynamic limit
        
        raw_results = await cursor.to_list(length=limit)

        # Deduplicate by image_hash, preferring records with org_id
        unique_map = {}
        for obj in raw_results:
            img_hash = obj.get("image_hash")
            # If image_hash is missing, use _id as unique key to preserve the object
            key = img_hash if img_hash else str(obj["_id"])
            
            if key not in unique_map:
                unique_map[key] = obj
            else:
                # If we already have this hash, check if the new one is 'better' (has org_id)
                # If current obj has org_id and the stored one doesn't, swap it.
                if obj.get("org_id") and not unique_map[key].get("org_id"):
                    unique_map[key] = obj
        
        results = list(unique_map.values())
        
        if results:
            logger.info(f"✅ Text search: {len(results)} results")
        else:
            logger.info("❌ Text search returned 0 results.")
        
        return results
        
    except Exception as e:
        logger.warning(f"⚠️ Text search failed (index may not exist): {e}")
        return []

async def _fuzzy_search_limited(search_query: str, org_id: str | None, threshold: float, limit: int = 1000) -> List[Dict[str, Any]]:
    """Limited fuzzy search - only check first N approved objects, filtered by org_id (Private OR Global) and 'Approved' status."""
    logger.info(f"🔎 Starting fuzzy search for query: '{search_query[:30]}...' and org_id: {org_id} with threshold: {threshold:.2f}, limit: {limit}")
    try:
        # 1. Build the flexible org filter: Private OR Global
        # if org_id:
        #     org_query = {
        #         "$or": [
        #             {"org_id": org_id},
        #             {"org_id": {"$exists": False}},
        #             {"org_id": None}
        #         ]
        #     }
        # else:
        #     org_query = {
        #         "$or": [
        #             {"org_id": {"$exists": False}},
        #             {"org_id": None}
        #         ]
        #     }

        # Pre-filter using regex for better performance
        prefix = search_query[:3].lower() if len(search_query) >= 3 else search_query.lower()
        regex_pattern = f".*{prefix}.*"
        
        # 2. Combine status, regex filter, and org filter
        fuzzy_pre_filter = {
            "image_status": "Approved", # Only Approved objects
            "$or": [
                {"object_name_en": {"$regex": regex_pattern, "$options": "i"}},
                {"embedding_text": {"$regex": regex_pattern, "$options": "i"}},
                {"metadata.tags": {"$regex": regex_pattern, "$options": "i"}},
            ]
        }         # "$and": [org_query], add it above later
        logger.debug(f"Fuzzy Pre-Filter Query: {fuzzy_pre_filter}")
        print(f"\n\n*******Inside fuzzy search: Filter used: {fuzzy_pre_filter}")   
        cursor =  objects_collection.find(
            fuzzy_pre_filter,
            {
                "_id": 1,
                "metadata": 1,
                "object_name_en": 1,
                "image_store": 1,
                "embedding_text": 1,
                "image_hash": 1,
                "org_id": 1
            }
        ).limit(limit) # Use dynamic limit
        
        raw_objects = await cursor.to_list(length=limit)

        # Deduplicate by image_hash, preferring records with org_id
        unique_map = {}
        for obj in raw_objects:
            img_hash = obj.get("image_hash")
            # If image_hash is missing, use _id as unique key to preserve the object
            key = img_hash if img_hash else str(obj["_id"])
            
            if key not in unique_map:
                unique_map[key] = obj
            else:
                # If we already have this hash, check if the new one is 'better' (has org_id)
                # If current obj has org_id and the stored one doesn't, swap it.
                if obj.get("org_id") and not unique_map[key].get("org_id"):
                    unique_map[key] = obj
        
        objects = list(unique_map.values())

        logger.info(f"📋 Fuzzy pre-filter: {len(objects)} candidates fetched for detailed check (deduplicated from {len(raw_objects)}).")
        
        results = []
        
        # ... (rest of the fuzzy matching logic remains the same)
        for obj in objects:
            embedding_text = obj.get("embedding_text", "")
            if not embedding_text:
                continue
            
            tokens = embedding_text.split()
            max_similarity = 0.0
            matched_token = None
            
            for token in tokens:
                if len(token) < 2:
                    continue
                sim = _similar(token, search_query)
                if sim > max_similarity:
                    max_similarity = sim
                    matched_token = token
            
            if max_similarity >= threshold:
                results.append({
                    **obj,
                    "similarity": max_similarity,
                    "matched_token": matched_token
                })
        
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        
        if results:
            logger.info(
                f"✅ Fuzzy search: {len(results)} matches "
                f"(best: {results[0].get('similarity', 0):.3f})"
            )
        else:
            logger.info("❌ Fuzzy search returned 0 matches.")
        
        # Limit to 50 for search results since we don't need to process thousands
        return results[:50]
    except Exception as e:
        logger.warning(f"⚠️ Fuzzy search failed: {e}")
        return []

async def _search_objects_by_query(search_query: str, org_id: str | None, result_limit: int = 50) -> List[Dict[str, Any]]:
    """
    Hybrid search: combines vector search, text search, and fuzzy search.
    Filters by org_id (Private OR Global) and limits to result_limit.
    """
    logger.info(f"--- Starting Hybrid Search for query: '{search_query[:30]}...' (Limit: {result_limit}) ---")
    if not search_query:
        logger.info("Search query is empty, returning no results.")
        return []
    
    # Determine threshold based on language
    is_eng = is_english(search_query)
    if not is_eng:
        vector_threshold = SIMILARITY_THRESHOLD_NON_EN 
        fuzzy_threshold = SIMILARITY_THRESHOLD_NON_EN * 1.1 
    else:
        vector_threshold = SIMILARITY_THRESHOLD 
        fuzzy_threshold = SIMILARITY_THRESHOLD * 1.1 
    
    logger.info(f"Language detected: {'EN' if is_eng else 'Non-EN'}. Vector T: {vector_threshold:.2f}, Fuzzy T: {fuzzy_threshold:.2f}")

    all_results = {}  # Use dict to deduplicate by _id
    
    # Strategy 1: Vector Search (best for semantic/multilingual)
    query_vector = get_gemini_embedding(search_query)
    if query_vector:
        # Pass dynamic limit to the vector search
        vector_results = await _vector_search(query_vector, org_id, threshold=vector_threshold, limit=result_limit)
        for r in vector_results:
            obj_id = str(r["_id"])
            all_results[obj_id] = {
                **r,
                "search_score": r.get("score", 0) * 2.0,  # Weight vector search higher
                "search_method": "vector"
            }
    
    # Strategy 2: MongoDB Text Search (fast keyword matching)
    # Pass dynamic limit to the text search
    text_results = await _text_search(search_query, org_id, limit=result_limit)
    for r in text_results:
        obj_id = str(r["_id"])
        text_score = r.get("score", 0) / 10.0  # Normalize text score
        if obj_id in all_results:
            all_results[obj_id]["search_score"] += text_score
            all_results[obj_id]["search_method"] += "+text"
        else:
            all_results[obj_id] = {
                **r,
                "search_score": text_score,
                "search_method": "text"
            }
    
    # Strategy 3: Limited Fuzzy Search (only if few results so far)
    # We only run fuzzy search if we got very few results from the other two methods
    if len(all_results) < result_limit // 2: 
        logger.info("⚠️ Few initial results, attempting fuzzy search...")
        # Fuzzy search is limited to 1000 candidates for performance, then matched to score.
        fuzzy_results = await _fuzzy_search_limited(
            search_query, 
            org_id, 
            threshold=fuzzy_threshold,
            limit=1000
        )
        for r in fuzzy_results:
            obj_id = str(r["_id"])
            if obj_id in all_results:
                all_results[obj_id]["search_score"] += r.get("similarity", 0)
                all_results[obj_id]["search_method"] += "+fuzzy"
            else:
                all_results[obj_id] = {
                    **r,
                    "search_score": r.get("similarity", 0),
                    "search_method": "fuzzy"
                }
    
    # Convert back to list and sort by combined score
    final_results = sorted(
        all_results.values(),
        key=lambda x: x.get("search_score", 0),
        reverse=True
    )
    
    logger.info(
        f"✅ Hybrid search total: {len(final_results)} unique results "
    )
    
    # Clean up temporary fields before returning
    for r in final_results:
        r.pop("search_score", None)
        r.pop("search_method", None)
        r.pop("similarity", None)
        r.pop("matched_token", None)
    
    return final_results[:result_limit]

async def filter_translated_languages_for_org_id(oid: ObjectId) -> List[str]:
    
    try:
        print(f"\nFiltering translated languages for object {oid} ...")
        # Query the translations collection for all documents linked to this object_id.
        # We only project the 'requested_language' field to minimize data transfer.
        # Its n org aware function and inserts org filtering logic inside.
        query_filter = {
            "object_id": oid,
            "translation_status": "Approved" # Only consider approved translations
        }
        
        # Using find() and await, assuming an async MongoDB driver (like Motor)
        tc_cursor = translations_collection.find(
            query_filter, 
            projection={"requested_language": 1, "_id": 0}
        )
        
        
        # Extract and deduplicate the languages
        # org_languages = [doc.get("requested_language") for doc in tc_cursor if doc.get("requested_language")]
        # translated_langs = list(set(org_languages))
        documents = await tc_cursor.to_list(length=None) # 
        all_languages = [doc.get("requested_language") for doc in documents if doc.get("requested_language")]
        translated_langs = list(set(all_languages))

        # translated_langs = await tc_cursor.to_list(length=None) # length=None fetches all results

        print(f"\n✅✅✅ Found {translated_langs} unique requested languages via translations collection.")
        return translated_langs
    
    except Exception as e:
        print(f"Error querying translations collection for object {oid}: {e}")
        # Fallback to empty list or default logic if desired


async def get_images_from_pool(limit: int = 9, search_query: str = None, language:str = None) -> List[Dict[str, Any]]:
    """
    Returns up to `limit` translation summaries for the current user, prioritizing work that needs to be done.
    Implements Dynamic Cursor-Based Iteration for non-search (popularity) and a Hybrid Filter Buffer for search.
    """
    
    # Use the passed limit as the Display Limit (M), ensuring it's at least 1
    DISPLAY_LIMIT_M = max(1, limit) 
    # Calculate the buffer size for the search query
    SEARCH_LIMIT_L = DISPLAY_LIMIT_M * SEARCH_BUFFER_MULTIPLIER

    logger.info(f"--- STARTING get_images_from_pool --- Display Limit (M): {DISPLAY_LIMIT_M}, Search Buffer (L): {SEARCH_LIMIT_L}, Search: '{search_query}', Target Lang: {language}")
    
    try:
        username = get_current_user_id()
    except Exception as e:
        logger.exception(f"Failed to retrieve current user: {e}")
        return []

    # Step 2: Fetch user data and CRITICAL: Retrieve org_id
    try:
        user_doc = await users_collection.find_one({"username": username})
        if not user_doc:
            logger.warning(f"No user found with username={username}")
            return []
            
        languages_allowed = [str(x) for x in user_doc.get("languages_allowed", [])]
        user_permissions = list(user_doc.get("roles", []))
        org_id =  get_organisation_id()
        
        logger.info(f"User: {username}, Org ID: {org_id}")
        logger.info(f"CRITICAL 1/3: User's Allowed Languages (Target Set): {languages_allowed}")
        
        # Define the flexible org filter for objects that are either Global or belong to the user's Org
        if org_id:
            org_query = {
                "$or": [
                    {"org_id": org_id},
                    {"org_id": {"$exists": False}},
                    {"org_id": None}
                ]
            }
        else:
            org_query = {
                "$or": [
                    {"org_id": {"$exists": False}},
                    {"org_id": None}
                ]
            }
             
    except Exception as e:
        logger.exception(f"Error retrieving user data for {username}: {e}")
        return []

    # List to hold the objects that need processing, filtered by the work gap
    objects_to_process: List[Dict[str, Any]] = []

    if search_query:
        # --- SEARCH BRANCH (Hybrid Filter Buffer Strategy) ---
        logger.info(f"Executing search branch (Hybrid search + Buffer L={SEARCH_LIMIT_L})...")
        
        # 3a. Fetch a large buffer of L items based on search score
        top_objects_buffer = await _search_objects_by_query(search_query, org_id, result_limit=SEARCH_LIMIT_L) 
        
        if not top_objects_buffer:
            logger.info("Search returned no raw matches.")
            objects_to_process = []
        else:
            # 3b. Extract ObjectIds and perform bulk check for translation status
            object_ids = [ObjectId(obj["_id"]) for obj in top_objects_buffer if isinstance(obj.get("_id"), (ObjectId, str))]
            
            # Fetch summary data in bulk for the entire buffer
            summary_cursor = objects_collection.find(
                {
                    "_id": {"$in": object_ids}, 
                    "image_status": "Approved"
                },
                {
                    "object_votes_summary.total_net_votes": 1,
                    "object_votes_summary.language_scores.raw_net_votes": 1,
                    "object_name_en": 1,
                    "_id": 1
                }
            )
            summary_docs = await summary_cursor.to_list(length=len(object_ids))
            summary_map = {doc["_id"]: doc for doc in summary_docs}

            # 3c. Apply the Work Gap Filter
            for obj in top_objects_buffer:
                oid = obj.get("_id")
                static_summary_doc = summary_map.get(oid)
                
                if static_summary_doc:

                    summary = static_summary_doc.get("object_votes_summary", {})
                    raw_votes_map = summary.get("language_scores", {}).get("raw_net_votes", {})
                    
                    
                    # if org_id: # Replace translated languages via translations collection for org-specific filtering
                    # This is org Aware, works with or without org id and in both cases filters by approved translations
                    translated_langs = await filter_translated_languages_for_org_id(oid)
                    # else:
                    # translated_langs = list(raw_votes_map.keys())    
                    untranslated_languages = [lang for lang in languages_allowed if lang not in translated_langs]
                    # CRITICAL: Only include objects that need work
                    if untranslated_languages:
                        objects_to_process.append({
                            "_id": oid,
                            "total_net_votes": summary.get("total_net_votes", 0),
                            "languages_translated": translated_langs, 
                            "untranslated_languages": untranslated_languages, 
                            "untranslated_count": len(untranslated_languages),
                            "object_name_en": static_summary_doc.get("object_name_en", "")
                        })

            logger.info(f"📋 Search buffer (L={SEARCH_LIMIT_L}) processed. Found {len(objects_to_process)} objects needing work.")

            # 3d. Trim to final display limit (M)
            # The search results are already sorted by relevance (score) from the query, 
            # but we keep the sorting logic here for work gap tie-breakers if needed.
            
            # Sort by search relevance (implicit from the buffer), then by work gap
            objects_to_process = sorted(objects_to_process, key=lambda x: (
                -x.get("total_net_votes", 0), # Using net votes as a proxy for search score relevance tie-breaker
                -x.get("untranslated_count", 0), 
                x["object_name_en"]
            ))
            
            objects_to_process = objects_to_process[:DISPLAY_LIMIT_M]
       
    else:
        # --- NON-SEARCH BRANCH (Dynamic Cursor-Based Iteration Strategy) ---
        logger.info("Executing non-search branch (Dynamic Cursor Strategy)...")
        
        # 1. Prepare for Iteration
        last_id_cursor: ObjectId | None = None
        
        for iteration in range(MAX_ITERATIONS):
            if len(objects_to_process) >= DISPLAY_LIMIT_M:
                break
                
            logger.info(f"🔑 Iteration {iteration + 1}: Fetching {FETCH_SIZE_N} objects, starting after _id: {last_id_cursor}")

            # 2. Query Database (Fixed Limit N)
            query_filter = {
                "image_status": "Approved",
                "$and": [org_query]
            }
            
            if last_id_cursor:
                query_filter["_id"] = {"$gt": last_id_cursor}

            cursor = objects_collection.find(query_filter).sort("_id", 1).limit(FETCH_SIZE_N)
            fetched_chunk = await cursor.to_list(length=FETCH_SIZE_N)
            
            if not fetched_chunk:
                logger.info("Database returned no more records. Stopping iteration.")
                break 
                
            # 3. Update Cursor
            last_id_cursor = fetched_chunk[-1]["_id"]
            logger.debug(f"Cursor advanced to: {last_id_cursor}")
            
            # 4. Keyset/Bulk-Check for Translation Status
            chunk_ids = [obj["_id"] for obj in fetched_chunk]
            
            summary_cursor = objects_collection.find(
                {
                    "_id": {"$in": chunk_ids}, 
                    "image_status": "Approved"
                },
                {
                    "object_votes_summary.total_net_votes": 1,
                    "object_votes_summary.language_scores.raw_net_votes": 1,
                    "object_name_en": 1, 
                    "_id": 1
                }
            )
            summary_docs = await summary_cursor.to_list(length=len(chunk_ids))
            
            # 5. Accumulate Objects Needing Work (The Work-Gap Filter)
            for doc in summary_docs:
                summary = doc.get("object_votes_summary", {})
                raw_votes_map = summary.get("language_scores", {}).get("raw_net_votes", {})

                # if org_id: # Replace translated languages via translations collection for org-specific filtering
                translated_langs = await filter_translated_languages_for_org_id(doc.get("_id"))
                # else:
                #     translated_langs = list(raw_votes_map.keys())

                untranslated_languages = [lang for lang in languages_allowed if lang not in translated_langs]
                
                if untranslated_languages:
                    objects_to_process.append({
                        "_id": doc["_id"],
                        "total_net_votes": summary.get("total_net_votes", 0),
                        "languages_translated": translated_langs,
                        "untranslated_languages": untranslated_languages, 
                        "untranslated_count": len(untranslated_languages),
                        "object_name_en": doc.get("object_name_en", "")
                    })

            logger.info(f"✅ Iteration {iteration + 1}: Collected {len(objects_to_process)} total objects needing work.")

        if len(objects_to_process) > DISPLAY_LIMIT_M:
            objects_to_process = objects_to_process[:DISPLAY_LIMIT_M]

    
    # Step 5: Process objects concurrently (Now objects_to_process is already filtered and trimmed)
    async def _process_object(obj_data, language: str = None):
        object_obj_id = obj_data["_id"]
        logger.info(f"--- Processing Object ID: {object_obj_id} ---")
       
        try:
            if isinstance(object_obj_id, str):
                object_obj_id = ObjectId(object_obj_id)
            
            # Reuse pre-calculated work-gap fields
            translated_languages = obj_data.get("languages_translated", [])
            untranslated_languages = obj_data.get("untranslated_languages", [])
            
            if not untranslated_languages:
                logger.warning(f"Object {object_obj_id} was somehow fully translated despite filter. Dropping.")
                return None
            
            logger.info(f"CRITICAL 2/3: Object's Translated Languages (Status Set): {translated_languages}")
            logger.info(f"CRITICAL 3/3: Calculated Untranslated Languages (Work Gap): {untranslated_languages}")


            # Logic to extract object_name from translations collection 
            object_name_translated = None
            if language and translated_languages:
                if language in translated_languages:
                    # Filter translations collection by strict org_id (since translations are always specific) AND status
                    tc_doc = await translations_collection.find_one(
                        { 
                            "object_id": object_obj_id, 
                            "requested_language": language, 
                            "translation_status": "Approved" # CRITICAL: Only Approved translations
                        },
                        { "object_name": 1, "object_description": 1 }
                    )
                    if tc_doc:
                        object_name_translated = tc_doc.get("object_name", None)
                        logger.debug(f"Found translated name for target language {language}: {object_name_translated}")
          
            # Fetch object document, explicitly including the pre-calculated summary
            obj_doc = await objects_collection.find_one(
                {"_id": object_obj_id, "image_status": "Approved"}, 
                {
                    "image_hash": 1, 
                    "image_store": 1, 
                    "object_name_en": 1, 
                    "metadata": 1, 
                    "object_votes_summary": 1 
                }
            )
            if not obj_doc:
                logger.warning(f"Object {object_obj_id} not found or not approved.")
                return None

            # Fetch image and thumbnail
            image_base64, thumbnail_b64 = "", ""
            image_store = obj_doc.get("image_store")
            if image_store:
                try:
                    image_base64 = await retrieve_image(image_store)
                    thumbnail_b64 = await run_in_threadpool(make_thumbnail_from_base64, image_base64, (128, 128))
                except Exception as e:
                    logger.warning(f"Error fetching image for object {object_obj_id}: {e}")

            # --- Use Pre-calculated Star Rating, Net Votes, and Smoothed Votes from object_votes_summary ---
            summary = obj_doc.get("object_votes_summary", {})
            
            popularity_stars = summary.get("fair_star_rating", 0) 
            total_net_votes = summary.get("total_net_votes", 0)

            # Extract Smoothed Votes for Personalized Quality 
            smoothed_votes = summary.get("language_scores", {}).get("smoothed_votes", {})


            total_vote_count_human = _format_votes_human(total_net_votes)
            file_info = create_return_file_info(obj_doc) if obj_doc else {}
            
            return {
                "poolImage": {
                    "image_hash": obj_doc.get("image_hash", ""),
                    "object_name_en": (
                                object_name_translated
                                # if object_name_translated
                                # else obj_doc.get("object_name_en", "")
                            ),
                    "image_base64": image_base64 or "",
                    "thumbnail_base64": (
                        thumbnail_b64.decode("utf-8") if isinstance(thumbnail_b64, bytes) else (thumbnail_b64 or "")
                    ),
                    "popularity_stars": popularity_stars,
                    "total_vote_count": total_vote_count_human,
                    "raw_net_votes": total_net_votes,
                    "language_quality_scores": smoothed_votes, 
                },
                "permissions": user_permissions,
                "file_info": file_info,
                "translated_languages": translated_languages,
                "untranslated_languages": untranslated_languages,
                "untranslated_count": len(untranslated_languages) 
            }

        except Exception as e:
            logger.exception(f"Error processing object {obj_data.get('_id')}: {e}")
            return None

    
    tasks = [_process_object(obj, language) for obj in objects_to_process]
    results = await asyncio.gather(*tasks)
    results = [r for r in results if r is not None]
    
    # Step 6: Final Sort and Return Deterministically (Only needed if the list wasn't trimmed in the search path)
    try:
        
        results.sort(key=lambda r: (
            -r["poolImage"]["popularity_stars"], 
            -r["poolImage"]["raw_net_votes"], 
            -r.get("untranslated_count", 0), 
            r["poolImage"]["object_name_en"]
        ))
        logger.info(f"--- get_images_from_pool ENDED. Returning {len(results)} images (out of requested {DISPLAY_LIMIT_M}).")
        
        # Return only the DISPLAY_LIMIT_M requested items
        return results[:DISPLAY_LIMIT_M]
        
    except Exception as e:
        logger.error(f"Error sorting results: {e}")
        return results