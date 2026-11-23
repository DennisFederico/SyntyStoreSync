"""Synty Store session handling (dec-001).

The app never handles credentials. The user signs in inside a real Chrome window
(scripts/login.py). Only a minimal session is persisted to `.data/synty_session.json`:

    {
      "version": 2,
      "customer_id": "<numeric id>",
      "captured_at": "<iso utc>",
      "cookie_expires_at": <unix ts or null>,
      "verified_at": "<iso utc or null>",   # set after a successful catalog crawl
      "expired": false,                     # set when Synty rejects the session
      "cookies": {"_shopify_essential": "<value>"}
    }

Legacy Playwright `storage_state` files (version 1) are still read, and reduced to the same
allowlist on load.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / ".data"
SESSION_FILE = DATA_DIR / "synty_session.json"
LOGIN_SCRIPT = PROJECT_ROOT / "scripts" / "login.py"

SESSION_VERSION = 2
# The Sky Pilot app proxy (/apps/downloads/...) authenticates with this storefront cookie alone
# (verified 2026-11-22: library page 1 returns all bundles with only this cookie).
ALLOWED_COOKIES = ("_shopify_essential",)
STOREFRONT_DOMAINS = {"syntystore.com", ".syntystore.com"}

_CID_PATTERNS = [
    re.compile(r"-cid:(\d+)"),
    re.compile(r"/apps/downloads/(?:orders|customers)/(\d+)"),
    re.compile(r'"logged_in_customer_id"\s*:\s*"(\d+)"'),
    re.compile(r'"sub"\s*:\s*(\d+)'),
]


class SessionExpiredError(RuntimeError):
    """Synty Store did not accept the saved session."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_session(
    customer_id: str, cookies: dict[str, str], cookie_expires_at: float | None
) -> dict:
    return {
        "version": SESSION_VERSION,
        "customer_id": customer_id,
        "captured_at": _now_iso(),
        "cookie_expires_at": cookie_expires_at,
        "verified_at": None,
        "expired": False,
        "cookies": {k: v for k, v in cookies.items() if k in ALLOWED_COOKIES},
    }


def session_from_storage_state(data: dict, raw_text: str = "") -> dict | None:
    """Reduce a legacy Playwright storage_state (or a live context's cookies) to the minimal schema."""
    cookies: dict[str, str] = {}
    expires_at: float | None = None
    for c in data.get("cookies", []):
        if c.get("name") in ALLOWED_COOKIES and c.get("domain", "") in STOREFRONT_DOMAINS:
            cookies[c["name"]] = c.get("value", "")
            exp = c.get("expires")
            if isinstance(exp, (int, float)) and exp > 0:
                expires_at = float(exp)
    customer_id = None
    for pattern in _CID_PATTERNS:
        m = pattern.search(raw_text or json.dumps(data))
        if m:
            customer_id = m.group(1)
            break
    if not cookies or not customer_id:
        return None
    return build_session(customer_id, cookies, expires_at)


def write_session_file(path: Path, session: dict) -> None:
    """Atomic write so a crash never leaves a half-written session file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".session-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class AuthService:
    def __init__(self, session_file: Path = SESSION_FILE) -> None:
        self.session_file = session_file
        self._login_proc: subprocess.Popen | None = None

    # ---- reading -------------------------------------------------------------------------

    def _load(self) -> dict | None:
        if not self.session_file.exists():
            return None
        try:
            raw = self.session_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception:
            return None
        if data.get("version") == SESSION_VERSION:
            return data
        return session_from_storage_state(data, raw)

    def get_cookies(self) -> dict[str, str]:
        data = self._load()
        return dict(data.get("cookies", {})) if data else {}

    def get_customer_id(self) -> str | None:
        data = self._load()
        return data.get("customer_id") if data else None

    def session_status(self) -> dict:
        """Local-only view of the session (no network, AGENTS.md invariant 1).

        state: "none" (login required), "expired" (rejected by Synty or cookie past expiry),
        "saved" (present, not yet confirmed by a crawl) or "verified" (a crawl succeeded).
        """
        data = self._load()
        if not data or not data.get("cookies") or not data.get("customer_id"):
            return {"state": "none"}
        info = {
            "captured_at": data.get("captured_at"),
            "cookie_expires_at": data.get("cookie_expires_at"),
            "verified_at": data.get("verified_at"),
        }
        exp = data.get("cookie_expires_at")
        if data.get("expired") or (exp and exp < time.time()):
            return {"state": "expired", **info}
        return {"state": "verified" if data.get("verified_at") else "saved", **info}

    def has_valid_session(self) -> bool:
        return self.session_status()["state"] in ("saved", "verified")

    # ---- writing -------------------------------------------------------------------------

    def _update(self, **fields) -> None:
        data = self._load()
        if not data:
            return
        data.update(fields)
        write_session_file(self.session_file, data)

    def mark_verified(self) -> None:
        self._update(verified_at=_now_iso(), expired=False)

    def mark_expired(self) -> None:
        self._update(expired=True)

    def migrate_legacy_file(self) -> bool:
        """Rewrite a legacy storage_state file in the minimal schema. Returns True if rewritten."""
        if not self.session_file.exists():
            return False
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        if data.get("version") == SESSION_VERSION:
            return False
        session = self._load()
        if not session:
            return False
        write_session_file(self.session_file, session)
        return True

    # ---- interactive login ---------------------------------------------------------------

    async def launch_interactive_login(self) -> bool:
        """Launches scripts/login.py in a visible console window and waits for it to exit.

        Uses subprocess.Popen instead of asyncio.create_subprocess_exec: under `uvicorn --reload`
        on Windows the server runs on a SelectorEventLoop, which cannot spawn subprocesses
        (NotImplementedError). A second click while the browser is open waits on the same
        process instead of starting another browser on the locked persistent profile.
        """
        if self._login_proc is None or self._login_proc.poll() is not None:
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            self._login_proc = subprocess.Popen(
                [sys.executable, str(LOGIN_SCRIPT)],
                cwd=str(PROJECT_ROOT),
                creationflags=creationflags,
            )
        proc = self._login_proc
        await asyncio.to_thread(proc.wait)
        return self.has_valid_session()
