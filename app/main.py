import asyncio
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from app.auth import DATA_DIR, AuthService, SessionExpiredError
from app.catalog import BASE_STORE_URL, CatalogService
from app.downloader import DownloadLinkError, DownloadManager
from app.models import AppSettings, BundleSyncItem, FileCategory, RemoteBundle, SyncStatus
from app.naming import classify_filename, extract_folder_name
from app.product_meta import summary_message, sync_product_files
from app.scanner import scan_local_directory
from app.sync_engine import _norm_key, _singular_norm_key, compute_sync_items

SETTINGS_FILE = DATA_DIR / "settings.json"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="SyntyStoreSync")

auth_service = AuthService()
catalog_service = CatalogService(auth_service=auth_service)
download_manager = DownloadManager(max_concurrent=2)


def load_settings() -> AppSettings:
    if SETTINGS_FILE.exists():
        try:
            return AppSettings.model_validate_json(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return AppSettings()


def save_settings(settings: AppSettings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        settings.model_dump_json(indent=2), encoding="utf-8"
    )


def build_state_payload() -> dict:
    settings = load_settings()
    remote_bundles = catalog_service.load_cached_catalog()
    local_root = Path(settings.local_root_path)
    local_map = scan_local_directory(local_root)
    items = compute_sync_items(remote_bundles, local_map, settings)

    counts = {
        "all": len(items),
        "missing": sum(1 for i in items if i.status == SyncStatus.MISSING),
        "update_available": sum(1 for i in items if i.status == SyncStatus.UPDATE_AVAILABLE),
        "partial": sum(1 for i in items if i.status == SyncStatus.PARTIAL),
        "synced": sum(1 for i in items if i.status == SyncStatus.SYNCED),
    }

    return {
        "authenticated": auth_service.has_valid_session(),
        "session": auth_service.session_status(),
        "customer_id": auth_service.get_customer_id(),
        "has_cached_catalog": len(remote_bundles) > 0,
        "remote_bundle_count": len(remote_bundles),
        "local_folder_count": len(local_map),
        "settings": settings.model_dump(),
        "counts": counts,
        "items": [i.model_dump(mode="json") for i in items],
    }


class DownloadStartRequest(BaseModel):
    folder_names: list[str]


class OpenFolderRequest(BaseModel):
    folder_name: str | None = None


class DeleteBundleRequest(BaseModel):
    folder_name: str


@app.get("/api/state")
async def get_state() -> dict:
    return build_state_payload()


async def _open_and_focus_windows_folder(target: Path) -> None:
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _get_explorer_hwnds() -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []

        def _cb(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                cls_buf = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, cls_buf, 64)
                if cls_buf.value in ("CabinetWClass", "ExploreWClass"):
                    title_buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, title_buf, 512)
                    found.append((hwnd, title_buf.value))
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        return found

    def _bring_to_front(hwnd: int) -> None:
        user32.keybd_event(0x12, 0, 2, 0)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SwitchToThisWindow(hwnd, True)

    before_hwnds = {h for h, _ in _get_explorer_hwnds()}
    folder_label = target.name.lower()

    user32.AllowSetForegroundWindow(-1)
    user32.keybd_event(0x12, 0, 2, 0)
    subprocess.Popen(["explorer.exe", str(target)])

    for _ in range(15):
        await asyncio.sleep(0.1)
        current = _get_explorer_hwnds()
        for hwnd, _title in current:
            if hwnd not in before_hwnds:
                _bring_to_front(hwnd)
                return
        for hwnd, title in current:
            if folder_label and folder_label in title.lower():
                _bring_to_front(hwnd)
                return


@app.post("/api/open-folder")
async def open_folder(req: OpenFolderRequest) -> dict:
    settings = load_settings()
    root_dir = Path(settings.local_root_path)
    if req.folder_name:
        target = root_dir / req.folder_name
        if not target.exists() or not target.is_dir():
            raise HTTPException(status_code=404, detail="Local asset folder does not exist.")
    else:
        target = root_dir
        target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            await _open_and_focus_windows_folder(target)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Finder", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"opened": str(target)}


