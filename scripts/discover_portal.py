"""Opt-in portal discovery (developer tool, run via discover_portal.bat).

Captures network responses and page HTML from the Synty Store / Sky Pilot portal into
`.data/discovery/` so parsers can be updated when Synty changes its markup.

WARNING: the captures contain personal data (names, order data, download links that embed
the account email). Delete `.data/discovery/` when done, and only commit sanitized fixtures
(see tests/fixtures/README.md).

This script does NOT write the app session. Use scripts/login.py (the dashboard's
"Login to Synty Store" button) for that.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import async_playwright  # noqa: E402

from browser_launch import DATA_DIR, launch_context  # noqa: E402

DISCOVERY_DIR = DATA_DIR / "discovery"
_URL_KEYWORDS = (
    "syntystore", "download", "skypilot", "sendowl", "fileflare", "account",
    "apps", "graphql", "api", "orders", "customer", "shopify",
)


async def run_discovery() -> None:
    DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
    captured_requests: list[dict] = []
    html_snapshots: list[dict] = []

    print("Synty Store portal DISCOVERY (developer tool)", flush=True)
    print("  Browse My Downloads, open a bundle and page 2, then close the window.", flush=True)
    print(f"  Captures go to {DISCOVERY_DIR} and contain personal data.", flush=True)

    def _flush() -> None:
        (DISCOVERY_DIR / "network_log.json").write_text(
            json.dumps(captured_requests, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (DISCOVERY_DIR / "pages_log.json").write_text(
            json.dumps(html_snapshots, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    async with async_playwright() as p:
        context = await launch_context(p)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.bring_to_front()

        async def on_response(response):
            try:
                url = response.url
                if not any(k in url.lower() for k in _URL_KEYWORDS):
                    return
                content_type = response.headers.get("content-type", "")
                entry = {
                    "url": url,
                    "status": response.status,
                    "method": response.request.method,
                    "content_type": content_type,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if "application/json" in content_type or "text/html" in content_type:
                    try:
                        text = await response.text()
                        if len(text) < 2_000_000:
                            entry["body_preview"] = text[:150000]
                    except Exception:
                        pass
                captured_requests.append(entry)
            except Exception:
                pass

        async def on_load(p_obj):
            try:
                await asyncio.sleep(1.5)
                html_snapshots.append(
                    {
                        "url": p_obj.url,
                        "title": await p_obj.title(),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "html": await p_obj.content(),
                    }
                )
                _flush()
                print(f"[Captured Page] {p_obj.url}", flush=True)
            except Exception:
                pass

        page.on("response", on_response)
        page.on("load", lambda: asyncio.create_task(on_load(page)))

        await page.goto("https://syntystore.com/apps/downloads/orders")
        try:
            await page.wait_for_event("close", timeout=600_000)
        except Exception:
            pass
        try:
            await context.close()
        except Exception:
            pass

    _flush()
    print(
        f"Discovery complete: {len(captured_requests)} responses, {len(html_snapshots)} pages in {DISCOVERY_DIR}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(run_discovery())
