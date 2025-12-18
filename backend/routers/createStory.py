from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime, timezone
from typing import List, Optional
import logging
import os
from tenacity import AsyncRetrying, stop_after_attempt, wait_fixed


from motor.motor_asyncio import AsyncIOMotorClient
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import SystemMessage, HumanMessage
from tenacity import retry, stop_after_attempt, wait_fixed

from utils.common import make_thumbnail_from_base64
from storage.imagestore import store_image, retrieve_image
from starlette.concurrency import run_in_threadpool
from db.connection import objects_collection, books_collection, languages_collection
from models.books import StoryResponse, PageStoryRequest
from services.userauth import get_current_user

router = APIRouter(prefix="/curriculum/story", tags=["Story Generation"])


# LLM Init - Check API key at module load
if not os.getenv("GOOGLE_API_KEY"):
    raise RuntimeError("Missing GOOGLE_API_KEY environment variable")

# LLM instance will be created per request to avoid event loop issues
_llm_instance = None

def get_llm():
    """Get or create LLM instance lazily within async context"""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=1)
    return _llm_instance

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("create_story")



@router.post("/create_story", response_model=StoryResponse)
async def generate_story_for_page(
    req: PageStoryRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    📖 Generate a story using Gemini based on page images and book context.
    """
    try:
        # 🟩 1. Fetch book document
        book = await books_collection.find_one({"_id": ObjectId(req.book_id)})
        if not book:
            raise HTTPException(status_code=404, detail="Book not found")

        language = book.get("language")
        subject = book.get("subject")
        board = book.get("education_board")
        grade = book.get("grade_level")
        tags = ", ".join(book.get("tags", []))

        # 🟩 2. Get language script
        lang_doc = await languages_collection.find_one({"language": language})
        script = lang_doc.get("script") if lang_doc else "plain text"

        # 🟩 3. Find target chapter and page
        chapter = next((ch for ch in book.get("chapters", []) if ch["chapter_id"] == req.chapter_id), None)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        page = next((pg for pg in chapter.get("pages", []) if pg["page_id"] == req.page_id), None)
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")

        images = page.get("images", [])
        if len(images) < 1:
            raise HTTPException(status_code=400, detail="Page must have at least 1 images")

        # 🟩 4. Retrieve image_base64 + thumbnails
        enriched_images = []
        for img in images:
            image_hash = img.get("image_hash")
            if not image_hash:
                continue

            image_doc = await objects_collection.find_one(
                {"image_hash": image_hash}, {"_id": 1, "object_name_en": 1, "image_store": 1}
            )
            if not image_doc or "image_store" not in image_doc:
                continue

            base64_data = await retrieve_image(image_doc["image_store"])
            thumbnail_b64 = await run_in_threadpool(make_thumbnail_from_base64, base64_data, (128, 128))

            object_name = (
                img.get("object_name")
                or image_doc.get("object_name_en")
                or "unknown"
            )

            enriched_images.append({
                "image_hash": image_hash,
                "image_base64": base64_data,
                "thumbnail_base64": thumbnail_b64,
                "object_name": object_name or "unknown"
            })

        object_names = [i["object_name"] for i in enriched_images if i.get("object_name")]
        objects_str = ", ".join(object_names)

        # 🟩 5. Build context-aware prompt with all variables substituted
        system_content = f"""
You are a creative and empathetic children's storyteller with deep knowledge of world cultures, literature, folklore, mythology, and educational storytelling techniques.

Your task is to write a short, engaging story for **young children of grade {grade}**, based on the following information:

**Book Context**
- Language: {language}
- Script: {script}
- Subject: {subject}
- Education Board: {board}
- Grade Level: {grade}
- Tags (themes): {tags}

**Page Context**
- The following objects appear in the pictures: {objects_str}

**Story Requirements**
1. The story should be written entirely in **{language}**, using the **{script}** script.
2. The story must be **imaginative, emotionally engaging, and suitable for children of grade {grade} school age**.
3. Seamlessly include all listed objects as part of the narrative in natural and meaningful ways — for example, as characters, settings, or story elements.
4. The story must have:
   - A clear **theme or adventure** and some suspense.
   - A simple **conflict and resolution**.
   - A **positive moral or life lesson** at the end (start the moral with "Moral:").
5. Use the **cultural, historical, literary, and mythological background** of the given language for references, metaphors, or character inspiration. For example:
   - If the language is **Hindi or Sanskrit** → draw from Indian folktales, Panchatantra, Jataka Tales, Ramayana, Mahabharata, or stories of Akbar and Birbal.
   - If the language is **French** → include influences of La Fontaine's fables, French countryside life, classic tales like "Le Petit Prince," or themes of kindness and curiosity.
   - If the language is **English** → use references inspired by fairy tales, moral stories, or simple countryside adventures.
   - If the language is **Arabic** → weave motifs from "One Thousand and One Nights," desert life, or Arab folklore.
   - If the language is **Japanese** → use lessons from nature, Shinto myths, or cultural respect for harmony and perseverance.
6. The tone should be **playful, kind, inclusive, and moral-driven**, but simple enough to be narrated aloud by a teacher or parent.
7. Avoid violence, fear, or sadness — focus on humor, friendship, learning, discovery, and empathy.
8. End the story with a single line starting with "Moral:" that clearly summarizes the life lesson.
9. If the story involves cultural elements, include a subtle reference to **local trivia, festivals, songs, or folktales** known in that region.
10. If any animals, foods, or places appear among the objects, relate them to that culture's familiar environment or folklore.
11. Story should contain approximately 10-15 words per object name used (e.g., if 5 objects, story ~75 words).
12. All the words wihch are used to create this story {objects_str} should be prefixed with "⭐️" and suffixed with "⭐️" wherever they appear in teh story
13. **Dont repeat the object names {objects_str} more than once in the story.

**Example Guidance**
- In Hindi, the story might sound like an old village folk tale from Panchatantra with rhythmic, easy narration.
- In French, it could feel like a poetic fable that celebrates imagination and curiosity.
- In Arabic, it might be a desert journey or a tale of wisdom.
- In Japanese, it could include gentle imagery from nature and a quiet emotional depth.

The story should be inspiring, vivid, and make children smile — while teaching them a small but meaningful lesson about life, kindness, curiosity, or honesty.
"""

        human_content = f"These are the objects: {objects_str}. Write the story and end with 'Moral:' line."
        if req.user_comments:
            human_content = f"User comments: {req.user_comments}\n{human_content}"

        # 🟩 6. Create messages directly without templates
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content)
        ]

        # 🟩 7. Get LLM instance and call Gemini with retry logic
        llm = get_llm()
        result_content = None
        try:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(3), wait=wait_fixed(2)):
                with attempt:
                    result = await llm.ainvoke(messages)
                    result_content = result.content if hasattr(result, 'content') else str(result)
        except Exception as e:
            logger.error(f"Gemini LLM call failed after retries: {e}", exc_info=True)
            result_content = "Once upon a time... Moral: Be kind."

        story_text = result_content.strip()
        moral = None
        if "Moral:" in story_text:
            parts = story_text.split("Moral:", 1)
            story_text, moral = parts[0].strip(), parts[1].strip()

        print(f"\n\n\nGenerated Story: {story_text}\nMoral: {moral}\n\n\n")
        
        # # 🟩 8. Save back to book document
        # await books_collection.update_one(
        #     {
        #         "_id": ObjectId(req.book_id),
        #         "chapters.chapter_id": req.chapter_id,
        #         "chapters.pages.page_id": req.page_id
        #     },
        #     {
        #         "$set": {
        #             "chapters.$[ch].pages.$[pg].generated_story": story_text,
        #             "chapters.$[ch].pages.$[pg].moral": moral,
        #             "chapters.$[ch].pages.$[pg].story_generated_at": datetime.utcnow()
        #         }
        #     },
        #     array_filters=[
        #         {"ch.chapter_id": req.chapter_id},
        #         {"pg.page_id": req.page_id}
        #     ]
        # )
        print(f"\n Return Response: {StoryResponse}\n\n")
        # 🟩 9. Return response
        story_response = StoryResponse(
            book_id=req.book_id,
            chapter_id=req.chapter_id,
            page_id=req.page_id,
            language=language,
            object_names=object_names,
            story=story_text,
            moral=moral,
            created_at= datetime.now(timezone.utc).isoformat()
        )

        print("StoryResponse JSON:", story_response.model_dump())  # Pretty readable in logs

        return story_response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in story generation")
        raise HTTPException(status_code=500, detail=f"Story generation failed: {str(e)}")