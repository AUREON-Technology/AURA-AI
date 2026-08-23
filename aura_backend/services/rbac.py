import os
import json
from fastapi import HTTPException, Depends
from services.jwt_auth import verify_access_token

ROLE_HIERARCHY = {
    "User": 0,
    "Premium": 1,
    "Moderator": 2,
    "Developer": 3,
    "Admin": 4,
    "SuperAdmin": 5
}

CONSOLE_MIN_ROLE = "Developer"


def _email_list(name: str) -> list[str]:
    return [e.strip() for e in os.getenv(name, "").split(",") if e.strip()]


def _roles_file_role(email: str, uid: str) -> str | None:
    roles_file = "roles.json"
    if not os.path.exists(roles_file):
        return None
    try:
        with open(roles_file, "r", encoding="utf-8") as f:
            roles_map = json.load(f)
        mapped = roles_map.get(email, roles_map.get(uid))
        if mapped in ROLE_HIERARCHY:
            return mapped
    except Exception:
        return None
    return None


def get_user_role(user_info: dict) -> str:
    """Resolve the highest applicable role from env lists, persisted role, JWT, and roles.json.

    ``SUPERADMIN_EMAILS`` is the only env list that can mint SuperAdmin. ``ADMIN_EMAILS``
    never auto-upgrades to SuperAdmin.
    """
    email = (user_info.get("email") or "").strip()
    uid = str(user_info.get("uid") or user_info.get("sub") or "")

    if email and email in _email_list("SUPERADMIN_EMAILS"):
        return "SuperAdmin"

    candidates: list[str] = []
    persisted = user_info.get("db_role") or user_info.get("persisted_role")
    if persisted in ROLE_HIERARCHY:
        candidates.append(persisted)
    claimed = user_info.get("role")
    if claimed in ROLE_HIERARCHY:
        candidates.append(claimed)
    if email and email in _email_list("ADMIN_EMAILS"):
        candidates.append("Admin")
    if email and email in _email_list("DEVELOPER_EMAILS"):
        candidates.append("Developer")
    mapped = _roles_file_role(email, uid)
    if mapped:
        candidates.append(mapped)

    if not candidates:
        return "User"
    return max(candidates, key=lambda role: ROLE_HIERARCHY[role])


def stamp_resolved_role(user_info: dict) -> dict:
    """Mutate and return user_info with a resolved ``role`` claim."""
    user_info["role"] = get_user_role(user_info)
    return user_info


def is_console_operator(role: str) -> bool:
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY[CONSOLE_MIN_ROLE]


def require_role(required_role: str):
    """FastAPI dependency to enforce role authorization.
    
    Usage:
        @app.get("/admin")
        async def admin_route(user_info: dict = Depends(require_role("Admin"))):
            ...
    """
    def dependency(user_info: dict = Depends(verify_access_token)) -> dict:
        user_role = get_user_role(user_info)
        user_info["role"] = user_role

        if ROLE_HIERARCHY.get(user_role, 0) < ROLE_HIERARCHY.get(required_role, 0):
            raise HTTPException(
                status_code=403,
                detail=f"Access forbidden: requires at least {required_role} role."
            )
        return user_info
    return dependency
