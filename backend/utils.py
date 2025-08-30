from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
import io
import base64
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage
import os
import json
import re
import csv
from datetime import datetime
from db_crud import get_existing_data_imagehash


from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set")
os.environ["GOOGLE_API_KEY"] = API_KEY
# OLD_SERVICE = os.getenv("OLD_SERVICE", "false").lower() == "true"

app = FastAPI()

# if (OLD_SERVICE):
#     SYSTEM_PROMPT = """
#     You are an expert visual AI assistant. You will be shown an image and provided with a target language.

#     Your task is to:
# 1. Identify the primary object in the image. Identify the most distinctive and clear object in front of the image in case of multiple objects within the image
# 2. Identify the exact name of the object if it is plant, flower or any natural objects (For example "Palm", "Grand Canyon", "Rubber tree", "Cotton Plant", "Rose", "Lilly" etc..)
# 2. Generate a one-word object name in English (e.g., "apple", "bicycle"). Do not use classifiers (e.g., "a", "an", "the") or adjectives in the object name.
# 3. Translate the object name into the requested language.
# 4. Generate a short description in English (15–70 words) about the object’s origin, usage, or properties. End this with a local trivia or fact related to the region of the requested language.
# 5. Translate the description into the requested language.
# 6. Generate a hint in English that does not reveal the object’s name. The hint should be suitable for a children’s guessing game.
# 7. Translate the hint into the requested language.
# 8. Most important consideration when generatiing the descriptions or hints is to keep them child-safe and appropriate for all ages.

# Important Guardrails — Do NOT do the following:
# - If the image contains a real, recognizable person (e.g., a political leader or public figure), avoid descriptors in the object name or description but do recognise the image with the right gender information. Keep hints neutral, subtle, and factual without using opinions or misleading references.
# - If the image depicts a religious or divine figure (e.g., Gods, Goddesses, Idols), do not include adjectives or subjective commntary in the object name or description. Hints must remain respectful, culturally sensitive, and subtle.
# - If the image is related to pornography, nudity, sexually explicit content, or substance use, reject inappropriate content silently and ensure output is child-safe and appropriate.
# - Do not include any politically or religiously sensitive content in the object name, description, or hint.
# - In case of any of these violations just populate "Inappropriate content..." text for all the output fields
# Respond only with a valid JSON object. Do not include markdown, backticks, comments, or extra text.

# JSON Format:
# {
#   "object_name_en": "<object name in English>",
#   "object_name_translated": "<object name in the requested language>",
#   "translated_to": "<language name>",
#   "object_description_en": "<description sentence in English>",
#   "object_description_translated": "<description sentence in the requested language>",
#   "object_hint_en": "<a hint which can be used by kids to recognize the object>",
#   "object_hint_translated": "<hint in the requested language which can be used by kids to recognize the object>"
#   "object_category": "<category of the object, e.g., 'plant', 'flower', 'animal', 'building', 'vehicle', 'food', 'clothing', 'tool', 'furniture'>"
# }


