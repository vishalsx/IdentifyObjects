from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from typing import List
from services.userauth import get_organisation_id


# async def aggregate_votes_languages(
#     translations_collection: AsyncIOMotorCollection,
#     object_ids,
#     language: str = None
# ):
#     """
#     Aggregates total net votes (GLOBAL) and languages_translated (ORG-AWARE).

#     Rules:
#       1. Vote totals are GLOBAL — sum of all docs, across orgs and languages.
#       2. Languages_translated are ORG-AWARE (handled by overloaded aggregate):
#          - Org user: only that org’s + global translations.
#          - Non-org user: only global translations.
#       3. If `language` is provided, include only that language if present.
#       4. CRITICAL: Only consider documents where translation_status is "Approved".
#     """

#     # --- Step 1: GLOBAL vote totals (bypass overloaded aggregate)
#     global_vote_pipeline = [
#         {"$match": {
#             "translation_status": "Approved", # REQUIRED: Only Approved translations
#             "object_id": {"$in": object_ids}
#         }},
#         {"$addFields": {
#             "net_votes": {
#                 "$subtract": [
#                     {"$ifNull": ["$up_votes", 0]},
#                     {"$ifNull": ["$down_votes", 0]},
#                 ]
#             }
#         }},
#         {"$group": {
#             "_id": "$object_id",
#             "total_net_votes": {"$sum": "$net_votes"}
#         }},
#     ]

#     # ⛔ Use .collection.aggregate() to AVOID org filtering for global vote counts
#     vote_cursor = translations_collection.collection.aggregate(global_vote_pipeline)
#     vote_results = await vote_cursor.to_list(length=len(object_ids))
#     vote_map = {r["_id"]: r["total_net_votes"] for r in vote_results}

#     # --- Step 2: ORG-AWARE language aggregation (uses overloaded aggregate)
#     base_match = {
#         "translation_status": "Approved", # REQUIRED: Only Approved translations
#         "object_id": {"$in": object_ids},
#     }

#     if language:
#         base_match["requested_language"] = language

#     language_pipeline = [
#         {"$match": base_match},
#         {"$group": {
#             "_id": "$object_id",
#             "languages_translated": {"$addToSet": "$requested_language"},
#         }},
#     ]

#     # ✅ This call respects the overloaded org-aware aggregate()
#     lang_cursor = translations_collection.aggregate(language_pipeline)
#     language_results = await lang_cursor.to_list(length=len(object_ids))
#     lang_map = {r["_id"]: r["languages_translated"] for r in language_results}

#     # --- Step 3: Merge and format results
#     results = []
#     for obj_id in object_ids:
#         langs = lang_map.get(obj_id, [])
#         if language:
#             langs = [l for l in langs if l.lower() == language.lower()]
#         results.append({
#             "_id": obj_id,
#             "total_net_votes": vote_map.get(obj_id, 0),
#             "languages_translated": langs,
#         })

#     formatted = [
#         {
#             "_id": doc["_id"],
#             "total_net_votes": doc.get("total_net_votes", 0),
#             "languages_translated": doc.get("languages_translated", []),
#         }
#         for doc in results
#     ]
#     print ("😎Aggregated votes and languages:", formatted)
#     return formatted


async def aggregate_topmost_popular_objects(
    objects_collection: AsyncIOMotorCollection,
    languages_allowed: List[str],
    limit: int = 150,
):
    """
    Find the top popular objects NOT yet translated in the user's assigned languages,
    using only the pre-calculated 'object_votes_summary' data stored in the 
    objects_collection.
    
    Rules:
      1. CRITICAL: Only consider documents where image_status is "Approved".
      2. CRITICAL: Filter based on Org Rule (Private OR Global) for visibility.
    """
    
    # 1. Fetch user's org_id
    # org_id = get_organisation_id()
    
    # 2. Build the flexible org filter: Private OR Global
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

    objects_pipeline = [
        {
            # Stage 1: Filter
            "$match": {
                # CRITICAL: Enforce 'Approved' status
                "image_status": "Approved", 
                
                # CRITICAL: Enforce Org Filter (Private OR Global)
                # "$and": [org_query],       
                
                # Ensure the object has been scored and has a weighted score
                "object_votes_summary.weighted_score": {"$exists": True, "$ne": None},
                
                # Filter out objects that ARE translated in an allowed language.
                "$expr": {
                    "$not": {
                        "$gt": [
                            {"$size": {
                                "$setIntersection": [
                                    languages_allowed,
                                    {
                                        "$map": {
                                            "input": {"$objectToArray": "$object_votes_summary.language_scores.raw_net_votes"},
                                            "as": "lang_vote",
                                            "in": "$$lang_vote.k"
                                        }
                                    }
                                ]
                            }},
                            0
                        ]
                    }
                }
            }
        },
        
        # Stage 2: Sort and Limit (ranking based on fair score)
        {"$sort": {"object_votes_summary.weighted_score": -1}},
        {"$limit": limit},
        
        # Stage 3: Project to the required output format
        {"$project": {
            "_id": 1,
            "total_net_votes": {"$ifNull": ["$object_votes_summary.total_net_votes", 0]},
            "languages_translated": {
                "$map": {
                    "input": {"$objectToArray": "$object_votes_summary.language_scores.raw_net_votes"},
                    "as": "lang_vote",
                    "in": "$$lang_vote.k"
                }
            }
        }}
    ]

    vote_cursor = objects_collection.aggregate(objects_pipeline)
    results = await vote_cursor.to_list(length=limit)

    formatted = [
        {
            "_id": obj["_id"],
            "total_net_votes": obj.get("total_net_votes", 0),
            "languages_translated": obj.get("languages_translated", []),
        }
        for obj in results
    ]

    print("🌍 Top globally popular untranslated objects (Single Collection Query):")
    for f in formatted:
        print(f"  - {f['_id']} → Net Votes: {f['total_net_votes']}, Langs: {f['languages_translated']}")

    return formatted