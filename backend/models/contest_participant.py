from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId
from .books import PyObjectId

class EntrySource(BaseModel):
    type: str
    registered_at: datetime
    school_id: Optional[str] = None
    invited_by: Optional[str] = None

class ParticipationTimeline(BaseModel):
    applied_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

class EligibilitySnapshot(BaseModel):
    min_age: int
    max_age: int
    allowed_countries: List[str]
    school_required: bool = False

class ParticipantSchool(BaseModel):
    school_id: str
    school_name: str

class ParticipantFlags(BaseModel):
    is_late_registration: bool = False
    manual_review_required: bool = False
    cheating_flagged: bool = False

class ContestParticipant(BaseModel):
    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    
    # Identifiers
    contest_id: PyObjectId
    user_id: PyObjectId
    
    # Entry Sources
    entry_sources: List[EntrySource] = Field(default_factory=list)
    active_entry_source: Optional[str] = None
    
    # Participation Status
    status: str = "applied"  
    # applied | approved | active | completed | withdrawn | disqualified | rejected
    
    # Participation Timeline
    participation_timeline: ParticipationTimeline = Field(default_factory=ParticipationTimeline)
    
    # User Selection Snapshot
    selected_languages: List[str] = Field(default_factory=list)
    proficiency_map: Dict[str, str] = Field(default_factory=dict)
    field_of_study: List[str] = Field(default_factory=list)
    
    # Age & Eligibility Snapshot
    age_group: Optional[str] = None
    age_at_registration: Optional[int] = None
    eligibility_snapshot: Optional[EligibilitySnapshot] = None
    
    # User Context (Denormalized)
    school: Optional[ParticipantSchool] = None
    city: Optional[str] = None
    country: Optional[str] = None
    
    # Flags & Control
    flags: ParticipantFlags = Field(default_factory=ParticipantFlags)
    
    # System Audit
    org_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str}
    }

class ContestParticipantCreate(BaseModel):
    contest_id: str
    user_id: str
    entry_sources: List[EntrySource]
    active_entry_source: str
    selected_languages: List[str]
    proficiency_map: Dict[str, str]
    field_of_study: List[str]
    age_group: Optional[str] = None
    age_at_registration: Optional[int] = None
    school: Optional[ParticipantSchool] = None
    city: Optional[str] = None
    country: Optional[str] = None