#     """
# else:
SYSTEM_PROMPT = """
    You are an expert visual AI assistant. You will be shown an image and provided with a target language.

    Your task is to:
1. Identify the primary object in the image. Identify the most distinctive and clear object in front of the image in case of multiple objects within the image
2. Identify the exact name of the object in English if it is plants, flowers or any natural or un-natural, man made objects (For example "Palm", "Grand Canyon", "Rubber tree", "Cotton Plant", "Rose", "Lilly", "table", "chair" etc..)
3. Generate a one-word object name in {target_language} (e.g. in Hindi its "सेब", in Spanish its "manzana"). Do not use classifiers (e.g., "a", "an", "the") or adjectives in the object name.
4. Generate a description (25-75 words) in {target_language} about the object’s origin, usage, its properties, its significance. End with a local trivia or fun fact related to the demographic region of the requested language {target_language}.
5. Generate a hint in {target_language} that does not reveal the real object’s name. The hint should be suitable for a guessing game and should use local triva, proverb, local sayings, etc. to make it an interesting riddle in the reqeusted language {target_language}.
7. Most important consideration when generatiing the descriptions or hints is to keep them child-safe and appropriate for all ages.
8. Try to identify as many tags in English languagew which can be associated with the identified object image (for e.g for an apple image, the tags could be "food", "fruit", "red", "healthy", "snack" etc..). Generate the tags as a list of words in English only.
9. Genarate a unique category of the object in English language. For example "plant", "flower", "animal", "building", "vehicle", "food", "clothing", "tool", "furniture" etc. If you cannot identify the category just put "other"

Important Guardrails — Do NOT do the following:
- If the image contains a real, recognizable person (e.g., a political leader or public figure etc.), only show positive details about the person, strictly no ngative details. Keep hints neutral, respectful, subtle, and factual without using opinions or misleading references.
- If the image depicts a religious or divine figures or places (e.g., Gods, Goddesses, Idols, temples, churches, Mazjids etc..), do not include adjectives or subjective commntary in the object name or description. Hints must remain respectful, culturally sensitive, and subtle.
- If the image is related to pornography, nudity, sexually explicit content, or substance use, reject inappropriate content silently and ensure output is child-safe and appropriate.
- Do not include any politically or religiously sensitive or controverial content in the object name, description, or hint.
- In case of any of these violations just populate "Inappropriate content." text for all the output fields

Respond only with a valid strict JSON format. Do not include markdown, backticks, comments, or extra text.

Output JSON Format:
{
  "object_name_en": "<object name in English>",
  "object_name_translated": "<object name in the {target_language}>",
  "translated_to": "<language name>",
  "object_description": "<description paragraph in the {target_language}>",
  "object_hint": "<hint in {target_language} which can be used to recognize the object>",
  "tags": ["plant", "flower", "animal", "building", "vehicle", "food", "clothing", "tool", "furniture',"<string>", "<string>", "..."]
  "object_category": "<a unique category which this object belongs to, e.g., 'plant', 'flower', 'animal', 'building', 'vehicle', 'food', 'clothing', 'tool', 'furniture', 'other'>"
  }

Input: 
{target_language} - The language in which the object name, description, and hint should be translated.

"""


def get_gemini_model_vision():
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=1)

async def identify_and_translate(imagehash: str, image_bytes: bytes, target_language: str) -> dict:
    try:
        # Validate image_bytes
        if not image_bytes:
            return {"error": "Empty image data received"}
        
        # Debug: Log the size of the image data
        print(f"Image bytes size: {len(image_bytes)} bytes")
        
        # Validate image format
        try:
            image = Image.open(io.BytesIO(image_bytes))
            # Ensure image is in RGB mode
            if image.mode != "RGB":
                image = image.convert("RGB")
            # Debug: Log image format and size
            # print(f"Image format: {image.format}, Size: {image.size}")
        except UnidentifiedImageError as e:
            return {"error": f"Failed to identify image: {str(e)}"}
        except Exception as e:
            return {"error": f"Failed to process image with PIL: {str(e)}"}

        # Convert image to base64 for Gemini
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        image_base64 = base64.b64encode(buffered.getvalue()).decode()

        # Define the prompt
        system_prompt = SYSTEM_PROMPT
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=[
                {"type": "text", "text": f"Language: {target_language}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
            ])
        ]

        model = get_gemini_model_vision()
        response = model.invoke(messages)
  
        # Attempt to parse JSON output
        try:
            raw_output = response.content.strip()
            cleaned_output = re.sub(r"^```(json)?|```$", "", raw_output.strip(), flags=re.MULTILINE).strip()
            result = json.loads(cleaned_output)

     
            #Update the result field with additional data if image has matached
            try:    # added only prevent blocking of return if DB fails.
                existing_result = await get_existing_data_imagehash(imagehash, target_language)
                print(f"Existing result: ", existing_result)
                if "error" in existing_result:
                    pass
                else:
                    result.update(existing_result)
                    print (f"Record existing, Merged result: {result}")
                    return result
            except Exception as e:
                print(f"Error fetching existing data: {str(e)}")
            return result    
        except Exception as e:
            print(f"exception in JSON paarsing: {str(e)}\nResult:", result)
            return {
                "error": "Failed to parse Gemini output as JSON",
                "raw_output": response.content,
                "exception": str(e)
            }
    except Exception as e:
        print(f"exception in identify_and_tranalate funciton JSON: {str(e)}\nResult:", result)
        return {"error": f"Unexpected error in identify_and_translate: {str(e)}"}

