from fastapi import APIRouter,HTTPException, Depends
from fastapi.responses import JSONResponse
from services.userauth import get_current_user
from services.prompt_orchestrator import generate_agent_prompt
from typing import Dict


from pydantic import BaseModel

# Define the structure of your successful response
class PromptResponse(BaseModel):
    prompt: str
    output_format: str
    # If your function can return an error, you might include:
    # error: Optional[str] = None

router = APIRouter(prefix="/prompt", tags=["AutoPrompter"])


@router.get("/createprompt",response_model=PromptResponse)
async def create_prompt(
    new_agent: str
    #current_user: dict = Depends(get_current_user)
)-> Dict[str, str]: # The endpoint now explicitly returns the new structure
    try:
        prompt_data = await generate_agent_prompt(new_agent)
        # Check if the internal function returned an error structure
        if "error" in prompt_data:
            # If the error is specific to prompt generation failure
            if prompt_data["error"] == "Failed to generate prompt for the specified agent.":
                 raise HTTPException(status_code=404, detail=prompt_data["error"])
            else:
                 # Catch generic errors from the function
                 raise HTTPException(status_code=500, detail=prompt_data["error"])
        
        # Success: prompt_data is {"prompt": "...", "output_format": "..."}
        return prompt_data 
        
    except HTTPException as e:
        # Re-raise explicit HTTP exceptions
        raise
    except Exception as e:
        # Catch any remaining top-level exceptions
        raise HTTPException(status_code=500, detail=f"Failed to process prompt creation request: {e}")