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


#: The env lists that can grant a console-capable role, highest first. Names are read *both*
#: bare and `AURA_`-prefixed -- see `_email_list`.
CONSOLE_ROLE_ENV_VARS = ("SUPERADMIN_EMAILS", "ADMIN_EMAILS", "DEVELOPER_EMAILS")

ROLES_FILE = "roles.json"


def _email_list(name: str) -> list[str]:
    """Read a comma-separated email list, accepting the `AURA_`-prefixed name too.

    Historically bare only, because this module predates `aura_core`'s `_env` helper and its
    `AURA_` convention. That inconsistency is a trap rather than a preference: every other
    setting in a deployment is `AURA_`-something, so an operator configuring
    `AURA_SUPERADMIN_EMAILS` by analogy got no role, no error naming the cause, and a 403 that
    blamed their resolved role. Both spellings are accepted so the guess cannot fail silently.

    The bare name wins when both are set, so no existing deployment changes behaviour.
    """
    raw = os.getenv(name) or os.getenv(f"AURA_{name}") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def console_operators_configured() -> bool:
    """Whether *anyone* can reach the operator console on this deployment.

    A fresh deployment has no console operator and nothing says so: the first person to sign in
    is refused with a message about their role, which is true but sends them looking in the wrong
    place. This is what lets that refusal name the real cause instead.
    """
    if any(_email_list(name) for name in CONSOLE_ROLE_ENV_VARS):
        return True
    # roles.json can also grant a console role, so its mere presence counts as "configured" --
    # whether it names *this* caller is a different question, and the ordinary 403 covers it.
    return os.path.exists(ROLES_FILE)


def _roles_file_role(email: str, uid: str) -> str | None:
    roles_file = ROLES_FILE
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


def configured_identities() -> set[str]:
    """Every email or uid the env lists or roles.json name -- the people who may hold a role
    without any Access-page assignment, so the operator directory can list them."""
    names: set[str] = set()
    for var in CONSOLE_ROLE_ENV_VARS:
        names.update(_email_list(var))
    if os.path.exists(ROLES_FILE):
        try:
            with open(ROLES_FILE, "r", encoding="utf-8") as f:
                names.update(str(k) for k in json.load(f))
        except Exception:
            pass
    return names


def granted_role(email: str, uid: str) -> tuple[str, str | None]:
    """The role the operator's configuration grants, and where it comes from.

    The env lists and roles.json -- the sources only someone with access to the deployment can
    change. An Access-page assignment cannot go below this (``api/admin_platform.py``): demoting
    someone the env still names would be undone at their next sign-in, so it is refused with the
    source named instead. Returns ``("User", None)`` when nothing grants a role.
    """
    email = (email or "").strip()
    if email and email in _email_list("SUPERADMIN_EMAILS"):
        return "SuperAdmin", "SUPERADMIN_EMAILS"
    best, source = "User", None
    for role, granted_by in (
        ("Admin" if email and email in _email_list("ADMIN_EMAILS") else None, "ADMIN_EMAILS"),
        ("Developer" if email and email in _email_list("DEVELOPER_EMAILS") else None,
         "DEVELOPER_EMAILS"),
        (_roles_file_role(email, uid), ROLES_FILE),
    ):
        if role and ROLE_HIERARCHY[role] > ROLE_HIERARCHY[best]:
            best, source = role, granted_by
    return best, source


def get_user_role(user_info: dict, *, assigned_role: str | None = None) -> str:
    """Resolve the highest applicable role from env lists, roles.json, an Access-page
    assignment, and the token's own claim.

    ``SUPERADMIN_EMAILS`` is the only env list that can mint SuperAdmin. ``ADMIN_EMAILS``
    never auto-upgrades to SuperAdmin.

    ``assigned_role`` is the Access page's ``users.role`` for this subject
    (``infrastructure/db/user_repo.assigned_role``), looked up by the caller because it needs the
    database. The claimed ``role`` is honoured because an access token is the server's own
    15-minute statement of the role; refresh strips it before re-resolving
    (``services/jwt_auth.refresh_token_pair``) so it can never outlive its sources.
    """
    email = (user_info.get("email") or "").strip()
    uid = str(user_info.get("uid") or user_info.get("sub") or "")

    granted, _ = granted_role(email, uid)
    candidates: list[str] = [granted]
    persisted = assigned_role or user_info.get("db_role") or user_info.get("persisted_role")
    if persisted in ROLE_HIERARCHY:
        candidates.append(persisted)
    claimed = user_info.get("role")
    if claimed in ROLE_HIERARCHY:
        candidates.append(claimed)
    return max(candidates, key=lambda role: ROLE_HIERARCHY[role])


def stamp_resolved_role(user_info: dict, *, assigned_role: str | None = None) -> dict:
    """Mutate and return user_info with a resolved ``role`` claim."""
    user_info["role"] = get_user_role(user_info, assigned_role=assigned_role)
    return user_info


async def stamp_role(user_info: dict) -> dict:
    """``stamp_resolved_role`` including the subject's Access-page assignment.

    Every place that issues a token calls this, so an assignment takes effect at the subject's
    next sign-in or refresh (at most an access-token lifetime, 15 minutes).
    """
    from infrastructure.db.user_repo import assigned_role

    uid = str(user_info.get("uid") or user_info.get("sub") or "")
    return stamp_resolved_role(user_info, assigned_role=await assigned_role(uid))


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
