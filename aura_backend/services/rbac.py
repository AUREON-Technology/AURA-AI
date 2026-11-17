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

#: Sessions from the customer web sign-in (``POST /api/v3/auth/consumer/token``) carry this
#: ``channel``. They skip device attestation, so they can never hold a platform role above
#: ``User`` -- whatever an email list, roles.json, the Access page or a claim says (SaaS P1-B).
CONSUMER_WEB_CHANNEL = "consumer_web"


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


#: The only identity provider whose email claim may select a role from an email list (SEC-08).
EMAIL_GRANT_PROVIDER = "google.com"


def email_grant_eligible(user_info: dict) -> bool:
    """Whether this identity's ``email`` may be matched against the email lists (SEC-08).

    The env lists and roles.json name people by email address, and an email claim is only as
    good as whoever vouched for it. Firebase will mint an ID token for an email/password account
    whose address nobody has verified -- so if that provider were ever enabled, anyone holding
    the (public) Firebase API key could register an operator's address and be granted
    SuperAdmin by address alone. The match therefore needs both:

    * ``email_verified is True`` -- the address was proven, not just typed; and
    * ``sign_in_provider == "google.com"`` -- the proof came from Google sign-in, the only
      provider the console and the apps offer.

    Both keys are written only by ``services.security.verify_identity_token`` from a verified
    Google or Firebase ID token (Firebase's ``firebase.sign_in_provider``; a Google ID token is
    Google sign-in by definition), and travel with the server-side refresh record. Nothing a
    client sends can set them: access-token claims (``verify_access_token``) never carry them.
    An identity without them still keeps a role it holds by uid in roles.json, by Access-page
    assignment, or by the role claim this server stamped into its own access token.
    """
    return (
        user_info.get("email_verified") is True
        and user_info.get("sign_in_provider") == EMAIL_GRANT_PROVIDER
    )


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
    if user_info.get("channel") == CONSUMER_WEB_CHANNEL:
        return "User"
    # An unverified or non-Google email selects nothing from the email lists; roles.json is
    # still consulted by uid, which the identity provider -- not the user -- chose.
    email = (user_info.get("email") or "").strip() if email_grant_eligible(user_info) else ""
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
