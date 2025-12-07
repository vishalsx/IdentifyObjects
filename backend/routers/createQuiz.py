from fastapi import APIRouter, HTTPException, Form
# from pymongo import MongoClient
from bson.objectid import ObjectId
from db.connection import db


from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from services.bulkCheckwithAI import identify_and_translate
from db.connection import objects_collection
from storage.imagestore import retrieve_image


router = APIRouter(prefix="/translations", tags=["translations"])

# MongoDB client setup (replace with your connection details)


translations_collection = db["translations"]
objects_collection = db["objects"]

# ---------- identify_object (modified to always return a dict) ----------
async def identify_object(image_hash: str, language: str):
    # --- Case 1: image hash provided. hash takes priority ---
    if image_hash:
        print("\n❌❌❌Image hash provided:", image_hash)
        doc = await objects_collection.find_one({"image_hash": image_hash})
        if not doc:
            raise HTTPException(status_code=404, detail="No object found for given image_hash")

        image_filename = doc.get("image_name")
        try:
            image_base64 = await retrieve_image(doc.get("image_store"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to retrieve image from storage: {str(e)}")

        imagehash = image_hash
    else:
        return {"error": "image_hash must be provided."}
    # --- Call main pipeline ---
    result = {}
    result = await identify_and_translate(
    image_base64, imagehash, image_filename, language, additional_context=None
    )

    return result


# ---------- /update-quiz-qa route (modified to work with identify_object returning a dict) ----------
@router.post("/update-quiz-qa")
async def update_quiz_qa(
    translation_id_str: str = Form(None)
    ):
    translations_list = []

    if translation_id_str is not None: # do this for the selected Id only.
        document = await translations_collection.find_one(
            {"_id": ObjectId(translation_id_str)}
        )
        if document:
            translations_list = [document]
    else:    #do this for all of them. Dont prefer using this exhausts the LLM quota
        translations_cursor = translations_collection.find({
            "$or": [
                {"quiz_qa": {"$exists": False}},
                {"quiz_qa": {"$size": 0}},
                {"$expr": {"$lt": [{"$size": "$quiz_qa"}, 15]}}
            ]
        })
        if translations_cursor:
            translations_list = await translations_cursor.to_list(length=None)

    updated_count = 0

    for translation in translations_list:

        object_id = translation.get("object_id")
        requested_language = translation.get("requested_language")

        if not object_id or not requested_language:
            continue

        object_doc = await objects_collection.find_one({"_id": ObjectId(object_id)})
        if not object_doc:
            continue

        image_hash = object_doc.get("image_hash")
        if not image_hash:
            continue

        try:
            result = await identify_object(image_hash, requested_language)
   

        # At this point `result` is guaranteed to be a dict (from identify_object)
            if not isinstance(result, dict):
                print("identify_object returned non-dict despite protections. Skipping. Value:", repr(result))
                continue

            # If identify_object returned an error, skip and log
            if result.get("error"):
                print(f"Skipping update for image_hash {image_hash} due to error from identify_object: {result.get('error')}")
                continue

            quiz_qa = result.get("quiz_qa")
            if not quiz_qa or not isinstance(quiz_qa, list):
                # Either missing or malformed; skip this translation
                continue

            # We expect each quiz_qa item to be a dict with keys question/answer/difficulty_level
            if all(isinstance(item, dict) for item in quiz_qa):
                await translations_collection.update_one(
                    {"_id": translation["_id"]},
                    {"$set": {"quiz_qa": quiz_qa}}
                )
                updated_count += 1
            else:
                print(f"quiz_qa for image_hash {image_hash} is not the expected list[dict]. Skipping.")
       
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

    return JSONResponse(content=result)