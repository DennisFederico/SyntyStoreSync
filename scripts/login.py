"""Guided Synty Store login (dec-001).

Opens a visible Chrome window, waits until Sky Pilot "My Downloads" shows the customer's library,
saves only the minimal session (customer id + storefront `_shopify_essential`) to
`.data/synty_session.json`, and closes the window automatically.

Exit codes: 0 = session saved, 2 = window closed before login completed, 3 = timed out.
No network traffic or page content is logged.
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from app.auth import ALLOWED_COOKIES, SESSION_FILE, STOREFRONT_DOMAINS, build_session, write_session_file  # noqa: E402
from browser_launch import launch_context  # noqa: E402

STORE = "https://syntystore.com"
DOWNLOADS_URL = f"{STORE}/apps/downloads/orders"
LOGIN_URL = f"{STORE}/account"
_LIBRARY_RE = re.compile(r"^https://syntystore\.com/apps/downloads/orders/(\d+)")
_ACCOUNT_HOME_RE = re.compile(r"^https://account\.syntystore\.com/(orders|profile)")
TIMEOUT_S = 600
POLL_S = 1.0


async def _goto(page, url: str) -> None:
    try:
        await page.goto(url)
    except Exception:
        pass  # navigation interrupted (user clicked, redirect, window closing): keep polling


async def _capture(context, customer_id: str) -> bool:
    cookies = await context.cookies(STORE)
    picked = {
        c["name"]: c
        for c in cookies
        if c["name"] in ALLOWED_COOKIES and c.get("domain", "") in STOREFRONT_DOMAINS
    }
    if not picked:
        return False
    expires = [c.get("expires") for c in picked.values() if (c.get("expires") or 0) > 0]
    session = build_session(
        customer_id,
        {name: c["value"] for name, c in picked.items()},
        min(expires) if expires else None,
    )
    write_session_file(SESSION_FILE, session)
    return True


async def run_login() -> int:
    print("Synty Store login", flush=True)
    print("  Sign in in the browser window (email code or 'Continue with Shop').", flush=True)
    print("  The window closes by itself once My Downloads has loaded.", flush=True)
    print("  If it stays open after signing in, open 'My Downloads' from the account menu.", flush=True)

    async with async_playwright() as p:
        context = await launch_context(p)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.bring_to_front()

        closed = asyncio.Event()
        context.on("close", lambda *_: closed.set())
        page.on("close", lambda *_: closed.set())

        await _goto(page, DOWNLOADS_URL)
        sent_to_login = False
        sent_to_downloads_at = 0.0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT_S

        try:
            while loop.time() < deadline:
                if closed.is_set():
                    print("Browser closed before login completed. Nothing saved.", flush=True)
                    return 2
                url = page.url
                m = _LIBRARY_RE.match(url)
                if m:
                    try:
                        has_list = await page.locator(".sky-pilot-files-list").count() > 0
                    except Exception:
                        has_list = False
                    if has_list and await _capture(context, m.group(1)):
                        print(f"Session saved to {SESSION_FILE}. Closing browser.", flush=True)
                        return 0
                elif url.split("?")[0].rstrip("/") == DOWNLOADS_URL and not sent_to_login:
                    # Anonymous My Downloads page: start the Shopify sign-in flow.
                    sent_to_login = True
                    await _goto(page, LOGIN_URL)
                elif _ACCOUNT_HOME_RE.match(url) and loop.time() - sent_to_downloads_at > 10:
                    # Signed in on the account site: go back to My Downloads to bind the storefront session.
                    sent_to_downloads_at = loop.time()
                    await _goto(page, DOWNLOADS_URL)
                try:
                    await asyncio.wait_for(closed.wait(), timeout=POLL_S)
                except asyncio.TimeoutError:
                    pass
            print("Timed out waiting for login. Nothing saved.", flush=True)
            return 3
        finally:
            try:
                await context.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(run_login()))
