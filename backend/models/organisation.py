from pydantic import BaseModel, Field, EmailStr, HttpUrl
from typing import Optional, List, Dict
from datetime import datetime

class OrganizationSettings(BaseModel):
    category: Optional[str] = Field(default="School", description="Organization category/type; e.g School, Corporate etc.")
    associated_parent_domains: Optional[str] = Field(default=None, description="Parent domain or board the organization is associated with. E.g. CBSE, IB etc for school cateagory.")
    language: Optional[str] = Field(default="English", description="Default language for users")
    timezone: Optional[str] = Field(default="UTC", description="Organization's timezone")
    theme: Optional[str] = Field(default="light", description="UI theme preference")
    logo_url: Optional[HttpUrl] = Field(None, description="URL to the organization's logo image")
    data_retention_days: Optional[int] = Field(default=90, description="Days to retain inactive user data")
    allow_external_access: Optional[bool] = Field(default=False, description="If external collaborators are allowed")
    features_enabled: Optional[List[str]] = Field(default_factory=list, description="List of enabled features/modules")


class Organization(BaseModel):
    id: Optional[str] = Field(None, description="Unique organization ID (UUID or ObjectId)")
    name: str = Field(..., description="Organization name")
    code: Optional[str] = Field(None, description="Short unique code or alias for the organization")
    
    email_domain: Optional[str] = Field(None, description="Primary email domain for users of this organization")
    contact_email: Optional[EmailStr] = Field(None, description="Official contact email")
    contact_phone: Optional[str] = Field(None, description="Support or admin contact phone")
    website: Optional[HttpUrl] = Field(None, description="Official organization website")
    address: Optional[Dict[str, str]] = Field(
        default_factory=lambda: {
            "line1": "",
            "line2": "",
            "city": "",
            "state": "",
            "country": "",
            "zip_code": ""
        },
        description="Organization address information"
    )
    admin_user_ids: Optional[List[str]] = Field(default_factory=list, description="List of user IDs with admin privileges")
    max_licensed_members: Optional[int] = Field(default=10, description="Total number of licensed users in the organization.")
    settings: Optional[OrganizationSettings] = Field(
        default_factory=OrganizationSettings,
        description="Organization-level configuration and preferences"
    )
    status: Optional[str] = Field(default="active", description="Organization status: active/inactive/suspended")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    metadata: Optional[Dict[str, str]] = Field(default_factory=dict, description="Additional metadata about the organization")