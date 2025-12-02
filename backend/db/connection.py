import os
import motor.motor_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorCollection
from typing import Dict, Any
from services.userauth import get_organisation_id
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DBNAME = os.getenv("MONGODB_DBNAME", "alphatubplay")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client[MONGODB_DBNAME]



# helper — recursively search for an 'org_id' occurrence
def _filter_contains_org_id(filter_obj: Any) -> bool:
    if filter_obj is None:
        return False
    if isinstance(filter_obj, dict):
        if "org_id" in filter_obj:
            return True
        for v in filter_obj.values():
            if _filter_contains_org_id(v):
                return True
    if isinstance(filter_obj, list):
        for item in filter_obj:
            if _filter_contains_org_id(item):
                return True
    return False

class OrgCollection:
    def __init__(self, collection: AsyncIOMotorCollection):
        self.collection = collection

    async def _inject_org_filter(self, filter: Dict[str, Any] | None) -> Dict[str, Any]:
        """
        - If user belongs to an org -> ensure filter restricts to that org_id (unless caller already filters org_id).
        - If user has no org -> ensure filter restricts to documents without org_id (org_id missing or null).
        """
        if filter is None:
            filter = {}

        org_id = get_organisation_id()  
        # If caller already has any org_id condition anywhere in the filter, keep it as-is
        if _filter_contains_org_id(filter):
            return filter

        if org_id:
            # Add top-level org_id equality
            # If filter is empty or simple, just add it
            if not filter:
                return {"org_id": org_id}
            # If there are other conditions, merge with $and to avoid accidental key collisions
            return {"$and": [filter, {"org_id": org_id}]}
        else:
            # Non-org user -> only show documents with no org_id or org_id == null
            no_org_condition = {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]}
            if not filter:
                return no_org_condition
            return {"$and": [filter, no_org_condition]}

    async def _inject_org_field(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """
        When inserting/upserting, add org_id only if the user belongs to an org.
        If user has no org_id we do NOT add org_id field.
        """
        org_id = get_organisation_id()
        if org_id:
            # don't overwrite if already provided
            if "org_id" not in document:
                document["org_id"] = org_id
        return document

    # find_one
    async def find_one(self, filter: Dict[str, Any] | None = None, *args, **kwargs):
        filter = await self._inject_org_filter(filter or {})
        return await self.collection.find_one(filter, *args, **kwargs)


    async def count_documents(self, filter: Dict[str, Any] | None = None, *args, **kwargs):
        filter = await self._inject_org_filter(filter or {})
        return await self.collection.count_documents(filter, *args, **kwargs)


    # # find (returns Motor cursor) - caller can await cursor.to_list(...)
    # async def find(self, filter: Dict[str, Any] | None = None, *args, **kwargs):
    #     filter = await self._inject_org_filter(filter or {})
    #     return self.collection.find(filter, *args, **kwargs)
    
    def find(self, filter=None, *args, **kwargs):
        """
        Organization-aware wrapper around collection.find().
        - If user has org_id => restrict to their org_id
        - Else => restrict to docs without org_id
        Returns a MotorCursor (not awaited).
        """
        org_id = get_organisation_id()  # your org fetch function

        filter = filter or {}

        if "org_id" not in filter:
            if org_id:
                filter["org_id"] = org_id
            else:
                filter["$or"] = [{"org_id": {"$exists": False}}, {"org_id": None}]
        return self.collection.find(filter, *args, **kwargs)




    async def insert_one(self, document: Dict[str, Any], *args, **kwargs):
        document = await self._inject_org_field(document)
        return await self.collection.insert_one(document, *args, **kwargs)

    async def update_one(self, filter: Dict[str, Any], update: Dict[str, Any], *args, **kwargs):
        filter = await self._inject_org_filter(filter or {})
        return await self.collection.update_one(filter, update, *args, **kwargs)

    async def delete_one(self, filter: Dict[str, Any], *args, **kwargs):
        filter = await self._inject_org_filter(filter or {})
        return await self.collection.delete_one(filter, *args, **kwargs)

    async def find_one_and_update(self, filter: Dict[str, Any], update: Dict[str, Any], *args, **kwargs):
        """
        Inject org filter into 'filter'. If upsert=True and user has org_id, ensure the upserted doc will
        include org_id by adding it to $set.
        """
        filter = await self._inject_org_filter(filter or {})

        # Check upsert param
        upsert = kwargs.get("upsert", False) or ("upsert" in kwargs and kwargs["upsert"])
        org_id = get_organisation_id()

        # Only inject org_id into $set if org_id exists for this user and update uses $set (typical case)
        # Do not blindly modify other operators; prefer adding to $set.
        if org_id and "$set" in update:
            if "org_id" not in update["$set"]:
                update = {**update, "$set": {**update["$set"], "org_id": org_id}}
        # If upsert True and update doesn't have $set, still ensure upsert contains org_id by wrapping:
        elif org_id and upsert and not _filter_contains_org_id(update):
            # best effort: put org_id into $setOnInsert
            if "$setOnInsert" in update:
                if "org_id" not in update["$setOnInsert"]:
                    update = {**update, "$setOnInsert": {**update["$setOnInsert"], "org_id": org_id}}
            else:
                update = {**update, "$setOnInsert": {"org_id": org_id}}

        return await self.collection.find_one_and_update(filter, update, *args, **kwargs)


    def aggregate(self, pipeline: list, *args, **kwargs):
        """
        Prepend organization-aware $match stage:
        - if user belongs to org => match org_id
        - else => match docs where org_id missing/null
        If caller already has a match on org_id anywhere, don't add anything.
        If pipeline starts with $vectorSearch, skip automatic match injection
        (must be first stage in MongoDB pipeline).
        """
        org_id = get_organisation_id()
        # Helper: check if a filter dict contains org_id
        def _filter_contains_org_id(f):
            if not isinstance(f, dict):
                return False
            return "org_id" in f or any(
                isinstance(v, dict) and _filter_contains_org_id(v) for v in f.values()
            )

        # Helper: check if pipeline already filters org_id
        def _pipeline_has_org_match(pipeline_list):
            for stage in pipeline_list:
                if isinstance(stage, dict) and "$match" in stage and _filter_contains_org_id(stage["$match"]):
                    return True
            return False

        # ✅ NEW: Skip injection if first stage is $vectorSearch
        is_vector_search_first = (
            len(pipeline) > 0 and isinstance(pipeline[0], dict) and "$vectorSearch" in pipeline[0]
        )

        # Only inject org filter if:
        # - $vectorSearch is NOT first stage
        # - org filter not already present
        if not is_vector_search_first and not _pipeline_has_org_match(pipeline):
            if org_id:
                org_match = {"$match": {"org_id": org_id}}
            else:
                org_match = {
                    "$match": {
                        "$or": [
                            {"org_id": {"$exists": False}},
                            {"org_id": None}
                        ]
                    }
                }
            pipeline = [org_match] + pipeline
        import json
        # Return Motor cursor — caller should handle .to_list() or iteration
        return self.collection.aggregate(pipeline, *args, **kwargs)


# ✅ Custom version for objects_collectio. Inherits from OrgCollection
class OrgCollectionWithFallback(OrgCollection):
    async def _inject_org_filter(self, filter: Dict[str, Any] | None) -> Dict[str, Any]:
        if filter is None:
            filter = {}

        org_id = get_organisation_id()

        if _filter_contains_org_id(filter):
            return filter

        if org_id:
            # include both org-specific and global (no org_id)
            combined_filter = {
            "$or": [
                {"org_id": org_id},
                {
                    "$and": [
                        {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]},
                        {"image_status": "Approved"}
                    ]
                }
            ]
            }
            return {"$and": [filter, combined_filter]} if filter else combined_filter
        else:
            # user without org → only global items
            no_org_condition = {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]}
            return {"$and": [filter, no_org_condition]} if filter else no_org_condition

    def find(self, filter=None, *args, **kwargs):
        """
        Overloaded find() for objects_collection.

        Behavior:
        - If user has org_id => include docs with same org_id OR no org_id.
        - If user has no org_id => include only docs without org_id.
        Returns a MotorCursor (not awaited).
        """
        org_id = get_organisation_id()
        filter = filter or {}

        # If caller has already applied org_id conditions manually, don’t override
        if "org_id" in str(filter):
            return self.collection.find(filter, *args, **kwargs)

        if org_id:
            org_fallback_filter = {
                "$and": [
                    filter,
                    {
                        "$or": [
                            {"org_id": org_id},
                            {"org_id": {"$exists": False}},
                            {"org_id": None}
                        ]
                    }
                ]
            }
            return self.collection.find(org_fallback_filter, *args, **kwargs)
        else:
            # No org user — restrict strictly to no-org data
            no_org_filter = {
                "$and": [
                    filter,
                    {
                        "$or": [
                            {"org_id": {"$exists": False}},
                            {"org_id": None}
                        ]
                    }
                ]
            }
            return self.collection.find(no_org_filter, *args, **kwargs)


    def aggregate(self, pipeline: list, *args, **kwargs):
        org_id = get_organisation_id()

        def _pipeline_has_org_match(pipeline_list):
            for stage in pipeline_list:
                if isinstance(stage, dict) and "$match" in stage and _filter_contains_org_id(stage["$match"]):
                    return True
            return False

        is_vector_search_first = (
            len(pipeline) > 0 and isinstance(pipeline[0], dict) and "$vectorSearch" in pipeline[0]
        )

        if not is_vector_search_first and not _pipeline_has_org_match(pipeline):
            if org_id:
                org_match = {
                    "$match": {
                        "$or": [
                            {"org_id": org_id},
                            {"org_id": {"$exists": False}},
                            {"org_id": None}
                        ]
                    }
                }
            else:
                org_match = {
                    "$match": {
                        "$or": [
                            {"org_id": {"$exists": False}},
                            {"org_id": None}
                        ]
                    }
                }
            pipeline = [org_match] + pipeline

        return self.collection.aggregate(pipeline, *args, **kwargs)



