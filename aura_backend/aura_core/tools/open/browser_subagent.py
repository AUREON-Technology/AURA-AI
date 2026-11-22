import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ._ops_common import PathRefused, UrlRefused, check_url, confine, open_guarded

#: Image formats Playwright's ``screenshot`` can write. Anything else (``.bashrc``, ``.py``, no
#: extension) is refused so a "screenshot path" cannot be used to overwrite an arbitrary file.
_SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg")
_MAX_ACTIONS = 50
_MAX_SCRIPT_CHARS = 20_000
_MAX_CONTENT_CHARS = 200_000
_ACTION_KINDS = ("click", "fill", "wait_for")


def _clamp_ms(value: Any, default: int = 15_000, low: int = 1_000, high: int = 120_000) -> int:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(ms, low), high)


def _screenshot_target(path: Any, base_dir: Optional[str]) -> str:
    """Validate and resolve a screenshot destination. Raises PathRefused."""
    if not isinstance(path, str):
        raise PathRefused("The screenshot path must be a string.")
    resolved = confine(path, base_dir, must_exist=False)
    if resolved.suffix.lower() not in _SCREENSHOT_SUFFIXES:
        raise PathRefused(f"The screenshot path must end in one of {', '.join(_SCREENSHOT_SUFFIXES)}.")
    if resolved.exists() and not resolved.is_file():
        raise PathRefused("The screenshot path is not a file.")
    if not resolved.parent.is_dir():
        raise PathRefused("The screenshot directory does not exist.")
    return str(resolved)


def _route_guard(allow_local: bool):
    """A Playwright route handler that applies the outbound-URL policy to *every* request.

    Checking only the first URL is not enough: a page can redirect, load sub-resources and call
    ``fetch`` against internal hosts. Anything the policy refuses is aborted.
    """

    def handler(route: Any) -> None:
        try:
            check_url(route.request.url, allow_loopback=allow_local, allow_private=allow_local)
        except UrlRefused:
            route.abort()
        else:
            route.continue_()

    return handler


