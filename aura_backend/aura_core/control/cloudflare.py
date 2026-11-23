"""The Cloudflare tab's engine: Workers AI providers, models and the account (console plan, B3).

Engine layer, like ``keys.py``: it builds on the Railway service of stage 2 and the ports.

A Cloudflare provider is a set of ``AURA_PROVIDER_<NAME>_*`` variables plus its name in
``AURA_LLM_PROVIDERS`` on the gateway, so **add, replace and remove are each one gateway variable
change**, applied through ``RailwayService.apply_variables``: the candidate environment is booted
through the real settings code in a child process, written, redeployed once, watched, and rolled back
with the old values if the gateway does not come back. Nothing here talks to Railway directly.

* **Add** -- a new provider from a validated ``CloudflareProviderSpec``. Its key is either created
  (a new user token scoped to the account, which a failed change revokes again) or pasted. If
  hosted inference has not been consented to, the caller may consent in the same change.
* **Replace** -- the model and the form fields of an existing Cloudflare provider. The key is kept
  unless a new one is asked for, and a new key goes into the variable the provider already reads.
* **Remove** -- takes the name out of the list and deletes every variable the provider owns. It
  names the token to revoke (``revoke_token_id``) when no other provider uses the same key, and
  attaches a follow-up note to the change. The revoke itself is :meth:`revoke_after_success`, run by
  the reconciler only once the change has stayed healthy for its whole watch, never before it: a
  change that is rolled back must find its key still working.

Only providers whose base URL is on the Cloudflare API root are listed or touched: Groq, the GPU
Space and the local fallback are not editable from here. The typed confirmation for add, replace and
remove is the provider's name. No key value is ever returned or journaled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from ..config.external_inference import is_open_weight_model
from ..domain.cloudflare_provider import (
    PROVIDER_OPTIONAL_TAILS,
    CloudflareProviderSpec,
    key_variable,
    provider_prefix,
    provider_variables,
    providers_with,
    providers_without,
)
from ..domain.control import Platform
from ..domain.errors import NotFoundError, ValidationError
from ..observability.logging import get_logger
from ..ports.control_plane import CloudflareAccount, CloudflareTokens, ControlStore
from .railway import RailwayService
from .service import ControlService, _confirm

__all__ = ["CloudflareControl", "CONSENT_FLAG", "PROVIDERS_VARIABLE", "REVOKE_ACTION"]

#: The follow-up a removal attaches (``service.ON_SUCCESS_KEY``) and the reconciler dispatches on.
REVOKE_ACTION = "cloudflare_revoke"

logger = get_logger("control.cloudflare")

#: The deliberate consent that a third party may read prompts (ADR-030).
CONSENT_FLAG = "AURA_ALLOW_HOSTED_INFERENCE"
PROVIDERS_VARIABLE = "AURA_LLM_PROVIDERS"
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_KEY_MODES = ("create", "paste", "keep")
_MAX_KEY = 8192
#: Stands in for a key's value in a dry run: the settings check never needs the real one.
_DRY_RUN_KEY = "dry-run-placeholder"
_CHAT_TASK = "Text Generation"


def _refuse(message: str, reason: str, **details: Any) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


def _names(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _whole(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


def _number(value: str | None) -> float | None:
    try:
        return float(str(value).strip()) if value not in (None, "") else None
    except ValueError:
        return None


class CloudflareControl:
    def __init__(
        self,
        *,
        control: ControlService,
        store: ControlStore,
        railway: RailwayService,
        tokens: CloudflareTokens,
        account: CloudflareAccount,
        api_root: str,
        provider_health: Callable[[], Sequence[Mapping[str, Any]]],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._control = control
        self._store = store
        self._railway = railway
        self._tokens = tokens
        self._account = account
        self._api_root = api_root.rstrip("/")
        self._provider_health = provider_health
        self._now = now

    # -- what is connected ---------------------------------------------------------------------
    async def _credential(self):
        return await self._store.get_credential(Platform.CLOUDFLARE)

    async def _gateway(self) -> str | None:
        if self._railway.self_service_id and await self._store.get_credential(Platform.RAILWAY) is not None:
            return self._railway.self_service_id
        return None

    async def _require_gateway(self) -> tuple[str, str]:
        gateway = await self._gateway()
        if gateway is None:
            raise _refuse("connect Railway first: a provider lives in the gateway's Railway variables",
                          "railway_not_connected")
        return gateway, (await self._railway.service(gateway)).name

    async def _require_account(self) -> str:
        credential = await self._credential()
        if credential is None:
            raise _refuse("connect Cloudflare first, in Settings", "cloudflare_not_connected")
        if not credential.target:
            raise _refuse("Cloudflare is connected without an account id: reconnect it with the account id",
                          "no_account")
        return credential.target

    # -- reading the gateway's providers ------------------------------------------------------
    def _is_cloudflare(self, environ: Mapping[str, str], name: str) -> bool:
        root = urlsplit(self._api_root)
        base = urlsplit(environ.get(f"{provider_prefix(name)}_BASE_URL", "") or "")
        return bool(
            root.hostname and base.scheme == "https" and base.hostname == root.hostname and "/accounts/" in base.path
        )

    @staticmethod
    def _account_of(environ: Mapping[str, str], name: str) -> str:
        path = urlsplit(environ.get(f"{provider_prefix(name)}_BASE_URL", "") or "").path
        return path.split("/accounts/", 1)[1].split("/", 1)[0] if "/accounts/" in path else ""

    def _row(self, environ: Mapping[str, str], name: str, health: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        p = provider_prefix(name)
        key_env = environ.get(f"{p}_API_KEY_ENV") or key_variable(name)
        state = health.get(name, {})
        return {
            "name": name,
            "model": environ.get(f"{p}_MODEL", ""),
            "account_id": self._account_of(environ, name),
            "enabled": (environ.get(f"{p}_ENABLED", "true") or "true").strip().lower() not in _FALSE,
            "key_env": key_env,
            "key_set": bool(environ.get(key_env)),
            "health_probe": (environ.get(f"{p}_HEALTH_PROBE") or "active").strip().lower(),
            "quality_tier": _whole(environ.get(f"{p}_QUALITY_TIER")),
            "context_window": _whole(environ.get(f"{p}_CONTEXT_WINDOW")),
            "max_output_tokens": _whole(environ.get(f"{p}_MAX_OUTPUT_TOKENS")),
            "rpm": _whole(environ.get(f"{p}_RPM")) or 0,
            "rpd": _whole(environ.get(f"{p}_RPD")) or 0,
            "tpm": _whole(environ.get(f"{p}_TPM")) or 0,
            "tpd": _whole(environ.get(f"{p}_TPD")) or 0,
            "tokens_per_second": _number(environ.get(f"{p}_TOKENS_PER_SECOND")) or 0.0,
            "json_mode": (environ.get(f"{p}_SUPPORTS_JSON_MODE") or "").strip().lower() in _TRUE,
            "vision": "image" in (environ.get(f"{p}_MODALITIES") or "").lower().split(","),
            "healthy": state.get("healthy"),
            "state": state.get("state"),
        }

    async def overview(self) -> dict[str, Any]:
        credential = await self._credential()
        gateway = await self._gateway()
        environ: Mapping[str, str] = await self._railway.environment(gateway) if gateway else {}
        health = {str(r.get("provider") or r.get("name")): r for r in self._provider_health()}
        providers = [
            self._row(environ, name, health)
            for name in _names(environ.get(PROVIDERS_VARIABLE))
            if self._is_cloudflare(environ, name)
        ]
        return {
            "connected": credential is not None,
            "account_id": credential.target if credential is not None else "",
            "api_root": self._api_root,
            "gateway_ready": gateway is not None,
            "hosted_inference_allowed": (
                (environ.get(CONSENT_FLAG) or "").strip().lower() in _TRUE if gateway else None
            ),
            "providers": providers,
        }

    # -- the catalog ----------------------------------------------------------------------------
    async def models(
        self, *, task: str = _CHAT_TASK, search: str = "", page: int = 1, per_page: int = 100
    ) -> dict[str, Any]:
        account = await self._require_account()
        result = await self._account.models(account, task=task, search=search, page=page, per_page=per_page)
        rows = []
        for m in result.models:
            chat = m.task == _CHAT_TASK
            open_weight = is_open_weight_model(m.model)
            rows.append({
                "model": m.model, "description": m.description, "task": m.task,
                "context_window": m.context_window, "vision": m.vision,
                "function_calling": m.function_calling, "reasoning": m.reasoning,
                "paid_only": m.paid_only, "price": [dict(p) for p in m.price],
                "chat": chat, "open_weight": open_weight, "can_add": chat and open_weight,
                "refusal": "" if chat and open_weight else ("not_chat" if not chat else "not_open_weight"),
            })
        return {"account_id": account, "models": rows, "page": result.page,
                "per_page": result.per_page, "total": result.total}

    # -- shared checks and application ---------------------------------------------------------
    @staticmethod
    def _key_mode(key: str, value: str | None, *, allowed: Sequence[str]) -> str:
        if key not in allowed:
            raise _refuse(f"the key is one of: {', '.join(allowed)}", "bad_key_mode", allowed=list(allowed))
        if key == "paste" and (not (value or "").strip() or len(value or "") > _MAX_KEY):
            raise _refuse("paste the key", "value_required")
        return key

    @staticmethod
    def _admit(model: str) -> None:
        if not is_open_weight_model(model):
            raise _refuse(
                f"{model} is not a recognised open-weight model; a hosted provider may only serve open-weight "
                "models (ADR-030)",
                "not_open_weight", model=model,
            )

    def _existing(self, environ: Mapping[str, str], name: str) -> dict[str, Any]:
        """The Cloudflare provider ``name``, or the reason it cannot be changed from here."""
        if name not in _names(environ.get(PROVIDERS_VARIABLE)):
            raise NotFoundError(f"the gateway has no provider named {name}")
        if not self._is_cloudflare(environ, name):
            raise _refuse(f"{name} is not a Cloudflare provider, so it cannot be changed from this tab",
                          "not_a_cloudflare_provider", provider=name)
        return self._row(environ, name, {})

    async def _dry_run(self, gateway: str, changes: Mapping[str, str | None], **extra: Any) -> dict[str, Any]:
        checked = await self._railway.validate(gateway, changes)
        return {**checked, **extra}

    async def _make_key(self, name: str, account: str) -> tuple[str, str]:
        stamp = self._now().strftime("%Y%m%d-%H%M")
        return await self._tokens.create(name=f"aura-{name}-{stamp}", account_id=account)

    async def _revoke_quietly(self, token_id: str) -> None:
        try:
            await self._tokens.revoke(token_id)
        except Exception as exc:  # the original failure is the one to report
            logger.warning("could not revoke a key made for a change that failed", extra={"error": type(exc).__name__})

    # -- add -------------------------------------------------------------------------------------
    async def add_provider(
        self, spec: CloudflareProviderSpec, *, key: str = "create", key_value: str | None = None,
        enable_consent: bool = False, actor: str, confirm: str | None, dry_run: bool = False,
    ) -> dict[str, Any]:
        _confirm(spec.name, confirm)
        mode = self._key_mode(key, key_value, allowed=("create", "paste"))
        gateway, service = await self._require_gateway()
        account = await self._require_account()
        self._admit(spec.model)
        environ = await self._railway.environment(gateway)
        listed = _names(environ.get(PROVIDERS_VARIABLE))
        if spec.name in listed:
            raise _refuse(f"{spec.name} is already a provider on the gateway", "provider_exists", provider=spec.name)
        if any(provider_prefix(n) == provider_prefix(spec.name) for n in listed):
            raise _refuse(
                f"{spec.name} would share its settings with an existing provider (names that differ only by '-' "
                "and '_' use the same variables)", "provider_name_collision", provider=spec.name,
            )
        consented = (environ.get(CONSENT_FLAG) or "").strip().lower() in _TRUE
        if not consented and not enable_consent:
            raise _refuse(
                f"hosted inference is not enabled: a third party would read prompts sent to {spec.name}. "
                "Confirm the consent to add it", "hosted_inference_not_consented",
            )
        changes: dict[str, str | None] = dict(provider_variables(spec, api_root=self._api_root, account_id=account))
        changes[PROVIDERS_VARIABLE] = providers_with(environ.get(PROVIDERS_VARIABLE), spec.name)
        if not consented:
            changes[CONSENT_FLAG] = "true"
        key_var = key_variable(spec.name)
        if dry_run:
            return await self._dry_run(
                gateway, {**changes, key_var: _DRY_RUN_KEY},
                creates_key=mode == "create", enables_consent=not consented,
            )
        token_id: str | None = None
        if mode == "create":
            token_id, value = await self._make_key(spec.name, account)
        else:
            value = (key_value or "").strip()
        changes[key_var] = value
        detail: dict[str, Any] = {
            "provider": spec.name, "model": spec.model, "key": "created" if token_id else "pasted",
            "consent_enabled": not consented,
        }
        if token_id:
            detail["cloudflare_token_id"] = token_id
        try:
            return await self._railway.apply_variables(
                gateway, changes, actor=actor, confirm=service, action="add_cloudflare_provider", extra_detail=detail,
            )
        except Exception:
            # Nothing reads the key it made: do not leave it behind.
            if token_id:
                await self._revoke_quietly(token_id)
            raise

    # -- replace ---------------------------------------------------------------------------------
    async def replace_provider(
        self, name: str, spec: CloudflareProviderSpec, *, key: str = "keep", key_value: str | None = None,
        actor: str, confirm: str | None, dry_run: bool = False,
    ) -> dict[str, Any]:
        _confirm(name, confirm)
        mode = self._key_mode(key, key_value, allowed=_KEY_MODES)
        gateway, service = await self._require_gateway()
        environ = await self._railway.environment(gateway)
        row = self._existing(environ, name)
        if spec.name != name:
            raise _refuse("a provider's name is fixed once it is added", "name_mismatch", provider=name)
        self._admit(spec.model)
        if mode == "create" and await self._credential() is None:
            raise _refuse("connect Cloudflare first, in Settings", "cloudflare_not_connected")
        p = provider_prefix(name)
        key_env = row["key_env"]
        variables = provider_variables(spec, api_root=self._api_root, account_id=row["account_id"], key_env=key_env)
        changes: dict[str, str | None] = dict(variables)
        for tail in PROVIDER_OPTIONAL_TAILS:
            variable = f"{p}_{tail}"
            if variable in environ and variable not in variables:
                changes[variable] = None  # declared before, undeclared now: delete it, never write 0
        if dry_run:
            if mode != "keep":
                changes[key_env] = _DRY_RUN_KEY
            return await self._dry_run(gateway, changes, creates_key=mode == "create")
        token_id: str | None = None
        if mode == "create":
            token_id, changes[key_env] = await self._make_key(name, row["account_id"])
        elif mode == "paste":
            changes[key_env] = (key_value or "").strip()
        detail: dict[str, Any] = {"provider": name, "model": spec.model, "key": {"keep": "kept", "create": "created", "paste": "pasted"}[mode]}
        if token_id:
            detail["cloudflare_token_id"] = token_id
        try:
            return await self._railway.apply_variables(
                gateway, changes, actor=actor, confirm=service, action="replace_cloudflare_provider", extra_detail=detail,
            )
        except Exception:
            if token_id:
                await self._revoke_quietly(token_id)
            raise

    # -- remove ----------------------------------------------------------------------------------
    def _owned_variables(self, environ: Mapping[str, str], name: str, others: Sequence[str]) -> list[str]:
        """Every variable ``name`` owns. A provider named ``cf`` must not take ``cf-old``'s settings
        with it, though they share the ``AURA_PROVIDER_CF_`` prefix, so a name that belongs to a
        longer-named provider is left alone."""
        mine = provider_prefix(name) + "_"
        longer = [provider_prefix(o) + "_" for o in others if len(provider_prefix(o)) > len(provider_prefix(name))]
        owned = [v for v in environ if v.startswith(mine) and not any(v.startswith(o) for o in longer)]
        key_env = environ.get(f"{provider_prefix(name)}_API_KEY_ENV") or ""
        if key_env and not key_env.startswith(mine) and key_env in environ:
            # A key variable the provider named itself is deleted only when no one else reads it.
            if not any(environ.get(f"{provider_prefix(o)}_API_KEY_ENV") == key_env for o in others):
                owned.append(key_env)
        return sorted(set(owned))

    async def remove_provider(
        self, name: str, *, actor: str, confirm: str | None, dry_run: bool = False,
    ) -> dict[str, Any]:
        _confirm(name, confirm)
        gateway, service = await self._require_gateway()
        environ = await self._railway.environment(gateway)
        row = self._existing(environ, name)
        current = environ.get(PROVIDERS_VARIABLE)
        try:
            remaining = providers_without(current, name)
        except ValidationError as exc:
            raise _refuse(str(exc), "last_provider", provider=name) from exc
        others = [n for n in _names(current) if n != name]
        changes: dict[str, str | None] = {PROVIDERS_VARIABLE: remaining}
        changes.update({variable: None for variable in self._owned_variables(environ, name, others)})
        revoke_token_id = await self._token_to_revoke(environ, row, others)
        if dry_run:
            return await self._dry_run(gateway, changes, revoke_token_id=revoke_token_id)
        return await self._railway.apply_variables(
            gateway, changes, actor=actor, confirm=service, action="remove_cloudflare_provider",
            extra_detail={"provider": name, "model": row["model"], "revoke_token_id": revoke_token_id},
            on_success=(
                {"action": REVOKE_ACTION, "token_id": revoke_token_id, "provider": name}
                if revoke_token_id else None
            ),
        )

    async def _token_to_revoke(self, environ: Mapping[str, str], row: Mapping[str, Any], others: Sequence[str]) -> str | None:
        """The Cloudflare token the removed provider's key is, unless something else uses that key
        or Cloudflare does not know it as a token the console manages."""
        value = environ.get(row["key_env"]) or ""
        if not value:
            return None
        for other in others:
            other_env = environ.get(f"{provider_prefix(other)}_API_KEY_ENV") or key_variable(other)
            if environ.get(other_env) == value:
                return None
        return await self._tokens.identify(value)

    async def revoke_after_success(self, note: Mapping[str, Any]) -> str:
        """The reconciler's follow-up for a removed provider: revoke its Cloudflare token.

        Run once the removal has stayed healthy for the whole watch. It re-checks, now, that no
        Cloudflare provider on the gateway still reads that token (something may have taken the key
        in the meantime), and it leaves the token alone whenever it cannot check. Only Cloudflare
        providers' keys are ever sent to Cloudflare to be identified. An already revoked token counts
        as done. Returns a sentence for the change's journal; raises on a failure, which the
        reconciler records without undoing the change.
        """
        token_id = str(note.get("token_id") or "")
        if not token_id:
            return "no Cloudflare token was named for revoking"
        gateway = await self._gateway()
        if gateway is None:
            return f"left Cloudflare token {token_id} active: Railway is not connected, so use could not be checked"
        environ = await self._railway.environment(gateway)
        for name in _names(environ.get(PROVIDERS_VARIABLE)):
            if not self._is_cloudflare(environ, name):
                continue
            key = environ.get(environ.get(f"{provider_prefix(name)}_API_KEY_ENV") or key_variable(name)) or ""
            if key and await self._tokens.identify(key) == token_id:
                return f"left Cloudflare token {token_id} active: provider {name} still uses it"
        try:
            await self._tokens.revoke(token_id)
        except NotFoundError:
            return f"Cloudflare token {token_id} was already revoked"
        return f"revoked Cloudflare token {token_id}"