# class OrgCollectionWithAggregateFallback(OrgCollection):
#     """
#     Specialized OrgCollection for translations_collection that:
#     - Overloads aggregate() only.
#     - Org users see their own org's + global (no-org) docs.
#     - Non-org users see only no-org docs.
#     """

#     def aggregate(self, pipeline, *args, **kwargs):
#         """
#         Overloaded aggregate() for translations_collection.
#         Inserts an org-aware $match at the start of the pipeline.
#         """
#         org_id = get_organisation_id()
#         pipeline = list(pipeline or [])

#         # Build org-aware match condition
#         if org_id:
#             org_fallback_match = {
#                 "$or": [
#                     {"org_id": org_id},
#                     {"org_id": {"$exists": False}},
#                     {"org_id": None}
#                 ]
#             }
#         else:
#             org_fallback_match = {
#                 "$or": [
#                     {"org_id": {"$exists": False}},
#                     {"org_id": None}
#                 ]
#             }

#         # If pipeline already has a $match, merge it safely
#         if pipeline and "$match" in pipeline[0]:
#             existing_match = pipeline[0]["$match"]
#             pipeline[0]["$match"] = {
#                 "$and": [existing_match, org_fallback_match]
#             }
#         else:
#             pipeline.insert(0, {"$match": org_fallback_match})

#         return self.collection.aggregate(pipeline, *args, **kwargs)


# ✅ Use modified class only for objects_collection
books_collection = OrgCollection(db.books)
objects_collection = OrgCollectionWithFallback(db.objects) #Find objects with org_id or no org_id to make images shared acroos orgs
translations_collection = OrgCollection(db.translations)



# following collections dont need org_id isolation
counters_collection = db["counters"]
permission_rules_collection = db["permission_rules"]
roles_collection = db["roles"]
users_collection = db["users"]
languages_collection = db["languages"]
organisations_collection = db["organisations"]





# objects_collection = db["objects"]
# translations_collection = db["translations"]
# counters_collection = db["counters"]
# permission_rules_collection = db["permission_rules"]
# roles_collection = db["roles"]
# users_collection = db["users"]
# languages_collection = db["languages"]
# books_collection = db["books"]
# organisations_collection = db["organisations"]