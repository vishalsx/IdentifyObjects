from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId
from datetime import datetime

from models.books import Book
from db.connection import books_collection
from utils.common import get_next_sequence
from services.userauth import get_current_user

router = APIRouter(prefix="/curriculum/books", tags=["Books"])


def convert_objectid_to_str(data):
    """Recursively convert ObjectIds to strings."""
    if isinstance(data, list):
        return [convert_objectid_to_str(i) for i in data]
    elif isinstance(data, dict):
        return {k: convert_objectid_to_str(v) for k, v in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    return data


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

    # ✅ Add count fields at book level
    book_data["chapter_count"] = len(chapters)
    book_data["page_count"] = total_pages
    book_data["image_count"] = total_images

    return book_data


@router.post("/create_book", response_model=Book)
async def create_or_update_book(
    book: Book,
    current_user: dict = Depends(get_current_user)
    ):
    """
    📘 Create or update a book in MongoDB.
    - If `_id` exists and matches → update existing book.
    - Otherwise → create a new one.
    - Automatically:
        - Resequences chapter/page/image numbers.
        - Populates chapter_count, page_count, and image_count.
    """
    try:
        book.updated_at = datetime.utcnow()
        book_data = book.model_dump(by_alias=True, exclude_none=True)
        print ("\nReceived book data for create/update:", book)
        # 🔄 STEP 1: Resequence
        book_data = resequence_book_structure(book_data)

        # 🔢 STEP 2: Calculate counts (safe defaults)
        chapters = book_data.get("chapters", [])
        #Use the following for a quick summary
        book_data["chapter_count"] = len(chapters)
        book_data["page_count"] = sum(len(ch.get("pages", [])) for ch in chapters)
        book_data["image_count"] = sum(
            len(pg.get("images", []))
            for ch in chapters
            for pg in ch.get("pages", [])
        )

        # -----------------------
        # ✅ UPDATE EXISTING BOOK
        # -----------------------
        if "_id" in book_data and book_data["_id"]:
            book_id = ObjectId(book_data["_id"])
            existing = await books_collection.find_one({"_id": book_id})

            if existing:
                result = await books_collection.update_one(
                    {"_id": book_id},
                    {"$set": book_data}
                )

                print(
                    "✅ Updated book:" if result.modified_count else "ℹ️ No changes for book:",
                    book.title,
                )

                updated_doc = await books_collection.find_one({"_id": book_id})
                safe_updated_doc = convert_objectid_to_str(updated_doc)
                return safe_updated_doc

        # ---------------------
        # 🆕 CREATE NEW BOOK
        # ---------------------
        book_data["created_at"] = datetime.utcnow()
        result = await books_collection.insert_one(book_data)
        book_data["_id"] = result.inserted_id

        # check the right count from a ever increasing sequence undera  book Id before returning it to frontend.
        book_id_sequence = str(book_data["_id"])
        book_data["chapter_count"] = await get_next_sequence(book_id_sequence + ".BOOK.CHAPTER")
        book_data["page_count"] = await get_next_sequence(book_id_sequence + ".BOOK.PAGE")
        book_data["image_count"] = await get_next_sequence(book_id_sequence + ".BOOK.IMAGE")
        

        safe_book_data = convert_objectid_to_str(book_data)
        print(f"📘 Created new book: {book.title}")

        return safe_book_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create/update book: {str(e)}")
