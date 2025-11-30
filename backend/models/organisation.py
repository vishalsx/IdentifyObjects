from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List, Dict, Literal
from datetime import datetime, timezone

class organisationSettings(BaseModel):
    language_allowed: List[str] = Field(default_factory=lambda: ["English"], description="Languages allowed for users in the organisation")
    category: Optional[str] = Field(default="School", description="organisation category/type; e.g School, Corporate etc.")
    affiliation: Optional[str] = Field(default=None, description="Parent domain or board the organisation is associated with. E.g. CBSE, IB etc for school cateagory.")
    language: Optional[str] = Field(default="English", description="Default language for users")
    timezone: Optional[str] = Field(default="UTC", description="organisation's timezone")
    theme: Optional[str] = Field(default="light", description="UI theme preference")
    logo_url: Optional[HttpUrl] = Field(None, description="URL to the organisation's logo image")
    data_retention_days: Optional[int] = Field(default=90, description="Days to retain inactive user data")
    allow_external_access: Optional[bool] = Field(default=False, description="If external collaborators are allowed")
    features_enabled: Optional[List[str]] = Field(default_factory=list, description="List of enabled features/modules")
    db_cluster: Optional[str] = Field(default="default_cluster", description="Database cluster assigned to the organisation")
    database_sharding_key: Optional[str] = Field(default=None, description="Sharding key for database distribution")
    # AI agent configurations
    ai_agent : Optional[str] = Field(default="Visual AI assistant expert in identifying object details in different languages", description="Specific agent based on organisation needs.")
    ai_guiding_prompts: Optional[str] = Field(default="formal", description="Default AI prompting style for the organisation")
    agent_tools: Optional[List[str]] = Field(default_factory=lambda: ["web_search", "code_interpreter"], description="List of AI tools enabled for the organisation")

    
class organisation(BaseModel):
    org_id: str = Field(..., description="Unique organisation ID (UUID or ObjectId)")
    org_name: str = Field(..., description="organisation name")
    org_code: str = Field(..., description="Short unique code or alias for the organisation")
    org_type: Literal['Private', 'Public'] = Field(
        default='Private',
        description="Whether Publically accessible or Private organisation",
        example="Public/Private"
    )    
    email_domain: Optional[str] = Field(None, description="Primary email domain for users of this organisation")
    contact_email: Optional[EmailStr] = Field(None, description="Official contact email")
    contact_phone: Optional[str] = Field(None, description="Support or admin contact phone")
    website: Optional[HttpUrl] = Field(None, description="Official organisation website")
    address: Optional[Dict[str, str]] = Field(
        default_factory=lambda: {
            "line1": "",
            "line2": "",
            "city": "",
            "state": "",
            "country": "",
            "zip_code": ""
        },
        description="organisation address information"
    )
    admin_user_ids: Optional[List[str]] = Field(default_factory=list, description="List of user IDs with admin privileges")
    organisation_license_key: Optional[str] = Field(None, description="License key associated with the organisation")
    max_licensed_members: Optional[int] = Field(default=10, description="Total number of licensed users in the organisation.")
    settings: Optional[organisationSettings] = Field(
        default_factory=organisationSettings,
        description="organisation-level configuration and preferences"
    )
    
    status: Optional[str] = Field(default="active", description="organisation status: active/inactive/suspended")
    created_at: datetime = Field(default_factory=datetime.now(timezone.utc).isoformat())
    updated_at: Optional[datetime] = None
    metadata: Optional[Dict[str, str]] = Field(default_factory=dict, description="Additional metadata about the organisation")


