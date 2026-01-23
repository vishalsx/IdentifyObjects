import asyncio
import logging
import os
from typing import List, Dict, Any, Optional
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
FETCH_SIZE_N = 50     # The fixed number of objects to fetch in each database query iteration for NON-SEARCH path.
MAX_ITERATIONS = 10  # Safety break for the iteration loop (prevents infinite loop if DB is corrupted).
# --- CONSTANTS FOR SEARCH STRATEGY ---
# Multiplier used for the search buffer. If DISPLAY_LIMIT_M is 9, we fetch 9 * 3 = 27 search results initially.
SEARCH_BUFFER_MULTIPLIER = 3
# ---------------------------------------------------

# Configuration based on environment variables
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.85))
SIMILARITY_THRESHOLD_NON_EN = float(os.getenv("SIMILARITY_THRESHOLD_NON_EN", 0.7))

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")

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

async def _search_objects_by_query(search_query: str, org_id: str | None, result_limit: int = 50, skip: int = 0, use_vector_search: bool = True) -> List[Dict[str, Any]]:
    """
    Hybrid search: combines vector search, text search, and fuzzy search.
    Filters by org_id (Private OR Global) and limits to result_limit.
    SKIP is applied artificially here by fetching (skip + limit) and slicing.
    """
    adjusted_limit = result_limit + skip
    logger.info(f"--- Hybrid Search: '{search_query}' (Fetch: {adjusted_limit}, Skip: {skip}, Vector: {use_vector_search}) ---")
    
    if not search_query:
        return []
    
    is_eng = is_english(search_query)
    if not is_eng:
        vector_threshold = SIMILARITY_THRESHOLD_NON_EN 
        fuzzy_threshold = SIMILARITY_THRESHOLD_NON_EN * 1.1 
    else:
        vector_threshold = SIMILARITY_THRESHOLD 
        fuzzy_threshold = SIMILARITY_THRESHOLD * 1.1 
    
    all_results = {} 
    
    # Strategy 1: Vector Search
    if use_vector_search:
        query_vector = get_gemini_embedding(search_query)
        if query_vector:
            vector_results = await _vector_search(query_vector, org_id, threshold=vector_threshold, limit=adjusted_limit)
            for r in vector_results:
                obj_id = str(r["_id"])
                all_results[obj_id] = {
                    **r,
                    "search_score": r.get("score", 0) * 2.0, 
                    "search_method": "vector"
                }

    # Strategy 2: Text Search
    text_results = await _text_search(search_query, org_id, limit=adjusted_limit)
    for r in text_results:
        obj_id = str(r["_id"])
        if obj_id not in all_results:
             all_results[obj_id] = {
                **r,
                "search_score": r.get("score", 0),
                "search_method": "text"
            }
        else:
            # Boost if found in both
             all_results[obj_id]["search_score"] += r.get("score", 0)

    # Strategy 3: Fuzzy
    # Only if low results
    if len(all_results) < adjusted_limit:
        fuzzy_results = await _fuzzy_search_limited(search_query, org_id, threshold=fuzzy_threshold, limit=adjusted_limit)
        for r in fuzzy_results:
            obj_id = str(r["_id"])
            if obj_id not in all_results:
                all_results[obj_id] = {
                    **r,
                    "search_score": r.get("similarity", 0), # Lower confidence
                    "search_method": "fuzzy"
                }

    # Sort and Slice
    sorted_items = sorted(all_results.values(), key=lambda x: x["search_score"], reverse=True)
    
    # Apply Skip and Limit
    final_items = sorted_items[skip : skip + result_limit]
    
    # Clean up temporary fields before returning
    for r in final_items:
        r.pop("search_score", None)
        r.pop("search_method", None)
        r.pop("similarity", None)
        r.pop("matched_token", None)

    return final_items
    
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


