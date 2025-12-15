from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
import os
from dotenv import load_dotenv
from jose import JWTError, jwt
from typing import Optional, Dict, Any

load_dotenv()  # Load environment variables from .env file

SECRET_KEY = os.getenv("SECRET_KEY","super-secret-key")  # use env var in production
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# SECRET_KEY = "super-secret-key"  # 🔑 use the same as in login.py
# ALGORITHM = "HS256"

#oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


import contextvars

# Async-safe context variable to store user info
_current_user: contextvars.ContextVar[dict] = contextvars.ContextVar("current_user", default=None)


def set_current_user(user: dict):
    """Set current user info in context (call per request)"""
    _current_user.set(user)


def get_current_user_id() -> str | None:
    """Retrieve the user_id from context, returns None if not set"""
    user = _current_user.get()
    return user.get("user_id") if user else None


def get_organisation_id() -> str | None:
    """Retrieve organisation Id if it exists for the user"""
    user = _current_user.get()
    if user:
        org_id = user.get("organisation_id")
        
    return org_id if user and org_id else None


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Decode the JWT token and normalize the user dict so every route
    receives the same shape:
    {
        "user_id": <str>,
        "role": <str>
    }
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

    # user_id comes from `sub`
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing sub")

    # normalize roles: sometimes [["Contributor"]], sometimes ["Contributor"]
    raw_roles = payload.get("roles", [])
    user_role = None
    if raw_roles:
        if isinstance(raw_roles[0], list) and raw_roles[0]:
            user_role = raw_roles[0][0]
        else:
            user_role = raw_roles[0]

    if not user_role:
        raise HTTPException(status_code=403, detail="No role assigned to this user")
    
    tokens_info = {
        "user_id": user_id,
        "role": user_role,
    }

    organisation_id = payload.get("organisation_id") 
    if organisation_id: # Add organisation_id if present
        tokens_info["organisation_id"] = organisation_id

    # user_info = {"user_id": user_id, "role": user_role}
    user_info = tokens_info

    # Store globally in context variable for this request
    set_current_user(user_info)
    
    # return {
    #     "user_id": user_id,
    #     "role": user_role
    # }
    return user_info

