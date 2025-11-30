from typing import List

from db.connection import users_collection, roles_collection, permission_rules_collection, translations_collection, objects_collection
from utils.common import make_thumbnail_from_base64
from storage.imagestore import retrieve_image
from services.fileinfo import create_return_file_info

# async def get_recent_translations(userid: str):
#     """
#     Return top 5 active translations for a user, joined with their objects.
#     """

#     # 1. Extract roles assigned to this user
#     user_doc = await users_collection.find_one({"username": userid}, {"roles": 1})
#     if not user_doc or "roles" not in user_doc:
#         return []

#     roles = user_doc["roles"]

#     # 2. Get permissions from roles
#     role_docs = roles_collection.find({"_id": {"$in": roles}}, {"permissions": 1})
#     user_permissions: List[str] = []
#     async for role_doc in role_docs:
#         user_permissions.extend(role_doc.get("permissions", []))

#     # 3. For each permission, fetch its rules and collect "from" states
#     allowed_states: set[str] = set()
#     rules_cursor = permission_rules_collection.find(
#         {"_id": {"$in": user_permissions}, "transitionType": "StateChange"}
#     )

#     async for rule in rules_cursor:
#         transitions = rule.get("stateTransitions", {}).get("language", [])
#         for t in transitions:
#             if "from" in t:
#                 allowed_states.add(t["from"])
#             if "to" in t:
#                 allowed_states.add(t["to"])    

#     print(f"Allowed states for {userid}: {allowed_states}")

#     # # 4. Find top 3 active translations for this user, where status is in allowed states
#     # query = {
#     #     "$and": [
#     #         {"translation_status": {"$in": list(allowed_states)}},
#     #         {"$or": [{"metadata.created_by": userid}, {"audit_trail.user_id": userid}]},
#     #     ]
#     # }

#     # translations_cursor = translations_collection.find(query).sort("updated_at", -1).limit(3)
#     # translations = [doc async for doc in translations_cursor]

#     # 4. Aggregation pipeline for per-translation last_activity
#     print("\n Jsut before pipeline construction......... ")
#     pipeline = [
#         {
#             "$match": {
#                 "translation_status": {"$in": list(allowed_states)},
#                 "$or": [
#                     {"metadata.created_by": userid},
#                     {"audit_trail.user_id": userid}
#                 ]
#             }
#         },
#         {
#             "$addFields": {
#                 "created_ts": {
#                     "$cond": [
#                         {"$ifNull": ["$created_at", False]},
#                         {"$toDate": "$created_at"},
#                         None
#                     ]
#                 },
#                 "audit_ts_array": {
#                     "$map": {
#                         "input": {"$ifNull": ["$audit_trail", []]},
#                         "as": "a",
#                         "in": {
#                             "$cond": [
#                                 {"$ifNull": ["$$a.timestamp", False]},
#                                 {"$toDate": "$$a.timestamp"},
#                                 None
#                             ]
#                         }
#                     }
#                 }
#             }
#         },
#         {
#             "$addFields": {
#                 "last_audit_ts": {
#                     "$cond": [
#                         {"$gt": [{"$size": {"$ifNull": ["$audit_ts_array", []]}}, 0]},
#                         {"$max": "$audit_ts_array"},
#                         None
#                     ]
#                 }
#             }
#         },
#         {
#             "$addFields": {
#                 "last_activity": {
#                     "$cond": {
#                         "if": {
#                             "$and": [
#                                 {"$ifNull": ["$last_audit_ts", False]},
#                                 {"$ifNull": ["$created_ts", False]}
#                             ]
#                         },
#                         "then": {
#                             "$cond": [
#                                 {"$gt": ["$last_audit_ts", "$created_ts"]},
#                                 "$last_audit_ts",
#                                 "$created_ts"
#                             ]
#                         },
#                         "else": {"$ifNull": ["$last_audit_ts", "$created_ts"]}
#                     }
#                 }
#             }
#         },
#         {"$sort": {"last_activity": -1}},   # newest first
#         {"$limit": 5}                       # only top 5
#     ]

