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
    return ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.8)

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
You are an expert visual AI assistant. You will be shown an image and given a target language.

Your task is to:
1. Identify the primary object in the image. 
2. Generate a short object name in English (e.g., "apple", "bicycle"). Do not use any classifiers or adjectives, and just identify the name of the object.
3. Translate the object name into the requested language.
4. Generate a short description in English providing a 15-70 words description about the object's origin, properties, etc. (e.g., "This is a red apple, grown in Shimla in India. It is considered very healthy if eaten daily..."). Do not exceed the description beyond 70 words.
5. Translate the short description into the requested language.
6. Generate a hint text in English without revealing the real name of object. This is required for a kids' game.
7. Translate the hint into the requested language also.

Respond with only a valid JSON object, without any markdown syntax, backticks, comments, or extra explanation.

Here is the required format:

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