class BrowserSubagent:
    """Browser automation subagent interface backed by Playwright when available, with fallback HTTP diagnostics.

    Safety properties (ADR-036 marks this class *Deferred* because it had no SSRF policy):

    * only ``http``/``https`` URLs on **public** hosts by default; ``allow_local=True`` (a keyword on
      every browsing method) adds loopback and private ranges. Link-local and cloud-metadata
      addresses are never reachable. In the browser the policy is enforced on every request through a
      route guard, not just on the first URL;
    * screenshots may only be written to an image path (optionally under ``base_dir``);
    * the browser is always closed, and an unknown or incomplete action is reported rather than
      silently skipped while ``executed`` says True.

    Known limit: this still drives a full browser inside the gateway process; DNS is resolved by the
    policy check and again by the browser (rebinding). The proper home is an ``aura_hostd`` RPC.
    Playwright is optional; without it every browsing method returns a clear error, never a fake
    success. The class reads no local files; a PDF *URL* is not fetched as a document here.
    """

    @staticmethod
    def check_playwright_availability() -> Dict[str, Any]:
        """Check whether Playwright Python package is installed in the environment."""
        try:
            import playwright  # noqa: F401
            return {"available": True, "package": "playwright"}
        except ImportError:
            return {
                "available": False,
                "package": "playwright",
                "error": "Playwright is not installed. Install with: pip install playwright",
            }

    @classmethod
    def check_url_accessibility(
        cls, url: str, timeout_seconds: float = 5.0, *, allow_local: bool = False
    ) -> Dict[str, Any]:
        """Perform lightweight HTTP GET probe to verify destination responsiveness before launching browser."""
        shown = url if isinstance(url, str) and len(url) <= 200 else None
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AURA-Browser/1.0"},
            )
            with open_guarded(req, timeout=timeout_seconds, allow_loopback=allow_local,
                              allow_private=allow_local) as resp:
                code = resp.getcode()
                if not isinstance(code, int):
                    return {"accessible": False, "status_code": 0, "url": shown,
                            "error": "The URL did not return an HTTP status."}
                return {
                    "accessible": 200 <= code < 400,
                    "status_code": code,
                    "url": shown,
                    "content_type": resp.headers.get("Content-Type", ""),
                }
        except UrlRefused as exc:
            return {"accessible": False, "status_code": 0, "error": f"URL refused: {exc}", "url": shown}
        except urllib.error.HTTPError as exc:
            return {"accessible": False, "status_code": exc.code, "error": str(exc), "url": shown}
        except Exception as exc:
            return {"accessible": False, "status_code": 0, "error": f"{type(exc).__name__}: {exc}", "url": shown}

    @classmethod
    def run_actions(
        cls,
        url: str,
        actions: Optional[List[Dict[str, Any]]] = None,
        headless: bool = True,
        screenshot_path: Optional[str] = None,
        timeout_ms: int = 15000,
        *,
        allow_local: bool = False,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Navigate to a URL and execute browser actions (click, fill, wait_for) using Playwright.

        ``executed`` is True only when the page loaded **and** every requested action was carried
        out; skipped actions are counted in ``skipped_actions`` and explained in ``error``.
        """
        try:
            check_url(url, allow_loopback=allow_local, allow_private=allow_local)
        except UrlRefused as exc:
            return {"executed": False, "url": url if isinstance(url, str) and len(url) <= 200 else None,
                    "error": f"URL refused: {exc}", "steps": []}

        action_list = actions if actions is not None else []
        if not isinstance(action_list, list) or not all(isinstance(a, dict) for a in action_list):
            return {"executed": False, "url": url, "error": "actions must be a list of objects.", "steps": []}
        if len(action_list) > _MAX_ACTIONS:
            return {"executed": False, "url": url, "error": f"Too many actions (limit {_MAX_ACTIONS}).", "steps": []}
        try:
            shot = _screenshot_target(screenshot_path, base_dir) if screenshot_path else None
        except PathRefused as exc:
            return {"executed": False, "url": url, "error": str(exc), "steps": []}

        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {
                "executed": False,
                "url": url,
                "error": avail["error"],
                "steps": [],
            }

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"executed": False, "url": url, "error": "Playwright sync_api import failed", "steps": []}

        steps_log: List[Dict[str, Any]] = []
        skipped = 0

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(headless))
                try:
                    context = browser.new_context()
                    context.route("**/*", _route_guard(allow_local))
                    page = context.new_page()
                    page.set_default_timeout(_clamp_ms(timeout_ms))

                    page.goto(url, wait_until="domcontentloaded")
                    steps_log.append({"action": "goto", "url": url, "status": "ok"})

                    for act in action_list:
                        kind = act.get("type", "")
                        selector = act.get("selector", "")
                        if not isinstance(selector, str):
                            selector = ""
                        if kind == "click" and selector:
                            page.click(selector)
                            steps_log.append({"action": "click", "selector": selector, "status": "ok"})
                        elif kind == "fill" and selector:
                            val = str(act.get("value", ""))
                            page.fill(selector, val)
                            steps_log.append({"action": "fill", "selector": selector, "status": "ok"})
                        elif kind == "wait_for" and selector:
                            page.wait_for_selector(selector)
                            steps_log.append({"action": "wait_for", "selector": selector, "status": "ok"})
                        else:
                            skipped += 1
                            steps_log.append({"action": str(kind), "status": "skipped"})

                    if shot:
                        page.screenshot(path=shot, full_page=True)
                        steps_log.append({"action": "screenshot", "path": shot, "status": "ok"})

                    title = page.title()
                    current_url = page.url
                finally:
                    browser.close()

            result: Dict[str, Any] = {
                "executed": skipped == 0,
                "url": url,
                "final_url": current_url,
                "title": title,
                "screenshot_path": shot,
                "skipped_actions": skipped,
                "steps": steps_log,
            }
            if skipped:
                result["error"] = (
                    f"{skipped} action(s) were skipped (unknown type {_ACTION_KINDS!r} or missing "
                    "selector); the page was not driven as requested."
                )
            return result
        except Exception as exc:
            return {
                "executed": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "steps": steps_log,
            }

    @classmethod
    def navigate_and_screenshot(
        cls,
        url: str,
        output_path: str,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        full_page: bool = True,
        headless: bool = True,
        *,
        allow_local: bool = False,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture screenshot of a web page using specified viewport dimensions."""
        try:
            check_url(url, allow_loopback=allow_local, allow_private=allow_local)
            target = _screenshot_target(output_path, base_dir)
        except UrlRefused as exc:
            return {"success": False, "error": f"URL refused: {exc}", "output_path": None}
        except PathRefused as exc:
            return {"success": False, "error": str(exc), "output_path": None}

        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"], "output_path": output_path}

        width = _clamp_ms(viewport_width, 1280, 100, 4000)
        height = _clamp_ms(viewport_height, 720, 100, 4000)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(headless))
                try:
                    context = browser.new_context(viewport={"width": width, "height": height})
                    context.route("**/*", _route_guard(allow_local))
                    page = context.new_page()
                    page.goto(url, wait_until="networkidle", timeout=20000)
                    page.screenshot(path=target, full_page=bool(full_page))
                    title = page.title()
                finally:
                    browser.close()

            return {
                "success": True,
                "url": url,
                "output_path": target,
                "title": title,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "output_path": target}

    @classmethod
    def extract_page_content(
        cls,
        url: str,
        selector: Optional[str] = None,
        headless: bool = True,
        *,
        allow_local: bool = False,
    ) -> Dict[str, Any]:
        """Fetch rendered text content or innerHTML from a live web page (at most 200,000 characters)."""
        try:
            check_url(url, allow_loopback=allow_local, allow_private=allow_local)
        except UrlRefused as exc:
            return {"success": False, "error": f"URL refused: {exc}", "url": None}
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"]}

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(headless))
                try:
                    context = browser.new_context()
                    context.route("**/*", _route_guard(allow_local))
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded")

                    if selector:
                        elem = page.query_selector(selector)
                        if elem is None:
                            return {"success": False, "url": url,
                                    "error": f"Selector matched no element: {selector!r}"}
                        text = elem.inner_text()
                    else:
                        text = page.inner_text("body")

                    title = page.title()
                finally:
                    browser.close()

            truncated = len(text) > _MAX_CONTENT_CHARS
            return {"success": True, "url": url, "title": title, "content": text[:_MAX_CONTENT_CHARS],
                    "truncated": truncated}
        except Exception as exc:
            return {"success": False, "error": str(exc), "url": url}

    @classmethod
    def run_script_in_page(
        cls,
        url: str,
        script_js: str,
        headless: bool = True,
        *,
        allow_local: bool = False,
    ) -> Dict[str, Any]:
        """Evaluate a custom JavaScript expression inside page DOM and return output."""
        if not isinstance(script_js, str) or not script_js.strip():
            return {"success": False, "error": "script_js must be a non-empty string."}
        if len(script_js) > _MAX_SCRIPT_CHARS:
            return {"success": False, "error": f"script_js is too long (limit {_MAX_SCRIPT_CHARS} characters)."}
        try:
            check_url(url, allow_loopback=allow_local, allow_private=allow_local)
        except UrlRefused as exc:
            return {"success": False, "error": f"URL refused: {exc}"}
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"]}

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(headless))
                try:
                    context = browser.new_context()
                    context.route("**/*", _route_guard(allow_local))
                    page = context.new_page()
                    page.goto(url, wait_until="domcontentloaded")
                    result = page.evaluate(script_js)
                finally:
                    browser.close()

            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
