from typing import Any, Dict, List, Optional


class BrowserSubagent:
    """Browser automation subagent interface backed by Playwright when available."""

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
    def run_actions(
        cls,
        url: str,
        actions: Optional[List[Dict[str, Any]]] = None,
        headless: bool = True,
        screenshot_path: Optional[str] = None,
        timeout_ms: int = 15000,
    ) -> Dict[str, Any]:
        """
        Navigate to a URL and execute browser actions (click, fill, wait_for) using Playwright.
        Returns structured step logs and optional screenshot path.
        """
        avail = cls.check_playwright_availability()
        if not avail["available"]:
            return {
                "executed": False,
                "url": url,
                "error": avail["error"],
                "steps": [],
            }

        from playwright.sync_api import sync_playwright

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