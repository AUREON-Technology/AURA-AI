import os
import json
from functools import wraps
from typing import Dict, Any, List
from fastapi import HTTPException, Depends
from services.security import verify_token

ROLE_HIERARCHY = {
    "User": 0,
    "Premium": 1,
    "Moderator": 2,
    "Developer": 3,
    "Admin": 4,
    "SuperAdmin": 5
}

def get_user_role(user_info: dict) -> str:
    """Extracts and resolves user role using claims, config lists, and local overrides."""
    # 1. Custom Claims from Token
    role = user_info.get("role")
    if role and role in ROLE_HIERARCHY:
        return role

    email = user_info.get("email", "")
    uid = user_info.get("uid", "")

    # 2. Configured env list overrides
    admin_emails = [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
    dev_emails = [e.strip() for e in os.getenv("DEVELOPER_EMAILS", "").split(",") if e.strip()]

    if email in admin_emails:
        return "Admin"
    if email in dev_emails:
        return "Developer"

    # 3. Roles configuration file mapping
    roles_file = "roles.json"
    if os.path.exists(roles_file):
        try:
            with open(roles_file, "r", encoding="utf-8") as f:
                roles_map = json.load(f)
            # Match by email or UID
            return roles_map.get(email, roles_map.get(uid, "User"))
        except Exception:
            pass

    return "User"


def require_role(required_role: str):
    """FastAPI dependency to enforce role authorization.
    
    Usage:
        @app.get("/admin")
        async def admin_route(user_info: dict = Depends(require_role("Admin"))):
            ...
    """
    def dependency(user_info: dict = Depends(verify_token)) -> dict:
        user_role = get_user_role(user_info)
        user_info["role"] = user_role

        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(required_role, 0):
            raise HTTPException(
                status_code=403,
                detail=f"Access forbidden: requires at least {required_role} role."
            )
        return user_info
    return dependency


def require_role_decorator(required_role: str):
    """Python decorator to enforce role authorization on route handlers.
    
    Usage:
        @app.get("/admin")
        @require_role_decorator("Admin")
        async def admin_route(user_info: dict = Depends(verify_token)):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Locate user_info parameter
            user_info = kwargs.get("user_info")
            if not user_info:
                for arg in args:
                    if isinstance(arg, dict) and "uid" in arg:
                        user_info = arg
                        break
            
            if not user_info:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication credentials not found or invalid."
                )

            user_role = get_user_role(user_info)
            user_info["role"] = user_role

            if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(required_role, 0):
                raise HTTPException(
                    status_code=403,
                    detail=f"Access forbidden: requires at least {required_role} role."
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator
