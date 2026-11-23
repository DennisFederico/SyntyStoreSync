import asyncio
import json
import subprocess
import threading
import time
from pathlib import Path

import app.auth as auth_mod
from app.auth import SESSION_VERSION, AuthService, build_session, session_from_storage_state, write_session_file

LEGACY_COOKIES = [
    {"domain": "syntystore.com", "name": "_shopify_essential", "value": "store", "expires": time.time() + 86400},
    {"domain": "account.syntystore.com", "name": "_shopify_essential", "value": "account", "expires": 1.0},
    {"domain": "syntystore.com", "name": "localization", "value": "ES"},
    {"domain": ".bing.com", "name": ".MSA.Auth", "value": "microsoft"},
]
CID_ORIGINS = [
    {"origin": "https://account.syntystore.com", "localStorage": [{"name": "x", "value": "shop-cid:123456789"}]}
]


def _legacy(path: Path, cookies=LEGACY_COOKIES, origins=None) -> Path:
    path.write_text(json.dumps({"cookies": cookies, "origins": origins or []}), encoding="utf-8")
    return path


def _minimal(path: Path, **overrides) -> Path:
    session = build_session("123456789", {"_shopify_essential": "store"}, time.time() + 86400)
    session.update(overrides)
    write_session_file(path, session)
    return path


# ---- legacy storage_state compatibility ---------------------------------------------------


def test_legacy_anonymous_cookies_are_not_a_session(tmp_path: Path):
    svc = AuthService(_legacy(tmp_path / "s.json"))
    assert svc.get_customer_id() is None
    assert svc.session_status()["state"] == "none"
    assert svc.has_valid_session() is False


def test_legacy_file_is_reduced_to_the_allowlist(tmp_path: Path):
    svc = AuthService(_legacy(tmp_path / "s.json", origins=CID_ORIGINS))
    assert svc.get_customer_id() == "123456789"
    # Only the storefront _shopify_essential survives: no account-domain or Microsoft cookies.
    assert svc.get_cookies() == {"_shopify_essential": "store"}
    assert svc.session_status()["state"] == "saved"


def test_migrate_legacy_file_rewrites_minimal_schema(tmp_path: Path):
    path = _legacy(tmp_path / "s.json", origins=CID_ORIGINS)
    svc = AuthService(path)
    assert svc.migrate_legacy_file() is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == SESSION_VERSION
    assert data["cookies"] == {"_shopify_essential": "store"}
    assert ".MSA.Auth" not in path.read_text(encoding="utf-8")
    assert svc.migrate_legacy_file() is False  # idempotent


def test_storage_state_without_storefront_cookie_is_rejected():
    data = {"cookies": [{"domain": "account.syntystore.com", "name": "_shopify_essential", "value": "a"}]}
    assert session_from_storage_state(data, '"-cid:1234567"') is None


# ---- three-state status (no network) ------------------------------------------------------


def test_missing_session_file_is_not_valid(tmp_path: Path):
    svc = AuthService(tmp_path / "missing.json")
    assert svc.get_cookies() == {}
    assert svc.session_status() == {"state": "none"}


def test_status_moves_saved_verified_expired(tmp_path: Path):
    svc = AuthService(_minimal(tmp_path / "s.json"))
    assert svc.session_status()["state"] == "saved"
    svc.mark_verified()
    status = svc.session_status()
    assert status["state"] == "verified" and status["verified_at"]
    svc.mark_expired()
    assert svc.session_status()["state"] == "expired"
    assert svc.has_valid_session() is False
    svc.mark_verified()  # a later successful crawl clears the flag
    assert svc.session_status()["state"] == "verified"


def test_cookie_past_expiry_shows_expired(tmp_path: Path):
    svc = AuthService(_minimal(tmp_path / "s.json", cookie_expires_at=time.time() - 60))
    assert svc.session_status()["state"] == "expired"


# ---- interactive login launch -------------------------------------------------------------


class _FakePopen:
    instances: list["_FakePopen"] = []

    def __init__(self, args, cwd=None, creationflags=0):
        self.args = args
        self.cwd = cwd
        self.creationflags = creationflags
        self._done = False
        _FakePopen.instances.append(self)

    def poll(self):
        return 0 if self._done else None

    def wait(self):
        self._done = True
        return 0


def test_login_launch_works_on_selector_loop(tmp_path: Path, monkeypatch):
    """uvicorn --reload on Windows uses SelectorEventLoop, which cannot run
    asyncio.create_subprocess_exec. The launcher must still work there."""
    _FakePopen.instances = []
    monkeypatch.setattr(auth_mod.subprocess, "Popen", _FakePopen)
    svc = AuthService(_minimal(tmp_path / "s.json"))

    result = asyncio.run(svc.launch_interactive_login(), loop_factory=asyncio.SelectorEventLoop)

    assert result is True
    assert len(_FakePopen.instances) == 1
    proc = _FakePopen.instances[0]
    assert proc.args[-1].endswith("login.py")
    assert proc.creationflags == getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def test_concurrent_login_clicks_share_one_browser(tmp_path: Path, monkeypatch):
    _FakePopen.instances = []
    browser_closed = threading.Event()

    class _BlockingPopen(_FakePopen):
        def wait(self):
            browser_closed.wait(timeout=5)
            return super().wait()

    monkeypatch.setattr(auth_mod.subprocess, "Popen", _BlockingPopen)
    svc = AuthService(_minimal(tmp_path / "s.json"))

    async def two_clicks():
        first = asyncio.create_task(svc.launch_interactive_login())
        await asyncio.sleep(0.05)  # browser window is open
        second = asyncio.create_task(svc.launch_interactive_login())
        await asyncio.sleep(0.05)
        browser_closed.set()  # user closes the browser
        await asyncio.gather(first, second)

    asyncio.run(two_clicks(), loop_factory=asyncio.SelectorEventLoop)
    assert len(_FakePopen.instances) == 1
