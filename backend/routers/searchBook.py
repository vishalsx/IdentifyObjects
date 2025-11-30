from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional
from bson import ObjectId
import re
from services.userauth import get_current_user
from db.connection import books_collection, objects_collection

router = APIRouter(prefix="/curriculum/books", tags=["Books"])


@router.get("/search")
async def search_books(
    search_text: str = Query(..., description="Text to search across title, author, subject, etc."),
    language: Optional[str] = Query(None, description="Optional language filter"),
    current_user: dict = Depends(get_current_user)
):
    """
    🔍 Search books by title, author, subject, grade, tags, or chapter name.
    Returns only top-level book data (no chapters/pages).
    """
    try:
        regex = re.compile(re.escape(search_text), re.IGNORECASE)
        query = {
            "$or": [
                {"title": regex},
                {"author": regex},
                {"subject": regex},
                {"grade_level": regex},
                {"tags": regex},
                {"chapters.chapter_name": regex},
            ]
        }
        if language:
            query["language"] = language

        projection = {
            "_id": 1,
            "title": 1,
            "language": 1,
            "author": 1,
            "subject": 1,
            "education_board": 1,
            "grade_level": 1,
            "chapter_count": 1, 
            "page_count": 1,
            "image_count": 1, 
            "tags": 1,
            "created_at": 1,
            "updated_at": 1,
        }

        cursor = books_collection.find(query, projection)
        results = await cursor.to_list(length=50)

        for b in results:
            b["_id"] = str(b["_id"])
        # print("\nResults:", results)
        return results

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error searching books: {str(e)}")


@router.get("/{book_id}/book")
async def get_book_data(
    book_id: str,
    current_user: dict = Depends(get_current_user)
    ):

    # Get specific book details. returen the entire book structure.

    try:
        book = await books_collection.find_one({"_id": ObjectId(book_id)})
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")
        else:
            book["_id"] = str(book["_id"])
            # print (f"\nBook id: {book_id}, \nbook data: {book}")
            return book
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching chapters: {str(e)}")


#Get chapters list for a book
@router.get("/{book_id}/chapters")
async def get_book_chapters(
    book_id: str,
    current_user: dict = Depends(get_current_user)
    ):
    """
    📚 Get list of all chapters in a given book.
    Returns: chapter_number, chapter_name, and description.
    """
    try:
        book = await books_collection.find_one(
            {"_id": ObjectId(book_id)},
            # {
            #     "chapters.chapter_id": 1,
            #     "chapters.chapter_number": 1, 
            #     "chapters.chapter_name": 1, 
            #     "chapters.description": 1
            #  }
        )

        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        chapters = book.get("chapters", [])
        print (f"\nBook id: {book_id}, \nchapters: {chapters}")
        return {"book_id": book_id, "chapters": chapters}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching chapters: {str(e)}")

#Get pages list for a chapter by chapter number or name
@router.get("/{book_id}/chapters/{chapter_identifier}/pages")
async def get_chapter_pages(
    book_id: str, 
    chapter_identifier: str,
    current_user: dict = Depends(get_current_user)
    ):
    """
    📖 Get all pages in a chapter by chapter number or name.
    """
    try:
        book = await books_collection.find_one({"_id": ObjectId(book_id)}, {"chapters": 1})
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        chapters = book.get("chapters", [])
        chapter = next(
            (
                ch for ch in chapters
                if str(ch.get("chapter_id")) == chapter_identifier
                or ch.get("chapter_number") == chapter_identifier
            ),
            None
        )

        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        pages = [
            {
                "page_id": p.get("page_id"),
                "page_number": p.get("page_number"), 
                "title": p.get("title")
            }
            for p in chapter.get("pages", [])
        ]
        print(f"\nBook id: {book_id}, \nchapter identifier: {chapter_identifier}, \npages: {pages}")
        return {"book_id": book_id, "chapter_identifier": chapter_identifier, "pages": pages}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching pages: {str(e)}")


#Get images for a page by page number or title
from storage.imagestore import retrieve_image
from utils.common import make_thumbnail_from_base64
from starlette.concurrency import run_in_threadpool

@router.get("/{book_id}/chapters/{chapter_identifier}/pages/{page_identifier}/images")
async def get_page_images(
    book_id: str, 
    chapter_identifier: str, 
    page_identifier: str,
    image_id: Optional[str] = Query(None, description="Optional image ID filter"),
    current_user: dict = Depends(get_current_user)
    ):
    """
    🖼️ Get all images (with thumbnails and hashes) for a specific page.
    `page_identifier` can be a number or page title.
    """
    try:
        book = await books_collection.find_one({"_id": ObjectId(book_id)}, {"chapters": 1, "language": 1})
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        # Find chapter by number
        chapter = next(
            (ch for ch in book.get("chapters", []) if ch.get("chapter_id") == chapter_identifier),
            None
        )
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        # Find page by number or title
        pages = chapter.get("pages", [])
        page = next(
            (
                p for p in pages
                if p.get("page_id") == page_identifier
                or str(p.get("page_number")) == page_identifier
                or p.get("title") == page_identifier
            ),
            None
        )

        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        
        # ✅ Filter images: all or one specific
        page_images = page.get("images", [])
        if image_id:
            filtered_images = [img for img in page_images if img.get("image_id") == image_id]
            if not filtered_images:
                raise HTTPException(status_code=404, detail=f"Image with ID {image_id} not found")
        else:
            filtered_images = page_images


        images_info = []
        # for img in page.get("images", []):
        for img in filtered_images:
            image_hash = img.get("image_hash")

            if image_hash:
                # Fetch image_store dict from objects_collection using image_hash
                
                image_doc = await objects_collection.find_one({"image_hash": image_hash},{"_id": 1, "image_store": 1})
                if not image_doc or "image_store" not in image_doc:
                    raise HTTPException(status_code=404, detail="Image not found for given hash")
                image_store = image_doc["image_store"]
            
            
            base64_data = await retrieve_image(image_store)
            thumbnail_b64 = await run_in_threadpool(make_thumbnail_from_base64, base64_data, (128, 128))
            
            # # Retrieve the language + object's ID specific object_name form translations
            # language = book.get("language")
            # if language:
            #     translation_doc = await translations_collection.find_one({"object_id" : ObjectId(image_doc["_id"]), "requested_language": language})    
            #     object_name = translation_doc.get("object_name") if translation_doc else None
            
            print(f"\nImage Document: {image_doc}")

            images_info.append({
                "image_id": img.get("image_id"),
                "image_hash": image_hash,
                "thumbnail_base64": (
                    thumbnail_b64.decode("utf-8")
                    if isinstance(thumbnail_b64, bytes)
                    else thumbnail_b64
                ),
                "image_base64": base64_data,
                "position": img.get("position"),
                "object_name": img.get("object_name") #object_name is store as part of image data
                # "object_name": object_name # Include translated object name if available
            })
            # print(f"\nImage hash: {image_hash}, Object Name: {object_name}, position: {img.get('position')}")
         
        # ✅ Sort images by position before returning only if entire page images are returned else no need to sort.
        if not image_id: 
            images_info.sort(key=lambda x: (x.get("position") is None, x.get("position")))

        return {
            "book_id": book_id,
            "chapter_identifier": chapter_identifier,
            "page_identifier": page_identifier,
            "images": images_info
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching page images: {str(e)}")
