"""Deferred (ADR-036) -- deliberately NOT registered as a tool. Playwright with no SSRF
policy cannot run in the gateway. The home for this is a ``browser.run`` RPC in ``aura_hostd``
(not built).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ._net import http_url_error


class BrowserSubagent:
    """Browser automation subagent interface backed by Playwright when available, with fallback HTTP diagnostics."""

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
    def check_url_accessibility(cls, url: str, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """Perform lightweight HTTP GET probe to verify destination responsiveness before launching browser."""
        problem = http_url_error(url)
        if problem:
            return {"accessible": False, "status_code": 0, "error": problem, "url": url}
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AURA-Browser/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                code = resp.getcode()
                return {
                    "accessible": 200 <= code < 400,
                    "status_code": code,
                    "url": url,
                    "content_type": resp.headers.get("Content-Type", ""),
                }
        except urllib.error.HTTPError as exc:
            return {"accessible": False, "status_code": exc.code, "error": str(exc), "url": url}
        except Exception as exc:
            return {"accessible": False, "status_code": 0, "error": f"{type(exc).__name__}: {exc}", "url": url}

    @classmethod
    def run_actions(
        cls,
        url: str,
        actions: Optional[List[Dict[str, Any]]] = None,
        headless: bool = True,
        screenshot_path: Optional[str] = None,
        timeout_ms: int = 15000,
    ) -> Dict[str, Any]:
        """Navigate to a URL and execute browser actions (click, fill, wait_for) using Playwright."""
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
        action_list = actions or []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(timeout_ms)

                page.goto(url, wait_until="domcontentloaded")
                steps_log.append({"action": "goto", "url": url, "status": "ok"})

                for act in action_list:
                    kind = act.get("type", "")
                    selector = act.get("selector", "")
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
                        steps_log.append({"action": kind, "status": "skipped"})

                if screenshot_path:
                    page.screenshot(path=screenshot_path, full_page=True)
                    steps_log.append({"action": "screenshot", "path": screenshot_path, "status": "ok"})

                title = page.title()
                current_url = page.url
                browser.close()

            return {
                "executed": True,
                "url": url,
                "final_url": current_url,
                "title": title,
                "screenshot_path": screenshot_path,
                "steps": steps_log,
            }
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
    ) -> Dict[str, Any]:
        """Capture screenshot of a web page using specified viewport dimensions."""
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"], "output_path": output_path}

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(viewport={"width": viewport_width, "height": viewport_height})
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=20000)
                page.screenshot(path=output_path, full_page=full_page)
                title = page.title()
                browser.close()

            return {
                "success": True,
                "url": url,
                "output_path": output_path,
                "title": title,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "output_path": output_path}

    @classmethod
    def extract_page_content(
        cls,
        url: str,
        selector: Optional[str] = None,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """Fetch rendered text content or innerHTML from a live web page."""
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"]}

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")

                if selector:
                    elem = page.query_selector(selector)
                    text = elem.inner_text() if elem else ""
                else:
                    text = page.inner_text("body")

                title = page.title()
                browser.close()

            return {"success": True, "url": url, "title": title, "content": text}
        except Exception as exc:
            return {"success": False, "error": str(exc), "url": url}

    @classmethod
    def run_script_in_page(
        cls,
        url: str,
        script_js: str,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """Evaluate a custom JavaScript expression inside page DOM and return output."""
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {"success": False, "error": avail["error"]}

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")
                result = page.evaluate(script_js)
                browser.close()

            return {"success": True, "result": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
