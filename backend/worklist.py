# routers/worklist.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from pymongo import ReturnDocument
from bson import ObjectId
from db.db_crud import map_object_collection, map_translation_collection
from db.connection import db  # centralized DB connection
from fileinfo import process_file_info

from db.connection import (
    objects_collection,
    translations_collection,
    counters_collection,
    permission_rules_collection,
    roles_collection,
    users_collection,
    languages_collection,
    MONGODB_DBNAME,
)
router = APIRouter()


# # --- COLLECTIONS ---
# objects_collection = db["objects"]
# translations_collection = db["translations"]
# counters_collection = db["counters"]
# permission_rules_collection = db["permission_rules"]
# roles_collection = db["roles"]
# users_collection = db["users"]


async def get_workitem_for_user(current_user: dict, languages: List[str] = None):
    user_role = current_user.get("role")
    user_id = current_user.get("user_id")
    if not user_role or not user_id:
        raise HTTPException(status_code=400, detail="Invalid user information") 

    # Step 1: Find the role (async)
    role_doc = await roles_collection.find_one({"_id": user_role})
    if not role_doc:
        raise ValueError(f"Role '{user_role}' not found in roles collection")
    print("Role document:", role_doc)

    permissions = role_doc.get("permissions", [])
    if not permissions:
        return {"metadata": [], "language": []}

    metadata_states = set()
    language_states = set()

    # Step 2: Collect from-states
    for perm in permissions:
        rule = await permission_rules_collection.find_one(
            {"_id": perm, "transitionType": "StateChange"}
        )
        if not rule:
            continue

        state_transitions = rule.get("stateTransitions", {})
        for trans in state_transitions.get("metadata", []):
            if trans.get("from"):
                metadata_states.add(trans["from"])
        for trans in state_transitions.get("language", []):
            if trans.get("from"):
                language_states.add(trans["from"])

    print("\nFrom states for role:", user_role)
    print("Metadata States", metadata_states)
    print("Language States:", language_states)

    print("\nRequested languages:", languages)

    results = []

    # Step 3: Loop through requested languages
    for lang in languages:
        print(f"\nChecking for work items in language: {lang}")
        translation = await translations_collection.find_one_and_update(
            {
                "requested_language": lang,
                "translation_status": {"$in": list(language_states)},
                "$or": [
                    {"locked_by": None},
                    {"locked_by": user_id}
                ],
                "last_skipped": {"$ne": user_id}  # Added condition: last_skipped is not user_id
            },
            {"$set": {"locked_by": user_id}},
            return_document=ReturnDocument.AFTER,
        )

        print(f"Translation found for language '{lang}':", translation)

        if not translation:
            continue

        obj = await objects_collection.find_one({"_id": translation["object_id"]})
        if not obj:
            continue

        result = {"image_base64": obj.get("image_base64")}
        result.update(map_object_collection(obj))
        result.update(map_translation_collection(translation))
        result.update( await process_file_info(None,None,obj.get("image_name"),obj.get("_id")))

        results.append(result)
        print(f"✅ Workitem found for user {user_id} in language {lang}")

    if not results:
        return {"Message": "No work items available in the requested languages"}

    return results
