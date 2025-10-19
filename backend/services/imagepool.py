
import asyncio
import logging
import os
from typing import List, Dict, Any
from bson import ObjectId
from starlette.concurrency import run_in_threadpool
from difflib import SequenceMatcher

from db.connection import users_collection, translations_collection, objects_collection
from storage.imagestore import retrieve_image
from utils.common import make_thumbnail_from_base64
from services.userauth import get_current_user_id
from services.db_crud import create_return_file_info
from pymongo.errors import OperationFailure


logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.85))

async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        return await x
    return x

def _format_votes_human(n: int) -> str:
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
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

#####

import os
import google.generativeai as genai

from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")

def get_gemini_embedding(text: str) -> list[float]:
    """Return Gemini embedding vector for a string."""
    try:
        resp = genai.embed_content(
            model=EMBEDDING_MODEL,  # Gemini embedding model
            content=text
        )
        return resp["embedding"]
    except Exception as e:
        logger.warning(f"⚠️ Error generating Gemini embedding: {e}")
        return None


async def _search_objects_by_query(search_query: str) -> List[Dict[str, Any]]:
    """
    Perform typo-tolerant, synonym-aware, and vector-based semantic search.
    Falls back to fuzzy local matching if embeddings or vector index are unavailable.
    """
    if not search_query:
        return []
    threshold = SIMILARITY_THRESHOLD

    expanded_queries = {search_query.lower()}
    query_vector = get_gemini_embedding(search_query)
    # Step 1: Try vector search first (if embeddings + model are available)
    if query_vector:
        try:
            # query_vector = embedding_model.encode(search_query).tolist()
            pipeline = [
                {
                    "$vectorSearch": {
                        "queryVector": query_vector,
                        "path": "embedding_vector",
                        "numCandidates": 200,
                        "limit": 50,
                        "index": "poolsearch_embedding_index",  # change this to your index name
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "object_name_en": 1,
                        "metadata": 1,
                        "image_store": 1,
                        "score": {"$meta": "vectorSearchScore"},
                    }
                },
                # ✅ Filter by score threshold
                {"$match": {"score": {"$gte": threshold}}},  # Only keep scores >= 0.7
                {"$sort": {"score": -1}},
                {"$limit": 20},            ]

            results = await objects_collection.aggregate(pipeline).to_list(length=20)
            if results:
                avg_score = sum(r.get("score", 0) for r in results) / len(results)
                logger.info(
                    f"✅ Vector search found {len(results)} matches for '{search_query}' "
                    f"(avg score: {avg_score:.4f})"
                )
            logger.info(f"⚠️ Vector search returned no results, falling back to fuzzy search")

        except OperationFailure as oe:
            # Happens when the vector index doesn't exist yet
            logger.warning(f"⚠️ Vector search not supported (no index or local MongoDB): {oe}")
        except Exception as e:
            logger.warning(f"⚠️ Vector search failed, falling back to fuzzy: {e}")

    # Step 2: Fallback to fuzzy local matching
    logger.info(f"🔄 Falling back to fuzzy search for '{search_query}'")

    cursor = objects_collection.find(
        {"image_status": "Approved"},
        {"_id": 1, "metadata": 1, "object_name_en": 1, "image_store": 1},
    )
    objects = await cursor.to_list(length=None)

    results = []

    for obj in objects:
        fields = [
            obj.get("object_name_en", ""),
            obj.get("metadata", {}).get("object_category", ""),
            obj.get("metadata", {}).get("field_of_study", ""),
        ] + obj.get("metadata", {}).get("tags", [])

        for f in fields:
            for q in expanded_queries:
                sim = _similar(f, q)
                if sim >= threshold:
                    results.append(obj)
                    break
            if obj in results:
                break

    logger.info(f"🧩 Fuzzy search matched {len(results)} results for '{search_query}'")
    return results


###########

# async def _search_objects_by_query(search_query: str) -> List[Dict[str, Any]]:
#     """Perform typo-tolerant, fuzzy, synonym-aware search over multiple fields."""
#     if not search_query:
#         return []

#     # Build expanded query list with synonyms
#     expanded_queries = {search_query.lower()}
#     # for term in search_query.lower().split():
#     #     if term in SYNONYMS:
#     #         expanded_queries.update(SYNONYMS[term])

