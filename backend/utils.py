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


from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set")
os.environ["GOOGLE_API_KEY"] = API_KEY


app = FastAPI()

def get_gemini_model_vision():
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=1)

def identify_and_translate(image_bytes: bytes, target_language: str) -> dict:
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
        system_prompt = """
You are an expert visual AI assistant. You will be shown an image and provided with a target language.

Your task is to:
1. Identify the primary object in the image. Identify the most distinctive and clear object in front of the image in case of multiple objects within the image
2. Identify the exact name of the object if it is plants, flowers or any natural objects (For example "Palm", "Grand Canyon", "Rubber tree", "Cotton Plant", "Rose", "Lilly" etc..)
2. Generate a one-word object name in English (e.g., "apple", "bicycle"). Do not use classifiers (e.g., "a", "an", "the") or adjectives in the object name.
3. Translate the object name into the requested language.
4. Generate a short description in English (15–70 words) about the object’s origin, usage, or properties. End this with a local trivia or fact related to the region of the requested language.
5. Translate the description into the requested language.
6. Generate a hint in English that does not reveal the object’s name. The hint should be suitable for a children’s guessing game.
7. Translate the hint into the requested language.
8. Most important consideration when generatiing the descriptions or hints is to keep them child-safe and appropriate for all ages.

Important Guardrails — Do NOT do the following:
- If the image contains a real, recognizable person (e.g., a political leader or public figure), avoid descriptors in the object name or description. Keep hints neutral, subtle, and factual without using opinions or misleading references.
- If the image depicts a religious or divine figure (e.g., Gods, Goddesses, Idols), do not include adjectives or subjective commntary in the object name or description. Hints must remain respectful, culturally sensitive, and subtle.
- If the image is related to pornography, nudity, sexually explicit content, or substance use, reject inappropriate content silently and ensure output is child-safe and appropriate.
- Do not include any politically or religiously sensitive content in the object name, description, or hint.
- In case of any of these violations just populate "Inappropriate content..." text for all the output fields
Respond only with a valid JSON object. Do not include markdown, backticks, comments, or extra text.

JSON Format:
{
  "object_name_en": "<object name in English>",
  "object_name_translated": "<object name in the requested language>",
  "translated_to": "<language name>",
  "object_description_en": "<description sentence in English>",
  "object_description_translated": "<description sentence in the requested language>",
  "object_hint_en": "<a hint which can be used by kids to recognize the object>",
  "object_hint_translated": "<hint in the requested language which can be used by kids to recognize the object>"
}


"""
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
            return result
        except Exception as e:
            return {
                "error": "Failed to parse Gemini output as JSON",
                "raw_output": response.content,
                "exception": str(e)
            }
    except Exception as e:
        return {"error": f"Unexpected error in identify_and_translate: {str(e)}"}

@app.post("/identify-object/")
async def identify_object_route(
    image: UploadFile = File(...),
    language: str = Form(...)
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image.")

    image_bytes = await image.read()

    # Debug: Log file details
    print(f"Received file: {image.filename}, Content-Type: {image.content_type}, Size: {len(image_bytes)} bytes")

    try:
        result = identify_and_translate(image_bytes, language)
        if "error" in result:
            raise HTTPException(status_code=500, detail=f"Processing error: {result['error']}")
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