#     # translations = await translations_collection.aggregate(pipeline).to_list(length=5)
#     cursor = await translations_collection.aggregate(pipeline)
#     translations = await cursor.to_list(length=5)

#     print("\nThumbnail translations constructed.. ")
    
#     results = []

#     for t in translations:
#         obj_id = t.get("object_id")
#         if not obj_id:
#             continue
        
#         # 5. Fetch corresponding object
        
#         obj_doc = await objects_collection.find_one(
#             # {"_id": obj_id}, {"image_hash": 1, "image_base64.": 1, "image_name":1} 
#             {"_id": obj_id}, {"image_hash": 1, "image_store":1, "image_name":1, "file_info":1,"metadata":1} 
#         )
#         if not obj_doc:
#             continue
        
#         # Calculate image_base64 from the store now.
#         image_store = obj_doc.get("image_store", "")
#         image_base64 = await retrieve_image (image_store)
        

#         # thumbnail_b64 = make_thumbnail_from_base64(obj_doc.get("image_base64", ""))
#         thumbnail_b64 = make_thumbnail_from_base64(image_base64)

#         results.append({
#             "object": {
#                 "image_hash": obj_doc.get("image_hash"),
#                 "image_base64": image_base64 or "",
#                 "thumbnail": (
#                     thumbnail_b64.decode("utf-8") if isinstance(thumbnail_b64, bytes) 
#                     else (thumbnail_b64 or "")
#                 ),
#             },
#             "translation": {
#                 "translation_id": str(t["_id"]),
#                 "requested_language": t.get("requested_language"),
#                 "translation_status": t.get("translation_status"),
#             },
#             "permissions": list(user_permissions or []),
#             "file_info": create_return_file_info(obj_doc) or {},
#         })

#     print(f"\n Thumbnail Structure for frist object\nImage hash: {results[0]['object']['image_hash']}\nImage Base64: {results[0]['object']['image_base64'][:10]}...\nThumbnail: {results[0]['object']['thumbnail'][:10]}...\nTranslation ID: {results[0]['translation']['translation_id']}\nRequested Language: {results[0]['translation']['requested_language']}\nTranslation Status: {results[0]['translation']['translation_status']}\nPermissions: {results[0]['permissions']}\nFile Info: {results[0]['file_info']}\n") if results else print("No results found.")
#     return results

from typing import List
from fastapi import HTTPException
import traceback

