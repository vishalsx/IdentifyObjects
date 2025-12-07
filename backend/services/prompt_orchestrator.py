
from services.validate_user_org import get_organisation_details
import re
import json
from typing import Any

def clean_prompt(raw: Any) -> str:
    """
    Cleans and normalizes a prompt string for safe API transmission.

    - Accepts str, list, dict, or other types.
    - If list/dict -> convert to JSON string (pretty print for readability).
    - Normalize line endings to \n.
    - Remove NULL chars and other non-printable control characters that commonly break APIs.
    - Trims leading/trailing whitespace.
    """
    if isinstance(raw, str):
        s = raw
    else:
        try:
            # For lists/dicts/other objects, convert to pretty JSON.
            s = json.dumps(raw, ensure_ascii=False, indent=2)
        except Exception:
            s = str(raw)

    # Normalize line endings (CRLF -> LF, CR -> LF)
    s = s.replace("\r\n", "\n").replace("\r", "\n")

    # Remove NUL and other non-printable control characters (except newline, tab)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)

    # NOTE: The brace escaping logic (s.replace("{", "{{").replace("}", "}}"))
    # has been removed as it interfered with the template substitution {DEFAULT_AGENT}
    # and corrupted the literal JSON schema instruction for the LLM.

    # Final trim (but keep indentation and newlines inside)
    return s.strip()

# orchestrate_prompt should be updated to import the fixed clean_prompt
# from the new file path if it's moved, or ensure it uses this new definition.


async def orchestrate_prompt(system_prompt: str, default_agent: str)->str:
    #Get the default agent detials from organisation settings
    prompt = None
    organistaion_details = await get_organisation_details()

    # Clean main prompt
    if system_prompt:
        system_prompt = clean_prompt(system_prompt)

    if organistaion_details:
        agent_info = organistaion_details.get("settings", {}).get("ai_agent", "")
        ai_guiding_prompt_JSON = organistaion_details.get("settings", {}).get("ai_guiding_prompts", "")
        ai_guiding_prompt = ai_guiding_prompt_JSON.get("prompt", "") + ai_guiding_prompt_JSON.get("output_format", "")
   
        if agent_info: #Specific agent defined for this organisation
            print (f"\nUsing organisation specific AI agent: {agent_info}")
            new_agent = agent_info # Override default agent with organisation specific agent
            prompt = system_prompt.replace("{DEFAULT_AGENT}", new_agent)
            
            if ai_guiding_prompt: # Replace the default prompt completely.
                prompt = clean_prompt(ai_guiding_prompt)
            else: #No specific guiding prompt defined, use the modified prompt with new agent
                print ("\nNo specific AI guiding prompt defined, generating a new prompt for: {new_agent}.")
                prompt_JSON = await generate_agent_prompt(new_agent) #call the LLM to generate the prompt with new agent
                prompt = prompt_JSON.get("prompt", "") + prompt_JSON.get("output_format", "")

                print(f"\n✅✅✅Generated new prompt for the organisation's specific agent({new_agent})->\n{prompt}✅✅✅")
                #save this prompt back to organisation settings for future use - TODO

        else: #No specific agent defined, use default
            print ("\nUsing default AI agent and guiding prompt.")
            prompt = system_prompt.replace("{DEFAULT_AGENT}", default_agent)
    else:
        prompt = system_prompt.replace("{DEFAULT_AGENT}", default_agent)
    return prompt

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import HumanMessage, SystemMessage
from langchain_core.exceptions import OutputParserException


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



async def generate_agent_prompt(new_agent: str):
    """
    Generates a precise system prompt for Gemini LLM based on the provided agent description.

    Args:
        new_agent (str): Description of the new agent role.

    Returns:
        str: Generated system prompt for the LLM.
    """
        