@app.post("/api/bundles/delete")
async def delete_bundle_folder(req: DeleteBundleRequest) -> dict:
    settings = load_settings()
    root_dir = Path(settings.local_root_path).resolve()
    clean_name = Path(req.folder_name).name
    if not clean_name or clean_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name.")

    target = (root_dir / clean_name).resolve()
    if target.parent != root_dir:
        raise HTTPException(status_code=400, detail="Folder must be a direct child of the local Synty path.")
    if not target.exists():
        return build_state_payload()

    def _on_rm_error(func, path, _exc_info):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    try:
        shutil.rmtree(target, onerror=_on_rm_error)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return build_state_payload()


@app.post("/api/bundles/delete-zip")
async def delete_bundle_zip(req: DeleteBundleRequest) -> dict:
    settings = load_settings()
    root_dir = Path(settings.local_root_path).resolve()
    clean_name = Path(req.folder_name).name
    if not clean_name or clean_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid folder name.")

    target = (root_dir / clean_name).resolve()
    if target.parent != root_dir or not target.exists() or not target.is_dir():
        return build_state_payload()

    for entry in list(target.iterdir()):
        if entry.is_file() and classify_filename(entry.name) == FileCategory.SOURCE_ZIP:
            try:
                os.chmod(entry, stat.S_IWRITE)
                entry.unlink()
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    return build_state_payload()


def _find_remote_bundle_for_folder(
    folder_name: str, remote_bundles: list[RemoteBundle]
) -> RemoteBundle | None:
    target_norm = _norm_key(folder_name)
    target_sing = _singular_norm_key(folder_name)
    for b in remote_bundles:
        if b.folder_name == folder_name or _norm_key(b.folder_name) == target_norm:
            return b
        for f in b.files:
            fn = extract_folder_name(f.filename)
            if _norm_key(fn) == target_norm or _singular_norm_key(fn) == target_sing:
                return b
        if _singular_norm_key(b.folder_name) == target_sing:
            return b
    return None


@app.get("/api/bundles/product-page/{folder_name}")
async def open_bundle_product_page(folder_name: str) -> RedirectResponse:
    remote_bundles = catalog_service.load_cached_catalog()
    bundle = _find_remote_bundle_for_folder(folder_name, remote_bundles)

    raw_title = bundle.title if bundle else folder_name.replace("_", " ")
    clean_title = re.sub(r"\|.*$", "", raw_title).replace("(Local Only)", "").strip()

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            resp = await client.get(
                f"{BASE_STORE_URL}/search/suggest.json",
                params={
                    "q": clean_title,
                    "resources[type]": "product",
                    "resources[limit]": "5",
                },
            )
            if resp.status_code == 200:
                products = (
                    resp.json()
                    .get("resources", {})
                    .get("results", {})
                    .get("products", [])
                )
                if products:
                    target_norm = _norm_key(clean_title)
                    best = next(
                        (
                            p
                            for p in products
                            if _norm_key(re.sub(r"\|.*$", "", p.get("title", ""))) == target_norm
                        ),
                        products[0],
                    )
                    prod_url = best.get("url", "").split("?")[0]
                    if prod_url:
                        return RedirectResponse(url=f"{BASE_STORE_URL}{prod_url}")
    except Exception:
        pass

    customer_id = auth_service.get_customer_id()
    if bundle and customer_id and "_" in bundle.bundle_id:
        order_id, item_id = bundle.bundle_id.split("_", 1)
        return RedirectResponse(
            url=f"{BASE_STORE_URL}/apps/downloads/customers/{customer_id}/orders/{order_id}/order_items/{item_id}"
        )

    return RedirectResponse(
        url=f"{BASE_STORE_URL}/search?q={quote(clean_title)}&type=product"
    )


@app.post("/api/settings")
async def update_settings(new_settings: AppSettings) -> dict:
    save_settings(new_settings)
    download_manager.max_concurrent = new_settings.max_concurrent_downloads
    return build_state_payload()


@app.post("/api/auth/login")
async def trigger_login() -> dict:
    try:
        await auth_service.launch_interactive_login()
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not launch login browser: {exc!r}"
        ) from exc
    return build_state_payload()