async def get_recent_translations(userid: str):
    """
    Return top 5 active translations for a user, joined with their objects.
    """
    try:
        # 1. Extract roles assigned to this user
        user_doc = await users_collection.find_one({"username": userid}, {"roles": 1})
        if not user_doc or "roles" not in user_doc:
            print(f"No roles found for user: {userid}")
            return []

        roles = user_doc["roles"]

        # 2. Get permissions from roles
        try:
            role_docs = roles_collection.find({"_id": {"$in": roles}}, {"permissions": 1})
            user_permissions: List[str] = []
            async for role_doc in role_docs:
                user_permissions.extend(role_doc.get("permissions", []))
        except Exception as e:
            print(f"Error fetching role permissions: {e}")
            traceback.print_exc()
            user_permissions = []

        # 3. Collect allowed states from permission rules
        allowed_states: set[str] = set()
        try:
            rules_cursor = permission_rules_collection.find(
                {"_id": {"$in": user_permissions}, "transitionType": "StateChange"}
            )

            async for rule in rules_cursor:
                transitions = rule.get("stateTransitions", {}).get("language", [])
                for t in transitions:
                    if "from" in t:
                        allowed_states.add(t["from"])
                    if "to" in t:
                        allowed_states.add(t["to"])    

            print(f"Allowed states for {userid}: {allowed_states}")
        except Exception as e:
            print(f"Error building allowed_states: {e}")
            traceback.print_exc()

        # 4. Aggregation pipeline
        print("\nConstructing aggregation pipeline...")
        pipeline = [
            {
                "$match": {
                    "translation_status": {"$in": list(allowed_states)},
                    "$or": [
                        {"metadata.created_by": userid},
                        {"audit_trail.user_id": userid}
                    ]
                }
            },
            {
                "$addFields": {
                    "created_ts": {
                        "$cond": [
                            {"$ifNull": ["$created_at", False]},
                            {"$toDate": "$created_at"},
                            None
                        ]
                    },
                    "audit_ts_array": {
                        "$map": {
                            "input": {"$ifNull": ["$audit_trail", []]},
                            "as": "a",
                            "in": {
                                "$cond": [
                                    {"$ifNull": ["$$a.timestamp", False]},
                                    {"$toDate": "$$a.timestamp"},
                                    None
                                ]
                            }
                        }
                    }
                }
            },
            {
                "$addFields": {
                    "last_audit_ts": {
                        "$cond": [
                            {"$gt": [{"$size": {"$ifNull": ["$audit_ts_array", []]}}, 0]},
                            {"$max": "$audit_ts_array"},
                            None
                        ]
                    }
                }
            },
            {
                "$addFields": {
                    "last_activity": {
                        "$cond": {
                            "if": {
                                "$and": [
                                    {"$ifNull": ["$last_audit_ts", False]},
                                    {"$ifNull": ["$created_ts", False]}
                                ]
                            },
                            "then": {
                                "$cond": [
                                    {"$gt": ["$last_audit_ts", "$created_ts"]},
                                    "$last_audit_ts",
                                    "$created_ts"
                                ]
                            },
                            "else": {"$ifNull": ["$last_audit_ts", "$created_ts"]}
                        }
                    }
                }
            },
            {"$sort": {"last_activity": -1}},
            {"$limit": 5}
        ]

        # 🧩 FIXED: await both aggregate() and to_list()
        translations = []
        try:
            translations = translations_collection.aggregate(pipeline)
            translations = await translations.to_list(length=5)
            print(f"Fetched {len(translations)} recent translations.")
        except Exception as e:
            print(f"Error running aggregation: {e}")
            traceback.print_exc()
            return []

        results = []

        # 5. Fetch related object data
        for t in translations:
            try:
                obj_id = t.get("object_id")
                if not obj_id:
                    continue

                obj_doc = await objects_collection.find_one(
                    {"_id": obj_id},
                    {"image_hash": 1, "image_store": 1, "image_name": 1, "file_info": 1, "metadata": 1}
                )

                if not obj_doc:
                    continue

                image_store = obj_doc.get("image_store", "")
                image_base64 = await retrieve_image(image_store)
                thumbnail_b64 = make_thumbnail_from_base64(image_base64)

                results.append({
                    "object": {
                        "image_hash": obj_doc.get("image_hash"),
                        "image_base64": image_base64 or "",
                        "thumbnail": (
                            thumbnail_b64.decode("utf-8") if isinstance(thumbnail_b64, bytes)
                            else (thumbnail_b64 or "")
                        ),
                    },
                    "translation": {
                        "translation_id": str(t["_id"]),
                        "requested_language": t.get("requested_language"),
                        "translation_status": t.get("translation_status"),
                    },
                    "permissions": list(user_permissions or []),
                    "file_info": create_return_file_info(obj_doc) or {},
                })

            except Exception as e:
                print(f"Error processing translation {t.get('_id')}: {e}")
                traceback.print_exc()
                continue

        if results:
            first = results[0]
            print(
                f"\nThumbnail Structure for first object:\n"
                f"Image hash: {first['object']['image_hash']}\n"
                f"Image Base64: {first['object']['image_base64'][:10]}...\n"
                f"Thumbnail: {first['object']['thumbnail'][:10]}...\n"
                f"Translation ID: {first['translation']['translation_id']}\n"
                f"Requested Language: {first['translation']['requested_language']}\n"
                f"Translation Status: {first['translation']['translation_status']}\n"
                f"Permissions: {first['permissions']}\n"
                f"File Info: {first['file_info']}\n"
            )
        else:
            print("No results found.")

        return results

    except Exception as e:
        print(f"Unhandled error in get_recent_translations: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
