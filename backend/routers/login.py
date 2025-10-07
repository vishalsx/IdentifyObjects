# login.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel
from typing import List, Dict, Any
import jwt
import datetime
from dotenv import load_dotenv
import os
import hashlib


# --- IMPORT CENTRALIZED DB CONNECTION ---
from db.connection import db  # use db directly

# --- CONFIG ---
load_dotenv()  # Load environment variables from .env file

SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# db = db_client[db_name]  # use shared db client
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Support both old bcrypt hashes and new bcrypt_sha256
from passlib.context import CryptContext

# Modern, secure password context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"   
)


router = APIRouter()

# --- SCHEMAS ---

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    roles: List[str]
    permissions: List[str]
    languages_allowed: List[str]
    permission_rules: Dict[str, Any]  # ✅ detailed permission rules


class CreateUserRequest(BaseModel):
    username: str
    email_id: str
    phone: str
    password: str
    roles: List[str]   # ✅ now always an array
    languages_allowed: List[str]
    country: str

class CreateUserResponse(BaseModel):
    message: str
    user_id: str

# --- UTILS ---
def hash_password(password: str) -> str:

    if len(password.encode('utf-8')) > 72:
        # Pre-hash long passwords with SHA256 to ensure they're under 72 bytes
        password = hashlib.sha256(password.encode('utf-8')).hexdigest()

    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password) -> bool:

    if len(plain_password.encode('utf-8')) > 72:
        plain_password = hashlib.sha256(plain_password.encode('utf-8')).hexdigest()
    
    return pwd_context.verify(plain_password, hashed_password)

async def get_user(username: str):
    return await db["users"].find_one({"username": username})

async def get_roles(roles: List[str]):
    cursor = db["roles"].find({"_id": {"$in": roles}})
    return await cursor.to_list(length=None)



users_collection = db["users"]
roles_collection = db["roles"]
permission_rules_collection = db["permission_rules"]


async def get_user_permissions(username: str) -> dict:
    """
    Fetch user's permissions based on roles and return each permission
    with allowed 'from' states for metadata and language.
    Example:
    {
      "SaveText": { "metadata": [None, "Draft"], "language": [None, "Draft"] },
      "VerifyText": { "metadata": ["Draft", "Verified"], "language": ["Draft"] }
    }
    """
    # 1. Find user
    user = await users_collection.find_one({"username": username})
    if not user:
        raise HTTPException(status_code=404, detail=f"User {username} not found")

    # 2. Ensure roles is a list
    roles = user.get("roles")
    if not roles:
        raise HTTPException(status_code=400, detail=f"User {username} has no roles assigned")

    if isinstance(roles, str):
        roles = [roles]  # normalize

    # 3. Find role documents for all roles
    cursor = roles_collection.find({"_id": {"$in": roles}})
    role_docs = await cursor.to_list(length=None)

    if not role_docs:
        raise HTTPException(status_code=404, detail=f"No role documents found for {roles}")

    # 4. Collect unique permission IDs
    permissions = {perm for role in role_docs for perm in role.get("permissions", [])}

    if not permissions:
        return {}

    # 5. Fetch corresponding permission_rules for these permissions
    cursor = db.permission_rules.find({"_id": {"$in": list(permissions)}})
    rules = await cursor.to_list(length=None)

    result = {}
    for rule in rules:
        state_transitions = rule.get("stateTransitions", {})
        transition_type = rule.get("transitionType","")
        metadata_from_states = [t.get("from") for t in state_transitions.get("metadata", [])]
        language_from_states = [t.get("from") for t in state_transitions.get("language", [])]
       
        # Deduplicate while preserving order
        metadata_from_states = list(dict.fromkeys(metadata_from_states))
        language_from_states = list(dict.fromkeys(language_from_states))

        result[rule["_id"]] = {
            "metadata": metadata_from_states,
            "language": language_from_states,
            "transitionType": transition_type,
        }

    return result




def create_access_token(data: dict):
    to_encode = data.copy()
    to_encode.update({"exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- CREATE USER ENDPOINT ---
@router.post("/create-user", response_model=CreateUserResponse)
async def create_user(user: CreateUserRequest):
    # check for duplicate username
    existing_username = await db["users"].find_one({"username": user.username})
    if existing_username:
        raise HTTPException(status_code=400, detail="Username already exists")

    # check for duplicate email
    existing_email = await db["users"].find_one({"email_id": user.email_id})
    if existing_email:
        raise HTTPException(status_code=400, detail="Email already exists")

    # check for duplicate phone
    existing_phone = await db["users"].find_one({"phone": user.phone})
    if existing_phone:
        raise HTTPException(status_code=400, detail="Phone number already exists")

     # hash the password (now handles long passwords properly)
    try:
        hashed_pw = hash_password(user.password)
    except Exception as e:
        print(f"Password hashing failed: {e}")
        raise HTTPException(status_code=500, detail="Password processing failed")

    doc = {
        "username": user.username,
        "email_id": user.email_id,
        "phone": user.phone,
        "password_hash": hashed_pw,
        "roles": user.roles,   # ✅ now stored as list
        "languages_allowed": user.languages_allowed,
        "country": user.country,
        "created_at": datetime.datetime.utcnow(),
        "is_active": True
    }

    result = await db["users"].insert_one(doc)

    return CreateUserResponse(
        message="User created successfully",
        user_id=str(result.inserted_id)
    )


# --- LOGIN ENDPOINT ---
@router.post("/login", response_model=LoginResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    print("Login attempt for user:", form_data.username, form_data.password)
    print(f"\n DBNAME:{db.name}\nColletions:{await db.list_collection_names()} ")
    user = await get_user(form_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    if not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )
    roles = user.get("roles", [])
    if isinstance(roles, str):
        roles = [roles]  # fallback for old users
    
    # Create access token
    access_token = create_access_token(
        {"sub": user["username"], "roles": [user["roles"]]}
    )

    # # Fetch permissions for this user based on the role assigned
    # permissions = await get_user_permissions(user["username"])
    # print ("Permissions for user:", user["username"], permissions)
    # print("User roles:", user["roles"])
    permission_rules = await get_user_permissions(user["username"])
    print ("\nPermission rules for user:", user["username"], permission_rules)

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        username=user["username"],
        roles=roles,
        permissions=list(permission_rules.keys()),   # just IDs
        languages_allowed=user.get("languages_allowed", []),
        permission_rules=permission_rules            # ✅ full mapping
    )



    # return LoginResponse(
    #     access_token=access_token,
    #     token_type="bearer",  # ✅ add this
    #     username=user["username"],
    #     roles=roles,
    #     permissions=permissions,
    #     languages_allowed=user.get("languages_allowed", [])
    # )