async def _process_single_object_for_pool(obj_data: Dict[str, Any], target_language: str | None, languages_allowed: List[str], username: str, org_id: str | None = None) -> Dict[str, Any] | None:
    """Helper to process a single object for the image pool, including work gap and image fetching.
    
    Now uses translation_summary directly from the object instead of querying translations collection.
    """
    object_obj_id = obj_data["_id"]
    logger.info(f"--- Processing Object ID: {object_obj_id} ---")
    
    try:
        if isinstance(object_obj_id, str):
            object_obj_id = ObjectId(object_obj_id)
        
        # Fetch summary data if not already present
        summary = obj_data.get("object_votes_summary", {})
        if not summary:
            summary_doc = await objects_collection.find_one(
                {"_id": object_obj_id},
                {"object_votes_summary": 1, "object_name_en": 1, "translation_summary": 1}
            )
            if summary_doc:
                summary = summary_doc.get("object_votes_summary", {})
                obj_data["object_name_en"] = summary_doc.get("object_name_en", "")
                obj_data["translation_summary"] = summary_doc.get("translation_summary", {})
            else:
                logger.warning(f"Summary for object {object_obj_id} not found.")
                return None

        # --- SIMPLIFIED: Use translation_summary from object ---
        translation_summary = obj_data.get("translation_summary", {})
        
        # Determine translated languages based on org_id
        if org_id:
            # For org users: ONLY use org-specific translations (not global)
            # Org users need translations done for their org, global doesn't count
            translated_langs = translation_summary.get("orgs", {}).get(org_id, {}).get("translated_languages", [])
        else:
            # For non-org users: use global only
            translated_langs = translation_summary.get("global", {}).get("translated_languages", [])
        
        untranslated_languages = [lang for lang in languages_allowed if lang not in translated_langs]
        
        if not untranslated_languages:
            logger.debug(f"Object {object_obj_id} is fully translated for user's allowed languages. Skipping.")
            return None
        
        logger.info(f"Object {object_obj_id}: Translated={translated_langs}, Untranslated={untranslated_languages}")

        # Fetch the full object document if not already fetched by search
        obj_doc = obj_data if "image_store" in obj_data else await objects_collection.find_one(
            {"_id": object_obj_id, "image_status": "Approved"}, 
            {
                "image_hash": 1, 
                "image_store": 1, 
                "object_name_en": 1, 
                "metadata": 1, 
                "object_votes_summary": 1,
                "org_id": 1 # Include org_id for consistency
            }
        )
        if not obj_doc:
            logger.warning(f"Object {object_obj_id} not found or not approved after work gap check.")
            return None

        # Logic to extract object_name from translations collection 
        object_name_translated = None
        specific_language_case = bool(target_language)

        if target_language and target_language in translated_langs:
            tc_doc = await translations_collection.find_one(
                { 
                    "object_id": object_obj_id, 
                    "requested_language": target_language, 
                    "translation_status": "Approved" 
                },
                { "object_name": 1, "object_description": 1 }
            )
            if tc_doc:
                object_name_translated = tc_doc.get("object_name", None)
                logger.debug(f"Found translated name for target language {target_language}: {object_name_translated}")
        
        # Fetch image and thumbnail
        image_base64, thumbnail_b64 = "", ""
        image_store = obj_doc.get("image_store")
        if image_store:
            try:
                image_base64 = await retrieve_image(image_store)
                thumbnail_b64 = await run_in_threadpool(make_thumbnail_from_base64, image_base64, (128, 128))
            except Exception as e:
                logger.warning(f"Error fetching image for object {object_obj_id}: {e}")

        popularity_stars = summary.get("fair_star_rating", 0) 
        total_net_votes = summary.get("total_net_votes", 0)
        smoothed_votes = summary.get("language_scores", {}).get("smoothed_votes", {})

        total_vote_count_human = _format_votes_human(total_net_votes)
        file_info = create_return_file_info(obj_doc) if obj_doc else {}
        
        if specific_language_case is False: 
            object_name_translated = obj_doc.get("object_name_en", "")
        
        return {
            "poolImage": {
                "image_hash": obj_doc.get("image_hash", ""),
                "object_name_en": (
                            object_name_translated
                            # if object_name_translated
                            # else obj_doc.get("object_name_en", "")
                        ),
                "object_id": str(object_obj_id),
                "image_base64": image_base64,
                "thumbnail_base64": thumbnail_b64,
                "metadata": obj_doc.get("metadata", {}),
                "popularity_stars": popularity_stars,
                "total_net_votes": total_net_votes,
                "total_vote_count_human": total_vote_count_human,
                "smoothed_votes": smoothed_votes,
                "file_info": file_info,
                "languages_translated": translated_langs,
                "untranslated_languages": untranslated_languages,
                "org_id": obj_doc.get("org_id")
            }
        }
    except Exception as e:
        logger.exception(f"Error processing object {object_obj_id} for pool: {e}")
        return None


