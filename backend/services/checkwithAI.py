
from PIL import Image, UnidentifiedImageError
import io
import base64
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage

import json
import re
from services.db_crud import get_existing_data_imagehash, get_language_details
from services.fileinfo import process_file_info


from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set")
os.environ["GOOGLE_API_KEY"] = API_KEY


SYSTEM_PROMPT = """
You are a visual AI assistant expert in identifying object details in different languages.

You will always receive input in the following format inside the HumanMessage:
{
  "target_language": "<language name>",
  "language_script": "<writing script>"
}

You will also receive an image encoded as base64.

---

Your core tasks are:

1. Identify the **primary object** in the image. If multiple objects exist, choose the most distinctive and clear object in the foreground.  

2. Identify the **exact name of the object in English** if it is a plant, flower, natural or man-made object (e.g., "Palm", "Grand Canyon", "Rose", "Table", "Chair").  

3. Identify the **object name in the target language** (e.g., in Hindi "सेब", in Spanish "manzana", in Kokborok "Seb").  
   - Do not use classifiers ("a", "an", "the") or adjectives.  
   - Always write it in the `language_script` provided.  

4. Generate a **description** (25–75 words) in the `target_language`.  
   - Cover origin, usage, properties, significance.  
   - End with a trivia or fun fact from the demographic region of that language.  
   - Always write it in the `language_script`.  

5. Generate a **hint** in the `target_language` for a guessing game.  
   - Do not reveal the object name.  
   - Use riddles, proverbs, or cultural sayings.  
   - Write it in the `language_script`.  

6. Generate a **short hint** (10–15 words) in the `target_language`.  
   - Same rules as the longer hint.  
   - Write it in the `language_script`.  

7. Generate **tags in English** (list of words). Example for apple: `["food", "fruit", "red", "healthy", "snack"]`.  

8. Generate a **category in English** (e.g., "plant", "flower", "animal", "building", "vehicle", "food", "clothing", "tool", "furniture", "other").  

9. Generate a **field of study in English** (e.g., "botany", "zoology", "architecture", "culinary arts", "engineering", "art history").  

10. Generate the **age appropriateness in English**: "all ages", "kids", "teens", "adults", "seniors".  

---

Important Guardrails:
- If the image contains a known celebrity, political leader, or divine/religious figure → only provide **neutral, respectful details**. No opinions or negative commentary.  
- If the image depicts inappropriate/explicit content → respond with `"Inappropriate content."` for all fields.  
- Never include politically or religiously sensitive or controversial content.  
- If you find any vilations in the guardrail rule, raise this error in the error JSON tag
---

Output strictly in **valid JSON only** (no markdown, no comments).  

Output JSON format:
{
  "object_name_en": "<object name in English>",
  "object_name": "<object name in target_language using language_script>",
  "translated_to": "<target_language>",
  "object_description": "<description in target_language using language_script>",
  "object_hint": "<hint in target_language using language_script>",
  "object_short_hint": "<short hint in target_language using language_script>",
  "tags": ["<tag1>", "<tag2>", "..."],
  "object_category": "<category in English>",
  "field_of_study": "<field of study in English>",
  "age_appropriate": "<all ages | kids | teens | adults | seniors>"
  "error:" "<Inappropriate content detected. Can't be processed..>"
}
"""

def get_gemini_model_vision():
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=1)


async def identify_and_translate(image_base64: str, imagehash:str, image_filename: str,target_language: str) -> dict:
    try:
        result = {}
        image_found_in_database = True
        existing_result = None
        object_found = False
        translation_found = False
        language_script = ""
        language_details = await get_language_details(target_language) #get language details.
        if not language_details or "error" in language_details:
            pass #use default scripts if langage not defined in languages collection
        else:
            language_script = language_details.get("script", "")
            print (f"\nLanguage Script found: {language_script}")

        try:
            existing_result = await get_existing_data_imagehash(imagehash, target_language)
            # print("Existing result returned:", existing_result)

            if existing_result:
                object_found = existing_result.get("flag_object", False)
                translation_found = existing_result.get("flag_translation", False)

                print("Object Name in En:",existing_result.get("object_name_en"))
                if object_found is True and translation_found is True:
                    return existing_result  # ✅ return DB copy immediately as both the details are found
                else:
                    print("\nObject Status:", object_found)
                    print("\nTranslation result:", translation_found)
                    image_found_in_database = False
            else:
                print("\nNothing found in DB in hash search: ", existing_result.get("message") )
                image_found_in_database = False
                existing_result = None


        except Exception as e:
            print(f"Error fetching existing data: {str(e)}")
            image_found_in_database = False  # ✅ ensure AI still runs

      
        print("Translations found:", image_found_in_database)

        if not image_found_in_database:
        # AI invoked if the image wasnt found in our own database
            system_prompt = SYSTEM_PROMPT
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=[
                    {
                            "type": "text",
                            "text": json.dumps({
                                "target_language": target_language,
                                "language_script": language_script
                            })
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                        }
                ])
            ]

            model = get_gemini_model_vision()
            #response = await model.invoke(messages)
            response = await model.ainvoke(messages) #Changed to Async invoke

    
            # Attempt to parse JSON output
            try:
                raw_output = response.content.strip()
                cleaned_output = re.sub(r"^```(json)?|```$", "", raw_output.strip(), flags=re.MULTILINE).strip()
                result = json.loads(cleaned_output) 
                
                if "error" in result:
                    return {"error": "Inappropriate content uploaded.."}
                    
                    
        
                #insert the state of database match for Object and Translation
                print(f"AI Output for {target_language}:", result)
                # print("\nAppending Object:", object_found)
                # print("\nAppending translation",translation_found)

               #Overwrite common data fields if already available in database. Dont use AI fields in this case for commondata only.
                #This cover the case when only object exists and no translation is available. will overwrite only commondata
                if object_found is True and existing_result and translation_found is False:
                    for field in existing_result:
                        value = existing_result.get(field)
                        if value is not None:   # only copy if exists
                            result[field] = value
                result.update({
                                "flag_object": object_found,
                                "flag_translation": translation_found
                })
                
                result.update(await process_file_info(None,image_base64,image_filename,None))

                return result    #return the AI result from here only
            except Exception as e:
                print(f"exception in JSON paarsing for LLM output: {str(e)}\nResult:", result)
                return {
                    "error": "Failed to parse Gemini output as JSON",
                    "raw_output": response.content,
                    "exception": str(e)
                }
    except Exception as e:
        print(f"exception in identify_and_tranalate funciton JSON: {str(e)}\nResult:", result)
        return {"error": f"Unexpected error in identify_and_translate: {str(e)}"}

