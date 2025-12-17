from fastapi import APIRouter, HTTPException, Query, Depends
from bson import ObjectId
from datetime import datetime, timezone

from models.books import Book
from db.connection import books_collection
from utils.common import get_next_sequence
from services.userauth import get_current_user, get_current_user_id, get_organisation_id
from typing import List, Optional
router = APIRouter(prefix="/curriculum/books", tags=["Books"])


def convert_objectid_to_str(data):
    """Recursively convert ObjectIds to strings."""
    if isinstance(data, list):
        return [convert_objectid_to_str(i) for i in data]
    elif isinstance(data, dict):
        return {k: convert_objectid_to_str(v) for k, v in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    elif isinstance(data, datetime):
        return data.isoformat()
    return data

def get_max_sequences(book: dict) -> dict:
    """
    Extracts the max chapter_seq, page_seq, and image_seq from a Book object
    (either dict or Pydantic model) and returns the next available ones.
    """
    # Handle both Pydantic and dict formats
    book_id = str(book["_id"]) if isinstance(book, dict) else str(book.id)

    # Extract nested structures safely
    chapters = book.get("chapters", []) if isinstance(book, dict) else (book.chapters or [])

    max_chapter_seq = 0
    max_page_seq = 0
    max_image_seq = 0

    for chapter in chapters:
        chapter_id = chapter.get("chapter_id") if isinstance(chapter, dict) else chapter.chapter_id
        if chapter_id:
            parts = chapter_id.split("-")
            if len(parts) >= 2 and parts[0] == book_id:
                try:
                    chapter_seq = int(parts[1])
                    max_chapter_seq = max(max_chapter_seq, chapter_seq)
                except ValueError:
                    pass

        pages = chapter.get("pages", []) if isinstance(chapter, dict) else (chapter.pages or [])
        for page in pages:
            page_id = page.get("page_id") if isinstance(page, dict) else page.page_id
            if page_id:
                parts = page_id.split("-")
                if len(parts) >= 3 and parts[0] == book_id:
                    try:
                        page_seq = int(parts[2])
                        max_page_seq = max(max_page_seq, page_seq)
                    except ValueError:
                        pass

            images = page.get("images", []) if isinstance(page, dict) else (page.images or [])
            for image in images:
                image_id = image.get("image_id") if isinstance(image, dict) else image.image_id
                if image_id:
                    parts = image_id.split("-")
                    if len(parts) == 4 and parts[0] == book_id:
                        try:
                            image_seq = int(parts[3])
                            max_image_seq = max(max_image_seq, image_seq)
                        except ValueError:
                            pass

    return {
        "next_chapter_seq": max_chapter_seq,
        "next_page_seq": max_page_seq,
        "next_image_seq": max_image_seq,
    }


def resequence_book_structure(book_data: dict) -> dict:
    """
    Re-sequence chapter numbers, page numbers, and image positions
    to maintain correct ordering before saving.
    Also updates chapter_count, page_count, and image_count totals.
    """
    chapters = book_data.get("chapters") or []

    total_pages = 0
    total_images = 0

    for i, chapter in enumerate(chapters, start=1):
        chapter["chapter_number"] = i  # 🔢 Sequential numbering of chapters

        pages = chapter.get("pages") or []
        for j, page in enumerate(pages, start=1):
            page["page_number"] = j  # 🔢 Sequential numbering of pages
            total_pages += 1

            images = page.get("images") or []
            # Sort images by existing position if any, else preserve order
            images_sorted = sorted(images, key=lambda x: x.get("position") or 999999)
            for k, image in enumerate(images_sorted, start=1):
                image["position"] = k  # 🔢 Re-sequence image positions
            page["images"] = images_sorted

            total_images += len(images_sorted)

        # Sort pages by updated page_number
        chapter["pages"] = sorted(pages, key=lambda p: p.get("page_number") or 0)

    # Sort chapters by updated chapter_number
    book_data["chapters"] = sorted(chapters, key=lambda c: c.get("chapter_number") or 0)

    return book_data


@router.post("/create_book", response_model=Book)
async def create_or_update_book(
    book: Book,
    current_user: dict = Depends(get_current_user)
    ):

    try:
        book.updated_at = datetime.now(timezone.utc)
        book_data = book.model_dump(by_alias=True, exclude_none=True)
        
        user_id = get_current_user_id()
        org_id = get_organisation_id()
        
        if org_id is None:
            org_check = False
        else:
            org_check = True

       
        # 🔄 STEP 1: Resequence
        book_data = resequence_book_structure(book_data)
        
        # STEP 2: Get accurate counts from IDs
        max_counters = get_max_sequences(book_data)

        book_data["chapter_count"] = max_counters["next_chapter_seq"]
        book_data["page_count"] = max_counters["next_page_seq"]
        book_data["image_count"] = max_counters["next_image_seq"]
        
        
        # # 🔢 STEP 2: Calculate counts (safe defaults)
        # chapters = book_data.get("chapters", [])
        
        # #Use the following for a quick summary
        # book_data["chapter_count"] = len(chapters)
        # book_data["page_count"] = sum(len(ch.get("pages", [])) for ch in chapters)
        # book_data["image_count"] = sum(
        #     len(pg.get("images", []))
        #     for ch in chapters
        #     for pg in ch.get("pages", [])
        # )

        # -----------------------
        # ✅ UPDATE EXISTING BOOK
        # -----------------------
        print (f"\n📗 Attempting to create/update book..with book id {book_data}.")
        try:
            if "_id" in book_data and book_data["_id"]: 
                book_id = ObjectId(book_data["_id"])
                existing = await books_collection.find_one({"_id": book_id})
                if existing and existing.get("created_by") == user_id:
                    existing_org_id = existing.get("org_id")
                    if org_check:
                        if existing_org_id != org_id:
                            raise HTTPException(status_code=403, detail="You do not have permission to update this book.")
                    
                    result = await books_collection.update_one(
                    {"_id": book_id},
                    {"$set": book_data}
                    )
                    print(f"\nUpdated sequence counts: Chapter {book_data['chapter_count']},\nPage Count: {book_data['page_count']},\nImage Count: {book_data['image_count']}\n")
                    updated_doc = await books_collection.find_one({"_id": book_id})
                    safe_updated_doc = convert_objectid_to_str(updated_doc)
                    return safe_updated_doc
                else:
                    # raise HTTPException(status_code=403, detail="You do not have permission to update this book.")
                    pass # should be created as new book
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to update book: {str(e)}")
        # ---------------------
        # 🆕 CREATE NEW BOOK
        # ---------------------
        try:
            book_data["book_status"] = "Draft" 
            if user_id:
                book_data["created_by"] = user_id
            else:
                book_data["created_by"] = "anonymous"
            
            book_data["created_at"] =  datetime.now(timezone.utc)
            
            if org_id:    
                book_data["org_id"] = org_id

            result = await books_collection.insert_one(book_data)
            book_data["_id"] = result.inserted_id

            
            safe_book_data = convert_objectid_to_str(book_data)
            print(f"📘 Created new book:{book_data['_id']}")

            return safe_book_data
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create book: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create/update book: {str(e)}")

# @router.post("/create_chapter")
# async def create_book_chapter(
#     book_id: str, 
#     chaptter_details: dict,
#     current_user: dict = Depends(get_current_user)
#     ):
#     pass

# @router.post("/create_page")
# async def create_book_chapter(
#     book_id: str,
#     chapter_identified: str,
#     page_details: dict,
#     current_user: dict = Depends(get_current_user)
#     ):
#     pass

# @router.post("/create_image")
# async def create_book_image(
#     book_id: str, 
#     chaptter_identifier: dict,
#     page_identifier: str,
#     image_details: dict,
#     current_user: dict = Depends(get_current_user)
#     ):
#     pass

def build_set_fields(prefix: str, payload: dict, exclude_keys: set = None) -> dict:
    """
    Build a flat dict of "$set" target paths for provided keys in payload.
    prefix: e.g. "chapters.$[ch]" or "chapters.$[ch].pages.$[pg]"
    exclude_keys: keys we should not include in $set (like 'pages' or 'images')
    """
    exclude_keys = exclude_keys or set()
    set_fields = {}
    for k, v in payload.items():
        if k in exclude_keys:
            continue
        set_fields[f"{prefix}.{k}"] = v
    return set_fields


# ✅ CHAPTER CREATION
@router.post("/create_chapter")
async def create_book_chapter(
    book_id: str,
    chapter_details: dict,
    current_user: dict = Depends(get_current_user)
):
    book = await books_collection.find_one({"_id": ObjectId(book_id)})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    provided_chapter_id = chapter_details.get("chapter_id")

    # ---------- UPDATE existing chapter (PATCH semantics) ----------
    if provided_chapter_id:
        # If pages provided, client intends to set/replace pages
        pages_provided = "pages" in chapter_details

        if pages_provided:
            # Replace pages explicitly and other shallow fields
            update_doc = {"$set": {}}
            # set top-level non-pages fields
            shallow = {k: v for k, v in chapter_details.items() if k != "pages"}
            if shallow:
                update_doc["$set"].update(build_set_fields("chapters.$[ch]", shallow))
            # explicitly set pages array
            update_doc["$set"]["chapters.$[ch].pages"] = chapter_details["pages"]
            # optionally update chapter_number if provided
            if "chapter_number" in chapter_details:
                update_doc["$set"]["chapters.$[ch].chapter_number"] = chapter_details["chapter_number"]

            result = await books_collection.update_one(
                {"_id": ObjectId(book_id)},
                update_doc,
                array_filters=[{"ch.chapter_id": provided_chapter_id}]
            )
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Chapter not found")
            return {"chapter_id": provided_chapter_id, "status": "updated"}
        else:
            # No pages provided → patch only shallow fields
            set_fields = build_set_fields("chapters.$[ch]", chapter_details, exclude_keys={"pages"})
            if not set_fields:
                return {"status": "no_changes"}
            result = await books_collection.update_one(
                {"_id": ObjectId(book_id)},
                {"$set": set_fields},
                array_filters=[{"ch.chapter_id": provided_chapter_id}]
            )
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="Chapter not found")
            return {"chapter_id": provided_chapter_id, "status": "patched"}

    # ---------- CREATE new chapter ----------
    chapter_seq = await get_next_sequence(f"{book_id}.BOOK.CHAPTER")
    new_chapter_id = f"{book_id}-{str(chapter_seq).zfill(3)}"
    new_chapter = {
        "chapter_id": new_chapter_id,
        "chapter_number": chapter_details.get("chapter_number", chapter_seq),
        "chapter_name": chapter_details.get("chapter_name", ""),
        "pages": chapter_details.get("pages", []),
        "description": chapter_details.get("description", "")
    }

    await books_collection.update_one(
        {"_id": ObjectId(book_id)},
        {
            "$push": {"chapters": new_chapter},
            "$set": {"chapter_count": chapter_seq, "updated_at": datetime.now(timezone.utc)}
        }
    )

    return {"chapter_id": new_chapter_id, "status": "created"}

