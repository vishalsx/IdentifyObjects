
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage
from langchain_core.exceptions import OutputParserException
import json
import re
from services.db_crud import get_language_details
from services.prompt_orchestrator import orchestrate_prompt
from dotenv import load_dotenv
import os


load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set")
os.environ["GOOGLE_API_KEY"] = API_KEY
temperature = os.getenv("DEFAULT_TEMPERATURE", "0.2")


def get_gemini_model_vision():
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=temperature, max_retries=3, timeout=120)


async def identify_and_translate(image_base64: str, imagehash: str, image_filename: str, target_language: str, additional_context: str = None) -> dict:
    try:
        DEFAULT_AGENT = "A Visual AI assistant"

        SYSTEM_PROMPT = """
        You are {DEFAULT_AGENT}, expert in identifying object details in different languages.
        You will always receive input in the following format inside the HumanMessage:
        {
        "target_language": "<language name>",
        "language_script": "<writing script>",
        "additional_context": "<Must consider optional additional context about the object or any related information in your response. If no additional context is provided or if this tag is blank or NULL then ignore this field.>"

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
        11. Generate at least 15 questions and answers of varying difficulty with the **difficulty level** in **Quiz style** to test the knowledge, related to the object and the object description, in `target_language` and `language_script`. Follow the following rules for generating questions and answers:
            - Must ensure the question and answers are precise and educational
            - Must ensure that ***answers are never same*** for more than 1 generated questions.
            - Must ensure that the answers are never same as the object name itself
            - Avoid generating Yes No or True False type of question/answers
            - Vary the difficulty levels across low, medium, high, very high
            - The question and answers must align to the context as defined in system promt : ***{DEFAULT_AGENT}***


        ---

        Important Guardrails:
        - If the image contains a known celebrity, political leader, or divine/religious figure → only provide **neutral, respectful details**. No opinions or negative commentary.  
        - If the image depicts inappropriate/explicit content → respond with `"Inappropriate content."` for all fields.  
        - Never include politically or religiously sensitive or controversial content.  
        - If you find any violations in the guardrail rule, raise this error in the error JSON tag
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
        "quiz_qa": [ {"question": "<question1 in target_language using language_script>", "answer": "<answer1 in target_language using language_script>", "difficulty_level": "<low, medium, high, very high>"}
        "error:" "<Inappropriate content detected. Can't be processed..>"
        }
        """

        result = {}

        language_script = ""
        language_details = await get_language_details(target_language) #get language details.
        if not language_details or "error" in language_details:
            pass #use default scripts if langage not defined in languages collection
        else:
            language_script = language_details.get("script", "")
        
        SYSTEM_PROMPT = await orchestrate_prompt(SYSTEM_PROMPT, DEFAULT_AGENT)
        # print("\nFinal SYSTEM PROMPT used:\n", SYSTEM_PROMPT)

        try:
            system_prompt = SYSTEM_PROMPT
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=[
                    {
                            "type": "text",
                            "text": json.dumps({
                                "target_language": target_language,
                                "language_script": language_script,
                                "additional_context": additional_context or ""
                            })
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                        }
                ])
            ]
        except OutputParserException as e:
            print(f"\nOutputParserException (Model failed to adhere to schema): {str(e)}")    

        try:
            model = get_gemini_model_vision()
            response = await model.ainvoke(messages) #Changed to Async invoke

        except OutputParserException as e:
            print(f"\nOutputParserException (Model failed to adhere to schema): {str(e)} and provided following resposne: {response}")    

        # Attempt to parse JSON output
        try:            
            raw_output = response.content.strip()
            # cleaned_output = re.sub(r"^```(json)?|```$", "", raw_output.strip(), flags=re.MULTILINE).strip()
            # cleaned_output = re.sub(r"(?<![A-Za-z0-9])'([^']*)'(?![A-Za-z0-9])", r'"\1"', cleaned_output)
            cleaned_output = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_output.strip(), flags=re.DOTALL)
            result = json.loads(cleaned_output) 

            if not isinstance(result, dict):
                result = {}

            if result.get("error"): # This is robust and checks if the value is truthy (non-empty string)
                return {"error": result.get("error")}

            print(f"✨✨✨✨✨✨AI Output for {target_language}:✨✨✨✨✨✨", result)
            return result    #return the AI result from here only
        except Exception as e:
            print(f"exception in JSON paarsing for LLM output: {str(e)}: {response.content}")
            return {
                "error": "Failed to parse Gemini output as JSON",
                "raw_output": response.content,
                "exception": str(e)
            }
    except Exception as e:
        print(f"exception in identify_and_tranalate funciton JSON: {str(e)}\nResult:", result)
        return {"error": f"Unexpected error in identify_and_translate: {str(e)}"}

