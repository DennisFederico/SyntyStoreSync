"""Shared Playwright launcher for scripts/login.py and scripts/discover_portal.py."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / ".data"
# Persistent Chrome-channel profile (dec-001, option b): keeps the Shopify account session so
# later logins usually need no email/code. Never read by the app itself.
PROFILE_DIR = DATA_DIR / "chrome_profile"

# Dennis uses Google Chrome. Fall back to Edge, then to Playwright's bundled Chromium.
CHANNELS = ("chrome", "msedge", None)


async def launch_context(playwright, profile_dir: Path = PROFILE_DIR):
    profile_dir.mkdir(parents=True, exist_ok=True)
    last_exc: Exception | None = None
    for channel in CHANNELS:
        kwargs = dict(
            user_data_dir=str(profile_dir),
            headless=False,
            args=["--start-maximized"],
            no_viewport=True,
            accept_downloads=False,
        )
        if channel:
            kwargs["channel"] = channel
        try:
            context = await playwright.chromium.launch_persistent_context(**kwargs)
            print(f"Opened browser: {channel or 'bundled chromium'}", flush=True)
            return context
        except Exception as exc:  # channel not installed, profile locked, ...
            last_exc = exc
    raise RuntimeError(f"Could not launch a browser: {last_exc}")