# # ✅ PAGE CREATION
# @router.post("/create_page")
# async def create_book_page(
#     book_id: str,
#     chapter_identifier: str,
#     page_details: dict,
#     current_user: dict = Depends(get_current_user)
# ):
#     book = await db.books.find_one({"_id": ObjectId(book_id)})
#     if not book:
#         raise HTTPException(status_code=404, detail="Book not found")

#     # Update existing page
#     existing_id = page_details.get("page_id")
#     if existing_id:
#         await db.books.update_one(
#             {"_id": ObjectId(book_id), "chapters.pages.page_id": existing_id},
#             {"$set": {"chapters.$[c].pages.$[p]": page_details}},
#             array_filters=[
#                 {"c.chapter_id": chapter_identifier},
#                 {"p.page_id": existing_id}
#             ]
#         )
#         return {"message": "Page updated", "page_id": existing_id}

#     # Create new page
#     page_seq = await get_next_sequence(f"{book_id}.BOOK.PAGE")
#     chapter_seq = chapter_identifier.split("-")[-1]
#     page_id = f"{book_id}-{chapter_seq}-{str(page_seq).zfill(4)}"

#     new_page = {
#         "page_id": page_id,
#         "page_number": page_seq,
#         "title": page_details.get("title", f"Page {page_seq}"),
#         "images": []
#     }

