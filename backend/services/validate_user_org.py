from fastapi import HTTPException, status
from typing import Dict, Any
from db.connection import organisations_collection
from services.userauth import get_organisation_id


DEFAULT_LOGO_URL = "https://cdn-icons-png.flaticon.com/512/8074/8074788.png"


async def validate_user_organisation(user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates a user's organisation membership and returns organisation-based settings
    such as allowed languages and logo URL.

    Logic:
    1. If user has no organisation_id → no org validation, return user’s own language + default logo.
    2. If user has organisation_id:
        - Org must exist.
        - Org status must be 'active'.
        - User's email domain must match org email_domain (unless external access is allowed).
        - Return org settings (languages_allowed, logo_url).
    """

    organisation_id = user.get("organisation_id")
    user_email = user.get("email_id")
    print(f"Validating organisation for user: {user['username']} with org ID: {organisation_id} and user email : {user_email}")
    # CASE 1: User not linked to any organisation
    if not organisation_id:
        print(f"User {user['username']} not linked to any organisation. Skipping org checks.")
        return {
            "languages_allowed": user.get("languages_allowed", ["English"]),
            "logo_url": DEFAULT_LOGO_URL,
        }

    # CASE 2: User linked to an organisation — perform full org validations
    organisation = await organisations_collection.find_one({"org_id": organisation_id})
    if not organisation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"organisation not found for ID: {organisation_id}"
        )

    # Status check
    if organisation.get("status") != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"organisation '{organisation['org_name']}' is not active"
        )

    org_settings = organisation.get("settings", {})
    allow_external = org_settings.get("allow_external_access", False)
    org_domain = organisation.get("email_domain")
    print(  f"\nOrganisation '{organisation['org_name']}' found. External access allowed: {allow_external}, Org domain: {org_domain}")
    # Domain validation (if external access not allowed)
    if not allow_external and org_domain:
        try:
            user_domain = user_email.split("@")[1]
            print(f"Validating user email domain '{user_domain}' against organisation domain '{org_domain}'")
            if user_domain.lower() != org_domain.lower():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"User's email domain '{user_domain}' does not match organisation domain '{org_domain}'"
                )
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid user email format for domain validation"
            )
        
    user_langs = user.get("languages_allowed")
    if user_langs and isinstance(user_langs, list) and len(user_langs) > 0:
        effective_langs = user_langs
    else:
        effective_langs = org_settings.get("language_allowed", [""])

    # ✅ Logo preference
    logo_url = org_settings.get("logo_url", DEFAULT_LOGO_URL)
    print (f"\n\nOrganisation '{organisation['org_name']}' validated successfully. Returning settings.")
    return {
        "org_name": organisation.get("org_name"),
        "languages_allowed": effective_langs,
        "logo_url": logo_url,
        "org_id": organisation.get("org_id"),
        "org_code": organisation.get("org_code")
    }



async def get_organisation_details():
    """
    Fetches all active organisations with basic details.
    """
    try:
        org_id = get_organisation_id()
        cursor = await organisations_collection.find_one(
            {
                "org_id": org_id,
                "status": "active"
            },
            {
                "org_name": 1,
                "org_code": 1,
                "email_domain": 1,
                "contact_email": 1,
                "website": 1,
                "address": 1,
                "settings": 1
            }
        )
        return cursor
    except Exception as e:
        print(f" No org Id for this user: {str(e)}")
        return None
