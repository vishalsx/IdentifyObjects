
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from typing import List, Dict, Any
from typing import Optional

# Modern, secure password context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"   
)
# --- SCHEMAS ---

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    roles: List[str]
    permissions: List[str]
    languages_allowed: List[str] #this shoudl come from the licensed organisation automatically.
    permission_rules: Dict[str, Any]  # ✅ detailed permission rules
    org_name: Optional[str] = None
    logo_url: Optional[str] = None
    org_id: Optional[str] = None
    org_code: Optional[str] = None

class CreateUserRequest(BaseModel):
    username: str
    email_id: Optional[EmailStr] = None
    phone: Optional [str] = None
    password: str
    roles: List[str]   # ✅ now always an array
    languages_allowed: List[str]
    country: Optional[str] = None
    organisation_id: Optional[str] = None

class CreateUserResponse(BaseModel):
    message: str
    user_id: str
