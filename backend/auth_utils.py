from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
import os
from dotenv import load_dotenv
from jose import JWTError, jwt


load_dotenv()  # Load environment variables from .env file

SECRET_KEY = os.getenv("SECRET_KEY","super-secret-key")  # use env var in production
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# SECRET_KEY = "super-secret-key"  # 🔑 use the same as in login.py
# ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# def get_current_user(token: str = Depends(oauth2_scheme)):
#     try:
#         payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
#         username: str = payload.get("sub")
#         if username is None:
#             raise HTTPException(
#                 status_code=status.HTTP_401_UNAUTHORIZED,
#                 detail="Invalid authentication token",
#                 headers={"WWW-Authenticate": "Bearer"},
#             )
#         return payload
#     except jwt.ExpiredSignatureError:
#         raise HTTPException(status_code=401, detail="Token has expired")
#     except jwt.PyJWTError:
#         raise HTTPException(status_code=401, detail="Invalid token")

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

    return {
        "user_id": user_id,
        "role": user_role
    }