def _sync_product_files(products: list[dict] | None = None) -> dict | None:
    """Writes synty_product.json into downloaded bundle folders from cached store products.

    Local only (no network). Never raises: product info must not break refresh or downloads.
    """
    try:
        if products is None:
            products = catalog_service.load_store_products()
        if not products:
            return None
        remote_bundles = catalog_service.load_cached_catalog()
        local_root = Path(load_settings().local_root_path)
        summary = sync_product_files(
            remote_bundles,
            products,
            catalog_service.load_overrides(),
            local_root,
            scan_local_directory(local_root),
        )
        catalog_service.save_cached_catalog(remote_bundles)
        return summary
    except Exception as exc:
        download_manager.broadcast(
            {"type": "catalog_progress", "stage": "products", "message": f"Product info failed: {exc}"}
        )
        return None


@app.post("/api/catalog/refresh")
async def refresh_catalog() -> dict:
    try:
        await catalog_service.fetch_remote_catalog(progress_cb=download_manager.broadcast)
    except SessionExpiredError as exc:
        auth_service.mark_expired()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Public product info (dec-mwsffuxz-a6dd): one cookieless /products.json crawl; on failure
    # the previous cached copy is used.
    download_manager.broadcast(
        {"type": "catalog_progress", "stage": "products", "message": "Fetching store product info..."}
    )
    products = await catalog_service.fetch_store_products(progress_cb=download_manager.broadcast)
    if products is None:
        print("[SyntyStoreSync] Store product fetch failed; using the previous cached copy.", flush=True)
    summary = _sync_product_files(products)
    message = summary_message(summary) if summary else "Product info: no store products available."
    print(f"[SyntyStoreSync] {message}", flush=True)
    download_manager.broadcast({"type": "catalog_completed", "message": message})
    payload = build_state_payload()
    payload["product_info"] = summary
    return payload


@app.post("/api/downloads/start")
async def start_downloads(req: DownloadStartRequest) -> dict:
    settings = load_settings()
    remote_bundles = catalog_service.load_cached_catalog()
    local_root = Path(settings.local_root_path)
    local_map = scan_local_directory(local_root)
    items_by_folder: dict[str, BundleSyncItem] = {
        i.folder_name: i for i in compute_sync_items(remote_bundles, local_map, settings)
    }

    cookies = auth_service.get_cookies()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }

    queued: list[str] = []
    for folder_name in req.folder_names:
        item = items_by_folder.get(folder_name)
        if not item or not item.files_to_download:
            continue
        queued.append(folder_name)
        download_manager.broadcast(
            {
                "type": "bundle_queued",
                "folder_name": folder_name,
                "file_count": len(item.files_to_download),
            }
        )

        async def _run_bundle(target_item: BundleSyncItem) -> None:
            try:
                await download_manager.download_bundle(
                    item=target_item,
                    root_path=local_root,
                    cookies=cookies,
                    headers=headers,
                )
                _sync_product_files()  # from the cached store products; no network
            except asyncio.CancelledError:
                pass
            except DownloadLinkError as exc:
                # Links are stale or revoked: stop the whole queue instead of hammering the store.
                download_manager.broadcast({"type": "download_link_invalid", "message": str(exc)})
                download_manager.cancel_all()
            except Exception:
                pass

        task = asyncio.create_task(_run_bundle(item))
        download_manager._active_tasks[folder_name] = task

    return {"queued": queued}


@app.post("/api/downloads/cancel")
async def cancel_downloads() -> dict:
    download_manager.cancel_all()
    return {"cancelled": True}


@app.get("/api/downloads/stream")
async def stream_downloads() -> StreamingResponse:
    async def event_publisher():
        sub = download_manager.subscribe_events()
        it = sub.__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(it.__anext__(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
            except StopAsyncIteration:
                break

    return StreamingResponse(event_publisher(), media_type="text/event-stream")


@app.get("/api/local-icon/{folder_name}")
async def get_local_icon(folder_name: str):
    settings = load_settings()
    folder_path = Path(settings.local_root_path) / folder_name
    if not folder_path.exists() or not folder_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    for entry in folder_path.iterdir():
        if entry.is_file() and entry.name.lower().endswith(
            ("_icon.png", "_icon.jpg", "_icon.jpeg", "icons.png")
        ):
            return FileResponse(entry)

    raise HTTPException(status_code=404, detail="Icon not found")


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