#     await db.books.update_one(
#         {"_id": ObjectId(book_id), "chapters.chapter_id": chapter_identifier},
#         {"$push": {"chapters.$.pages": new_page},
#          "$set": {"page_count": page_seq}}
#     )

#     return {"message": "Page created", "page_id": page_id}


# # ✅ IMAGE CREATION
# @router.post("/create_image")
# async def create_book_image(
#     book_id: str,
#     chapter_identifier: str,
#     page_identifier: str,
#     image_details: dict,
#     current_user: dict = Depends(get_current_user)
# ):
#     book = await db.books.find_one({"_id": ObjectId(book_id)})
#     if not book:
#         raise HTTPException(status_code=404, detail="Book not found")

#     # Update existing image
#     existing_id = image_details.get("image_id")
#     if existing_id:
#         await db.books.update_one(
#             {"_id": ObjectId(book_id), "chapters.pages.images.image_id": existing_id},
#             {"$set": {"chapters.$[c].pages.$[p].images.$[i]": image_details}},
#             array_filters=[
#                 {"c.chapter_id": chapter_identifier},
#                 {"p.page_id": page_identifier},
#                 {"i.image_id": existing_id}
#             ]
#         )
#         return {"message": "Image updated", "image_id": existing_id}

#     # Create new image
#     image_seq = await get_next_sequence(f"{book_id}.BOOK.IMAGE")
#     chapter_seq = chapter_identifier.split("-")[-1]
#     page_seq = page_identifier.split("-")[-1]
#     image_id = f"{book_id}-{chapter_seq}-{page_seq}-{str(image_seq).zfill(5)}"

#     new_image = {
#         "image_id": image_id,
#         "image_hash": image_details.get("image_hash"),
#         "position": image_details.get("position"),
#         "object_name": image_details.get("object_name")
#     }

#     await db.books.update_one(
#         {"_id": ObjectId(book_id), "chapters.chapter_id": chapter_identifier, "chapters.pages.page_id": page_identifier},
#         {"$push": {"chapters.$[c].pages.$[p].images": new_image},
#          "$set": {"image_count": image_seq}},
#         array_filters=[
#             {"c.chapter_id": chapter_identifier},
#             {"p.page_id": page_identifier}
#         ]
#     )

#     return {"message": "Image created", "image_id": image_id}
