from fastapi import APIRouter, HTTPException, Depends, Query
from bson import ObjectId
from datetime import datetime, timezone
from typing import List, Optional

from models.contest import Contest, ContestCreate, validate_contest_state_transition
from db.connection import contests_collection
from services.userauth import get_current_user, get_current_user_id, get_organisation_id

router = APIRouter(prefix="/contest", tags=["Contests"])

def convert_objectid_to_str(data):
    """Recursively convert ObjectIds to strings."""
    if isinstance(data, list):
        return [convert_objectid_to_str(i) for i in data]
    elif isinstance(data, dict):
        return {k: convert_objectid_to_str(v) for k, v in data.items()}
    elif isinstance(data, ObjectId):
        return str(data)
    elif isinstance(data, datetime):
        return data.isoformat()
    return data

@router.post("/create", response_model=Contest)
async def create_contest(
    contest: ContestCreate,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_id = get_current_user_id()
        user_org_id = get_organisation_id()
        
        # Validation Logic
        if contest.content_type == "Specialized" and not contest.specialized_theme:
             raise HTTPException(status_code=400, detail="Specialized theme is required for Specialized content type")

        if not (contest.registration_start_at < contest.registration_end_at < contest.contest_start_at < contest.contest_end_at):
             raise HTTPException(status_code=400, detail="Invalid date range sequence. Ensure: reg_start < reg_end < contest_start < contest_end")

        # Convert Pydantic model to dict
        contest_data = contest.model_dump(by_alias=True, exclude_none=True)
        
        # Remove _id if it's there to let MongoDB generate it
        contest_data.pop("_id", None)
        
        # Set audit fields
        if user_id:
            contest_data["created_by"] = ObjectId(user_id) if ObjectId.is_valid(user_id) else user_id
        
        contest_data["created_at"] = datetime.now(timezone.utc)
        
        # Handle contest_type and org_id
        if contest.contest_type == "Global":
             contest_data["org_id"] = None
        else: # Local
             if not user_org_id:
                 raise HTTPException(status_code=400, detail="User must belong to an organization to create a Local contest")
             contest_data["org_id"] = user_org_id

        result = await contests_collection.insert_one(contest_data)
        contest_data["_id"] = result.inserted_id
        
        return convert_objectid_to_str(contest_data)
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create contest: {str(e)}")


@router.put("/update/{contest_id}", response_model=Contest)
async def update_contest(
    contest_id: str,
    contest: Contest,
    action: str = Query(..., description="Action to perform: 'save' or 'publish'"),
    current_user: dict = Depends(get_current_user)
):
    if not ObjectId.is_valid(contest_id):
        raise HTTPException(status_code=400, detail="Invalid contest ID")
        
    user_id = get_current_user_id()
    
    # Check if contest exists
    existing = await contests_collection.find_one({"_id": ObjectId(contest_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Contest not found")
    
    # Permission check: Creator or Org Admin should be able to update.
    # For now, matching the existing creator pattern.
    if str(existing.get("created_by")) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized to update this contest")
    
    status = (existing.get("status") or "").strip().lower()


    # if status in {"published", "active", "completed", "cancelled"}:
    #     raise HTTPException(
    #         status_code=400,
    #         detail="Cannot update contest once it is published or active"
    #     )
   # def validate_contest_state_transition(current_state: Optional[str], action: str) -> str:

    contest_data = contest.model_dump(by_alias=True, exclude_none=True)
    contest_data.pop("_id", None) # Don't update _id
    
    new_status = validate_contest_state_transition(status, action)
    if not new_status:
        raise HTTPException(status_code=400, detail="Invalid action for current state")
    contest_data["status"] = new_status
    print ("\n🟢Contest_data", contest_data)
  
    await contests_collection.update_one(
        {"_id": ObjectId(contest_id)},
        {"$set": contest_data}
    )
    
    updated_contest = await contests_collection.find_one({"_id": ObjectId(contest_id)})
    return convert_objectid_to_str(updated_contest)

@router.get("/list", response_model=List[Contest])
async def list_contests(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    query = {}
    if status:
        query["status"] = status.title()
        
    cursor = contests_collection.find(query).skip(skip).limit(limit)
    contests = await cursor.to_list(length=limit)
    return [convert_objectid_to_str(c) for c in contests]

@router.get("/search/{contest_id}", response_model=Contest)
async def get_contest(
    contest_id: str,
    current_user: dict = Depends(get_current_user)
):
    if not ObjectId.is_valid(contest_id):
        raise HTTPException(status_code=400, detail="Invalid contest ID")
        
    contest = await contests_collection.find_one({"_id": ObjectId(contest_id)})
    if not contest:
        raise HTTPException(status_code=404, detail="Contest not found")
        
    return convert_objectid_to_str(contest)


@router.delete("/delete/{contest_id}")
async def delete_contest(
    contest_id: str,
    current_user: dict = Depends(get_current_user)
):
    if not ObjectId.is_valid(contest_id):
        raise HTTPException(status_code=400, detail="Invalid contest ID")
        
    user_id = get_current_user_id()
    
    existing = await contests_collection.find_one({"_id": ObjectId(contest_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Contest not found")
        
    if str(existing.get("created_by")) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized to delete this contest")
        
    await contests_collection.delete_one({"_id": ObjectId(contest_id)})
    return {"message": "Contest deleted successfully"}