#     # Fetch all objects and score similarity
#     cursor = objects_collection.find({"image_status":"Approved"}, {"_id":1,"metadata": 1, "object_name_en": 1, "image_store": 1})
#     objects = await cursor.to_list(length=None)

#     threshold = SIMILARITY_THRESHOLD
#     results = []

#     # while threshold > 0.4 and not results:  # dynamically lower threshold if no matches
#     for obj in objects:
#         fields = [
#             obj.get("object_name_en", ""),
#             obj.get("metadata", {}).get("object_category", ""),
#             obj.get("metadata", {}).get("field_of_study", ""),
#         ] + obj.get("metadata", {}).get("tags", [])

#         for f in fields:
#             for q in expanded_queries:
#                 sim = _similar(f, q)
#                 if sim >= threshold:
#                     results.append(obj)
#                     break
#             if obj in results:
#                 break
#         # threshold -= 0.05  # lower threshold gradually
#     return results

async def get_images_from_pool(limit: int = 9, search_query: str = None) -> List[Dict[str, Any]]:
    """Returns up to `limit` translation summaries for the current user in deterministic order."""
    try:
        username = await _maybe_await(get_current_user_id())
    except Exception as e:
        logger.exception(f"Failed to retrieve current user: {e}")
        return []

    # Step 2: Fetch user data
    try:
        user_doc = await users_collection.find_one({"username": username})
        if not user_doc:
            logger.warning(f"No user found with username={username}")
            return []
        languages_allowed = [str(x) for x in user_doc.get("languages_allowed", [])]
        user_permissions = list(user_doc.get("roles", []))
    except Exception as e:
        logger.exception(f"Error retrieving user data for {username}: {e}")
        return []

    # Step 3: Aggregate top objects by total net votes
    try: ###########################################
        if search_query:
            # --- SEARCH BRANCH ---
            # Step 3a: Search objects based on query (fuzzy + synonym aware)
            matching_objects = await _search_objects_by_query(search_query)
            if not matching_objects:
                top_objects = []
            else:
                # Extract valid ObjectIds from matching results
                object_ids = []
                for obj in matching_objects:
                    oid = obj.get("_id")
                    if isinstance(oid, str) and ObjectId.is_valid(oid):
                        oid = ObjectId(oid)
                    if isinstance(oid, ObjectId):
                        object_ids.append(oid)

                if not object_ids:
                    top_objects = []
                else:
                    # --- Step 3b: Aggregate translations for matched object_ids ---
                    pipeline = [
                        {
                            "$match": {
                                "translation_status": "Approved",
                                "object_id": {"$in": object_ids},  # ensure ObjectId matching
                            }
                        },
                        {
                            "$addFields": {
                                "net_votes": {
                                    "$subtract": [
                                        {"$ifNull": ["$up_votes", 0]},
                                        {"$ifNull": ["$down_votes", 0]},
                                    ]
                                }
                            }
                        },
                        {
                            "$group": {
                                "_id": "$object_id",
                                "total_net_votes": {"$sum": "$net_votes"},
                                "languages_translated": {
                                    "$addToSet": "$requested_language"
                                },
                            }
                        },
                    ]
                    cur = translations_collection.aggregate(pipeline)
                    translations = await cur.to_list(length=len(object_ids))

                    # Map translations by object_id
                    translation_map = {t["_id"]: t for t in translations}

                    # Merge with matching_objects list to form top_objects
                    top_objects = []
                    for obj in matching_objects:
                        oid = obj.get("_id")
                        # if isinstance(oid, str) and ObjectId.is_valid(oid):
                        #     oid = ObjectId(oid)
                        # if not isinstance(oid, ObjectId):
                        #     continue

                        trans = translation_map.get(oid, {})
                        top_objects.append({
                            "_id": oid,
                            "total_net_votes": int(trans.get("total_net_votes", 0)),
                            "languages_translated": trans.get("languages_translated", []),
                        })

                    # Sort by votes descending, then by _id
                    top_objects.sort(key=lambda x: (-x["total_net_votes"], str(x["_id"])))
                    top_objects = top_objects[: limit * 3]
                    print (f"⏰ Top objects after search aggregation: {len(top_objects)} \n {top_objects}")
       
       ############################
        else:
            pipeline = [
                {"$match": {"translation_status": "Approved"}},
                {
                    "$addFields": {
                        "net_votes": {
                            "$subtract": [
                                {"$ifNull": ["$up_votes", 0]},
                                {"$ifNull": ["$down_votes", 0]}
                            ]
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$object_id",
                        "total_net_votes": {"$sum": "$net_votes"},
                        "languages_translated": {"$addToSet": "$requested_language"}
                    }
                },
                {"$sort": {"total_net_votes": -1, "_id": 1}},
                {"$limit": limit * 3},
            ]
            cursor = translations_collection.aggregate(pipeline)
            top_objects = await cursor.to_list(length=limit * 3)
            print(f"⏰ Top objects without search aggregation: {len(top_objects)} \n {top_objects}")
    except Exception as e:
        logger.exception(f"Error aggregating top objects: {e}")
        return []

    if not top_objects:
        return []

    # Step 4: Compute max votes per language
    max_net_per_language = {}
    try:
        languages_in_result = set()
        for obj in top_objects:
            languages_in_result.update(obj.get("languages_translated", []))

        for lang in languages_in_result:
            agg = [
                {"$match": {"translation_status": "Approved", "requested_language": lang}},
                {
                    "$addFields": {
                        "net_votes": {
                            "$subtract": [
                                {"$ifNull": ["$up_votes", 0]},
                                {"$ifNull": ["$down_votes", 0]}
                            ]
                        }
                    }
                },
                {"$group": {"_id": "$object_id", "max_net_for_obj": {"$max": "$net_votes"}}},
                {"$group": {"_id": None, "max_net": {"$max": "$max_net_for_obj"}}}
            ]
            cur = translations_collection.aggregate(agg)
            res = await cur.to_list(length=1)
            max_net_per_language[lang] = int(res[0]["max_net"]) if res else 0
    except Exception as e:
        logger.error(f"Error computing max votes per language: {e}")

    # Step 5: Process objects concurrently
    async def _process_object(obj_data):
        try:
            object_obj_id = obj_data["_id"]
            if isinstance(object_obj_id, str):
                object_obj_id = ObjectId(object_obj_id)

            total_net_votes = int(obj_data.get("total_net_votes", 0))
            translated_languages = obj_data.get("languages_translated", [])
            untranslated_languages = [lang for lang in languages_allowed if lang not in translated_languages]
            print(f"\n🔴 Object {object_obj_id} \nTranslated: {translated_languages} \nUntranslated: {untranslated_languages}")
            if not untranslated_languages:
                return None

            obj_doc = await objects_collection.find_one({"_id": object_obj_id})
            if not obj_doc:
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

            # Popularity stars
            max_total_net = max(max_net_per_language.values() or [1])
            popularity_stars = min(5, max(0, round((total_net_votes / max_total_net) * 5)))

            total_vote_count_human = _format_votes_human(total_net_votes)
            file_info = create_return_file_info(obj_doc) if obj_doc else {}

            return {
                "poolImage": {
                    "image_hash": obj_doc.get("image_hash", ""),
                    "object_name_en": obj_doc.get("object_name_en", ""),
                    "image_base64": image_base64 or "",
                    "thumbnail_base64": (
                        thumbnail_b64.decode("utf-8") if isinstance(thumbnail_b64, bytes) else (thumbnail_b64 or "")
                    ),
                    "popularity_stars": popularity_stars,
                    "total_vote_count": total_vote_count_human,
                    "raw_net_votes": total_net_votes,
                },
                "permissions": user_permissions,
                "file_info": file_info,
                "translated_languages": translated_languages,
                "untranslated_languages": untranslated_languages,
            }

        except Exception as e:
            logger.exception(f"Error processing object {obj_data.get('_id')}: {e}")
            return None

    tasks = [_process_object(obj) for obj in top_objects]
    results = await asyncio.gather(*tasks)
    results = [r for r in results if r is not None]

    # Step 6: Sort and return deterministically
    try:
        results.sort(key=lambda r: (-r["poolImage"]["raw_net_votes"], r["poolImage"]["object_name_en"]))
        return results[:limit]
    except Exception as e:
        logger.error(f"Error sorting results: {e}")
        return results