async def get_images_from_pool(
        limit: int = 9, 
        search_query: Optional[str] = None, 
        language: Optional[str] = None, 
        skip: int = 0, 
        last_object_id: Optional[str] = None,
        use_vector_search: bool = True
    ) -> Dict[str, Any]:
    """
    Returns up to `limit` translation summaries for the current user.
    Supports Search Pagination (via `skip`) and Discovery Pagination (via `last_object_id` cursor).
    Returns wrapped response: { items: [], total: int, has_more: bool }
    """
    
    # Use the passed limit
    DISPLAY_LIMIT_M = max(1, limit) 
    
    logger.info(f"--- STARTING get_images_from_pool --- Limit: {DISPLAY_LIMIT_M}, Skip: {skip}, LastID: {last_object_id}, Search: '{search_query}', Vec: {use_vector_search}")
    
    try:
        username = get_current_user_id()
    except Exception as e:
        logger.exception(f"Failed to retrieve current user: {e}")
        return {"items": [], "total": 0, "has_more": False}

    # Step 2: Fetch user data and CRITICAL: Retrieve org_id
    try:
        user_doc = await users_collection.find_one({"username": username})
        if not user_doc:
            return {"items": [], "total": 0, "has_more": False}
            
        languages_allowed = [str(x) for x in user_doc.get("languages_allowed", [])]
        user_permissions = list(user_doc.get("roles", []))
        org_id =  get_organisation_id()
        
        # Org Filter
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
        logger.exception(f"Error retrieving user data: {e}")
        return {"items": [], "total": 0, "has_more": False}

    # List to hold the objects that need processing
    objects_to_process: List[Dict[str, Any]] = []
    
    total_estimated = -1 # Unknown

    if search_query:
        # --- SEARCH PAGINATION ---
        logger.info(f"Executing search branch with SKIP={skip}...")
        
        objects_to_process = await _search_objects_by_query(
            search_query, 
            org_id, 
            result_limit=DISPLAY_LIMIT_M, 
            skip=skip, 
            use_vector_search=use_vector_search
        )
        
        # Estimate total roughly
        if len(objects_to_process) >= DISPLAY_LIMIT_M:
             # Assume more exists if we filled the page
            has_more = True
            total_estimated = 1000 
        else:
            has_more = False
            total_estimated = skip + len(objects_to_process)
            
        # Process and return for Search
        tasks = []
        for obj in objects_to_process:
            tasks.append(_process_single_object_for_pool(obj, language, languages_allowed, username, org_id))
        
        results = await asyncio.gather(*tasks)
        
        final_output_list = []
        for res in results:
            if res:
                final_output_list.append(res)

    else:
        # --- DISCOVERY / POPULARITY PAGINATION (Optimized with translation_summary filter) ---
        # Goal: Find 'DISPLAY_LIMIT_M' objects that have a WORK GAP (untranslated).
        # Sort: fair_star_rating DESC, then total_net_votes DESC, then _id DESC.
        # Now we filter at MongoDB level using translation_summary!
        
        # Build the work gap filter
        # We need objects where at least one of the user's languages is NOT in translated_languages
        if org_id:
            # For org users: check org-specific translations
            translated_langs_path = f"translation_summary.orgs.{org_id}.translated_languages"
        else:
            # For non-org users: check global translations
            translated_langs_path = "translation_summary.global.translated_languages"
        
        # Filter: At least one of languages_allowed is NOT in the translated_languages array
        # This means the object has work to be done
        # Using $nin to find objects where ANY of user's languages is missing
        work_gap_filter = {
            "$or": [
                # Case 1: translation_summary doesn't exist for this path (all languages needed)
                {translated_langs_path: {"$exists": False}},
                # Case 2: At least one allowed language is NOT in the translated list
                {translated_langs_path: {"$nin": languages_allowed}}
            ]
        }
        
        # Build base match stage
        match_stage = {"$and": [org_query, {"image_status": "Approved"}, work_gap_filter]}
        
        # Cursor pagination
        if last_object_id:
            try:
                cursor_obj = await objects_collection.find_one(
                    {"_id": ObjectId(last_object_id)}, 
                    {"object_votes_summary.fair_star_rating": 1, "object_votes_summary.total_net_votes": 1}
                )
                if cursor_obj:
                    summary = cursor_obj.get("object_votes_summary", {})
                    s_rating = summary.get("fair_star_rating", 0)
                    s_votes = summary.get("total_net_votes", 0)
                    
                    # Compound Cursor Logic (Rating -> Votes -> ID)
                    cursor_query = {
                        "$or": [
                            {"object_votes_summary.fair_star_rating": {"$lt": s_rating}},
                            {
                                "$and": [
                                    {"object_votes_summary.fair_star_rating": s_rating},
                                    {"object_votes_summary.total_net_votes": {"$lt": s_votes}}
                                ]
                            },
                            {
                                "$and": [
                                    {"object_votes_summary.fair_star_rating": s_rating},
                                    {"object_votes_summary.total_net_votes": s_votes},
                                    {"_id": {"$lt": ObjectId(last_object_id)}}
                                ]
                            }
                        ]
                    }
                    match_stage["$and"].append(cursor_query)
            except Exception as e:
                logger.error(f"Failed to fetch cursor object {last_object_id}: {e}")
        
        # Fetch with work gap filter applied at DB level
        # No more loop needed - we get exactly what we need
        fetch_limit = DISPLAY_LIMIT_M + 1  # +1 to check has_more
        
        cursor = objects_collection.find(match_stage)\
            .sort([
                ("object_votes_summary.fair_star_rating", -1),
                ("object_votes_summary.total_net_votes", -1),
                ("_id", -1)
            ])\
            .limit(fetch_limit)
        
        batch_objects = await cursor.to_list(length=fetch_limit)
        
        has_more = len(batch_objects) > DISPLAY_LIMIT_M
        if has_more:
            batch_objects = batch_objects[:DISPLAY_LIMIT_M]
        
        # Process batch (these all have work gaps already)
        tasks = []
        for obj in batch_objects:
            tasks.append(_process_single_object_for_pool(obj, language, languages_allowed, username, org_id))
        
        batch_results = await asyncio.gather(*tasks)
        final_output_list = [res for res in batch_results if res]
                
    # Construct response
    response = {
        "items": final_output_list,
        "total": total_estimated if total_estimated != -1 else len(final_output_list), 
        "has_more": has_more
    }
         
    return response
            