#------------PROMPT GENERATION TEMPLATE-------------------------------------------#
#           PASS THIS ON TO THE LLM SERVICE TO GENERATE THE APPROPRIATE PROMPT    #
    base_prompt_template= """

You are an expert **Prompt Engineer** specializing in generating precise system prompts to be provided to an LLM, based on the provided cotext. 
Can you generate a precise system prompt for an AI agent, which acts as a {new_agent}. 
Generate the output as a string in a human readable format (example: with proper indentation and newline characters) only and not JSON since this prompt will be used to pass to another LLM call.


Ensure that the generated prompt contains instructions for the LLM which aligns to the context of the {new_agent} in terms of:
- Ask it to generate useful information about objects in images which are relevant for {new_agent}.
- Ask it to generate information keeping target audience of {new_agent} in mind.
- Ask it to generate information with Domain expertise relevant to {new_agent}.
- Ask it to generate fun facts about the image relevant to {new_agent} 
- ask it to always remain culturally sensitive when generating the prompt for {new_agent}.
- Ask it to raise errors for inappropriate content in the image.
- Ask it adhere to age appropriateness guidelines.
- Ask it to generate 5-8 tags, object_category, field of study, for the identified object in english only in the context of {new_agent}
- Ask it to Generate a **hint** in the "<target_language>" for a guessing game with the following rules:
        1. Do not reveal the object name.  
        2. Use riddles, proverbs, or cultural sayings.

- Ask it to Generate a **short hint** (10–15 words) in the <"target_language"> with the followiing rules:  
        1. Do not reveal the object name
        2. Keep it very brief and concise and try and use riddles, proverbs, or cultural sayings.
- Ask it to always use the "<target_language>" and "<language_script>" provided in the HumanMessage for generating the output fields which require translation.
- Ask it to Generate at least 15 questions and answers of varying difficulty with the **difficulty level** in **Quiz style**  to test the knowledge, related to the object and the object description, in "<target_language>" and "<language_script>". Ensure the question and answers are precise and educational.

"""
    fixed_prompt_footer = """
***Important Guardrails***
    - If the image contains a known celebrity, political leader, or divine/religious figure → only provide **neutral, respectful details**. No opinions or negative commentary.  
    - If the image depicts inappropriate/explicit content → respond with `"Inappropriate content."` for all output fields.  
    - Never include politically or religiously sensitive or controversial content.  
    - If you find any violations in the guardrail rule, raise this error in the error JSON tag

You will always receive input in the following format inside the HumanMessage:
{
"target_language": "<language name>",
"language_script": "<writing script>"
"additional_context": "<Must consider optional additional context about the object or any related information in your response. If no additional context is provided, ignore this field.>"
}
You will also receive an image encoded as base64.

***The output should be strictly in **valid JSON only** (no markdown, no comments).***
Output JSON format:
{
"object_name_en": "<object name in English>",
"object_name": "<object name in target_language using language_script>",
"translated_to": "<target_language>",
"object_description": "<description about the object in target_language using language_script>",
"object_hint": "<hint in target_language using language_script>",
"object_short_hint": "<short hint without naming the object in target_language using language_script>",
"tags": ["<tag1>", "<tag2>", "..."],
"object_category": "<category in English>",
"field_of_study": "<field of study in English>",
"age_appropriate": "<all ages | kids | teens | adults | seniors>"
"quiz_qa": [ {"question": "<question1 in target_language using language_script>", "answer": "<answer1 in target_language using language_script>", "difficulty_level": "<low, medium, high, very high>"}
"error:" "<Inappropriate content detected. Can't be processed if its an inappropriate image..>"
}

"""
#------------END OF PROMPT GENERATION TEMPLATE------------------------------------#

    prompt = base_prompt_template.replace("{new_agent}", new_agent)

    try:
        system_prompt = prompt
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=[
                {
                        "type": "text",
                        "text": json.dumps({
                            "target_language": "English",
                            "language_script": "Latin"
                        })
                    },
            ])
        ]
    
        model = get_gemini_model_vision()
        response = await model.ainvoke(messages) #Changed to Async invoke
        response = response.content.strip()
        print(f"\n❌❌Raw generated prompt for Agent({new_agent})->\n{response}❌❌")
        # response = await review_generated_prompt(response, new_agent) #send to to a prompt reviewer agent

        # 2. Package the response into the desired JSON format
        final_response_data = {
            "prompt": response,
            "output_format": fixed_prompt_footer
        }

        
        print (f"\n✅✅✅Generated prompt for Agent({new_agent})->\n{final_response_data}✅✅✅")
        # Attempt to parse JSON output
        # raw_output = response.content.strip()
        # cleaned_output = re.sub(r"^```(json)?|```$", "", raw_output.strip(), flags=re.MULTILINE).strip()
        # result = json.loads(cleaned_output) 
            
        # if result.get("error"): # This is robust and checks if the value is truthy (non-empty string)
        #     return {"error": result.get("error")}
        
        # print(f"AI Output for Agent: {new_agent}:", result)

    except OutputParserException as e:
        print(f"\nOutputParserException (Model failed to adhere to schema): {str(e)} and provided following resposne: {response}")    
        # Return an error structure that matches the successful return type
        return {"error": "Failed to generate prompt for the specified agent.", "output_format": ""}
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        return {"error": f"An unexpected error occurred: {e}", "output_format": ""}

    # 3. Return the Python dictionary, which FastAPI automatically converts to JSON
    return final_response_data



async def review_generated_prompt(unreviewed_prompt: str, agent: str)->str:


    """
    Reviews the generated prompt for quality and adherence to guidelines.

    Args:
        prompt (str): The generated prompt to be reviewed.
        agent (str): The agent description for context.

    Returns:
        str: Reviewed and potentially modified prompt.
    """
    review_prompt_template= """

You are an expert **Prompt Engineer Agent** specializing in reviewing, optimizing, and correcting system prompts for large language models (LLMs). 
Your goal is to review the generated system prompt for clarity, effectiveness, security, and adherence to LLM best practices.
***The output format is a string only containing the reviewed prompt***

Can you review the following system prompt generated for an AI agent, which acts as a {agent}.

**Core Review Directives:**

1.  **Clarity and Specificity:** Is the **role** of the LLM clearly defined? Are the **goals** and **constraints** unambiguous? Are all terms defined?
2.  **Output Format and Consistency:** Is the required output format (e.g., JSON, Markdown, pure text) strictly specified? Are fields and required information clearly enumerated?
3.  **Instruction Placement and Emphasis:** Are the most critical instructions (e.g., safety, format, guardrails) placed near the end and emphasized (e.g., using bold text, triple quotes, or specific sections)?
4.  **Guardrails and Security:** Does the prompt include effective **negative constraints** (e.g., "NEVER mention X," "DO NOT guess") and explicit safety guardrails (e.g., handling inappropriate content, refusal instructions)? Does it mitigate risks of prompt injection from user input?
5.  **Efficiency and Brevity:** Can the prompt be shortened without losing necessary detail? Avoid unnecessary conversational filler.
    
Here is the prompt to review:
Generated Prompt to be reviewed: {prompt}
    """

    prompt = review_prompt_template.replace("{agent}", agent)
    prompt = review_prompt_template.replace("{prompt}", unreviewed_prompt)

    try:
        system_prompt = prompt
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=[
                {
                        "type": "text",
                        "text": json.dumps({
                            "target_language": "English",
                            "language_script": "Latin"
                        })
                    },
            ])
        ]
    
        model = get_gemini_model_vision()
        response = await model.ainvoke(messages) #Changed to Async invoke
        response = response.content.strip()
    except OutputParserException as e:
        print(f"\nOutputParserException (Model failed to adhere to schema): {str(e)} and provided following resposne: {response}")    
        # Return an error structure that matches the successful return type
        return {"error": "Failed to generate prompt for the specified agent.", "output_format": ""}
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        return {"error": f"An unexpected error occurred: {e}", "output_format": ""}
    print (f"\n✅✅✅Reviewed prompt for Agent({agent})->\n{response}✅✅✅")
    return response


    
