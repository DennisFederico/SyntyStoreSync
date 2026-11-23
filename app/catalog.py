import asyncio
import json
import re
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
from app.auth import DATA_DIR, AuthService, SessionExpiredError
from app.models import FileCategory, RemoteAssetFile, RemoteBundle
from app.naming import (
    classify_filename,
    extract_folder_name,
    normalize_sky_pilot_filename,
    parse_unity_and_pkg_version,
    parse_zip_version,
)

CATALOG_CACHE_FILE = DATA_DIR / "remote_catalog_cache.json"
STORE_PRODUCTS_CACHE_FILE = DATA_DIR / "store_products_cache.json"
PRODUCT_OVERRIDES_FILE = DATA_DIR / "product_overrides.json"
STORE_PRODUCTS_PAGE_SIZE = 250
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BASE_STORE_URL = "https://syntystore.com"

_ORDER_ITEM_RE = re.compile(r"/orders/(\d+)/order_items/(\d+)")
_SIZE_RE = re.compile(r"\(?\s*([\d.]+)\s*(B|KB|MB|GB)\s*\)?", re.IGNORECASE)
_HAS_EXT_RE = re.compile(r"\.(png|jpg|jpeg|unitypackage|zip)$", re.IGNORECASE)


def parse_size_bytes(raw: str) -> int | None:
    m = _SIZE_RE.search(raw)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit, 1)
    return int(val * mult)


def reconstruct_sky_pilot_filename(heading_el) -> str:
    """Reconstructs canonical Synty filename from a Sky Pilot .sky-pilot-file-heading element."""
    clone = BeautifulSoup(str(heading_el), "html.parser").select_one(".sky-pilot-file-heading")
    if not clone:
        return ""

    for size_el in clone.select(".sky-pilot-file-size"):
        size_el.decompose()

    spans = clone.find_all("span")
    if not spans:
        raw_text = clone.get_text(" ", strip=True)
        return normalize_sky_pilot_filename(raw_text)

    meta_text = spans[0].get_text(" ", strip=True)
    spans[0].decompose()
    base_text = clone.get_text(" ", strip=True)

    base_clean = re.sub(r"\s+", "_", base_text.strip(" _-|"))
    if _HAS_EXT_RE.search(base_clean) and not meta_text:
        return base_clean

    meta_parts = [re.sub(r"\s+", "_", p.strip()) for p in meta_text.split("|") if p.strip()]
    meta_clean = "_".join(meta_parts)

    combined = f"{base_clean}_{meta_clean}" if meta_clean else base_clean
    return normalize_sky_pilot_filename(combined)


def parse_library_page(
    html: str, base_url: str = BASE_STORE_URL
) -> tuple[list[dict], str | None]:
    """Parses a Sky Pilot library listing page into bundle stubs and optional next-page URL."""
    soup = BeautifulSoup(html, "html.parser")
    bundles: list[dict] = []

    for item in soup.select(".sky-pilot-files-list > a.sky-pilot-list-item"):
        href = item.get("href", "")
        if not href:
            continue
        detail_url = urljoin(base_url, href)
        m = _ORDER_ITEM_RE.search(href)
        bundle_id = f"{m.group(1)}_{m.group(2)}" if m else href

        heading = item.select_one(".sky-pilot-file-heading")
        title = heading.get_text(" ", strip=True) if heading else bundle_id

        img = item.select_one("img.sky-pilot-product-thumbnail") or item.select_one("img")
        thumb_url = img.get("src") if img else None

        bundles.append(
            {
                "bundle_id": bundle_id,
                "title": title,
                "detail_url": detail_url,
                "store_thumbnail_url": thumb_url,
            }
        )

    next_link = soup.select_one(".sky-pilot-pagination span.next a[href]")
    next_url = urljoin(base_url, next_link["href"]) if next_link else None
    return bundles, next_url


def _derive_bundle_folder_name(
    title: str, files: list[RemoteAssetFile], store_thumbnail_url: str | None = None
) -> str:
    # 1. Prefer the bundle's own _ICON file if present
    icons = [f for f in files if f.category == FileCategory.ICON]
    if icons:
        return extract_folder_name(icons[0].filename)

    # 2. Otherwise prefer the primary Unity package filename
    unitys = [f for f in files if f.category == FileCategory.UNITY]
    if unitys:
        return extract_folder_name(unitys[0].filename)

    # 3. Otherwise prefer a non-generic SourceFiles zip
    zips = [
        f
        for f in files
        if f.category == FileCategory.SOURCE_ZIP
        and "polygon_generic" not in f.filename.lower()
    ]
    if zips:
        return extract_folder_name(zips[0].filename)

    # 4. Fallback: clean the display title (e.g. "POLYGON - Dark Fortress | FREE" -> "POLYGON_Dark_Fortress")
    cleaned = re.sub(r"\|.*$", "", title).strip()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", cleaned).strip("_")
    return cleaned or "UNKNOWN_Bundle"


def parse_bundle_detail_page(
    html: str,
    bundle_id: str,
    title: str,
    store_thumbnail_url: str | None = None,
    base_url: str = BASE_STORE_URL,
) -> RemoteBundle:
    """Parses a Sky Pilot bundle detail page into a RemoteBundle with classified files."""
    soup = BeautifulSoup(html, "html.parser")
    raw_files: list[RemoteAssetFile] = []

    for item in soup.select(".sky-pilot-files-list > div.sky-pilot-list-item"):
        heading = item.select_one(".sky-pilot-file-heading")
        btn = item.select_one("a.sky-pilot-button[href]")
        if not heading or not btn:
            continue

        size_el = heading.select_one(".sky-pilot-file-size")
        size_bytes = parse_size_bytes(size_el.get_text(" ", strip=True)) if size_el else None

        filename = reconstruct_sky_pilot_filename(heading)
        if not filename:
            continue

        download_url = urljoin(base_url, btn["href"])
        category = classify_filename(filename)

        unity_ver: tuple[int, ...] = ()
        pkg_ver: tuple[int, ...] = ()
        if category == FileCategory.UNITY:
            unity_ver, pkg_ver = parse_unity_and_pkg_version(filename)
        elif category == FileCategory.SOURCE_ZIP:
            pkg_ver = parse_zip_version(filename)

        raw_files.append(
            RemoteAssetFile(
                filename=filename,
                download_url=download_url,
                category=category,
                size_bytes=size_bytes,
                unity_version=unity_ver,
                pkg_version=pkg_ver,
            )
        )

    folder_name = _derive_bundle_folder_name(title, raw_files, store_thumbnail_url)

    # Filter out secondary POLYGON_Generic attachments unless this bundle is POLYGON_Generic itself
    is_generic_bundle = "generic" in folder_name.lower() or "generic" in title.lower()
    filtered_files = [
        f
        for f in raw_files
        if is_generic_bundle or not f.filename.lower().startswith("polygon_generic")
    ]

    return RemoteBundle(
        bundle_id=bundle_id,
        title=title,
        folder_name=folder_name,
        store_thumbnail_url=store_thumbnail_url,
        files=filtered_files,
    )


def compute_retry_delay(retry_after_header: str | None, attempt: int, base_delay: float = 3.5) -> float:
    """Computes backoff delay in seconds when HTTP 429 or transient 5xx occurs."""
    if retry_after_header:
        try:
            val = float(retry_after_header.strip())
            if val > 0:
                return min(val + 0.5, 60.0)
        except ValueError:
            pass
    return min(base_delay * (1.8 ** attempt), 45.0)


def _merge_bundles_by_folder(bundles: list[RemoteBundle]) -> list[RemoteBundle]:
    merged_by_folder: dict[str, RemoteBundle] = {}
    for b in bundles:
        if b.folder_name not in merged_by_folder:
            merged_by_folder[b.folder_name] = b
        else:
            existing = merged_by_folder[b.folder_name]
            existing_names = {f.filename for f in existing.files}
            for f in b.files:
                if f.filename not in existing_names:
                    existing.files.append(f)
            if not existing.store_thumbnail_url and b.store_thumbnail_url:
                existing.store_thumbnail_url = b.store_thumbnail_url
    return sorted(merged_by_folder.values(), key=lambda x: x.folder_name.lower())


class CatalogService:
    def __init__(
        self,
        auth_service: AuthService,
        cache_file: Path = CATALOG_CACHE_FILE,
        max_concurrent_requests: int = 2,
        min_request_interval: float = 0.45,
        store_products_file: Path = STORE_PRODUCTS_CACHE_FILE,
        overrides_file: Path = PRODUCT_OVERRIDES_FILE,
    ) -> None:
        self.auth_service = auth_service
        self.cache_file = cache_file
        self.store_products_file = store_products_file
        self.overrides_file = overrides_file
        self.max_concurrent_requests = max_concurrent_requests
        self.min_request_interval = min_request_interval

    def load_cached_catalog(self) -> list[RemoteBundle]:
        if not self.cache_file.exists():
            return []
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            bundles = [RemoteBundle.model_validate(item) for item in raw]
            for b in bundles:
                for f in b.files:
                    norm_name = normalize_sky_pilot_filename(f.filename)
                    if norm_name != f.filename or f.category == FileCategory.IGNORED:
                        f.filename = norm_name
                        f.category = classify_filename(norm_name)
                        if f.category == FileCategory.UNITY:
                            f.unity_version, f.pkg_version = parse_unity_and_pkg_version(norm_name)
                        elif f.category == FileCategory.SOURCE_ZIP:
                            f.pkg_version = parse_zip_version(norm_name)
            return bundles
        except Exception:
            return []

    def save_cached_catalog(self, bundles: list[RemoteBundle]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = [b.model_dump(mode="json") for b in bundles]
        self.cache_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _make_getter(
        self, progress_cb: Callable[[dict], None] | None = None
    ) -> Callable[[httpx.AsyncClient, str, str], Awaitable[httpx.Response]]:
        """Returns a GET helper with polite pacing plus 429/5xx backoff, shared across calls."""
        rate_limit_lock = asyncio.Lock()
        last_request_ts = 0.0
        cooldown_until = 0.0

        async def _get_with_retry(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response:
            nonlocal last_request_ts, cooldown_until
            max_attempts = 7
            for attempt in range(max_attempts):
                async with rate_limit_lock:
                    now = time.monotonic()
                    if cooldown_until > now:
                        await asyncio.sleep(cooldown_until - now)
                    now = time.monotonic()
                    elapsed = now - last_request_ts
                    if elapsed < self.min_request_interval:
                        await asyncio.sleep(self.min_request_interval - elapsed)
                    last_request_ts = time.monotonic()

                resp = await client.get(url)
                if resp.status_code in (429, 502, 503, 504):
                    wait_sec = compute_retry_delay(resp.headers.get("Retry-After"), attempt)
                    cooldown_until = max(cooldown_until, time.monotonic() + wait_sec)
                    if progress_cb:
                        progress_cb(
                            {
                                "type": "catalog_progress",
                                "stage": "backoff",
                                "message": (
                                    f"Store rate limit ({resp.status_code}) on {label} — "
                                    f"pausing {wait_sec:.1f}s before retrying ({attempt + 1}/{max_attempts})..."
                                ),
                            }
                        )
                    await asyncio.sleep(wait_sec)
                    continue

                resp.raise_for_status()
                return resp

            raise RuntimeError(f"Exceeded retry limit due to store rate limiting (429) on {label}")

        return _get_with_retry

    async def fetch_remote_catalog(
        self,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> list[RemoteBundle]:
        customer_id = self.auth_service.get_customer_id()
        cookies = self.auth_service.get_cookies()
        if not customer_id or not cookies:
            raise RuntimeError("Not authenticated with Synty Store. Please log in first.")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        _get_with_retry = self._make_getter(progress_cb)

        all_stubs: list[dict] = []
        seen_urls: set[str] = set()
        next_url: str | None = (
            f"{BASE_STORE_URL}/apps/downloads/orders/{customer_id}"
            f"?logged_in_customer_id={customer_id}&line_items_page=1"
        )

        async with httpx.AsyncClient(
            cookies=cookies,
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=15.0),
        ) as client:
            page_num = 1
            while next_url and next_url not in seen_urls:
                seen_urls.add(next_url)
                if progress_cb:
                    progress_cb(
                        {
                            "type": "catalog_progress",
                            "stage": "pages",
                            "message": f"Scanning library page {page_num}...",
                        }
                    )
                resp = await _get_with_retry(client, next_url, f"Library page {page_num}")
                stubs, next_url = parse_library_page(resp.text, BASE_STORE_URL)
                if page_num == 1:
                    if not stubs:
                        # Logged-out / expired sessions get HTTP 200 with an empty library
                        # (verified 2026-11-22). Abort instead of overwriting the cached catalog.
                        raise SessionExpiredError(
                            "Synty Store returned no library items. Your session has probably "
                            "expired. Click 'Login to Synty Store' and try again."
                        )
                    self.auth_service.mark_verified()
                all_stubs.extend(stubs)
                page_num += 1

            # Deduplicate stubs by bundle_id while preserving order
            unique_stubs: list[dict] = []
            seen_ids: set[str] = set()
            for stub in all_stubs:
                if stub["bundle_id"] not in seen_ids:
                    seen_ids.add(stub["bundle_id"])
                    unique_stubs.append(stub)

            sem = asyncio.Semaphore(self.max_concurrent_requests)
            completed_count = 0
            total_count = len(unique_stubs)
            collected_bundles: list[RemoteBundle] = list(self.load_cached_catalog())

            async def _fetch_one(stub: dict) -> RemoteBundle:
                nonlocal completed_count
                async with sem:
                    r = await _get_with_retry(client, stub["detail_url"], stub["title"])
                    bundle = parse_bundle_detail_page(
                        html=r.text,
                        bundle_id=stub["bundle_id"],
                        title=stub["title"],
                        store_thumbnail_url=stub["store_thumbnail_url"],
                        base_url=BASE_STORE_URL,
                    )
                    completed_count += 1
                    collected_bundles.append(bundle)
                    if completed_count % 5 == 0:
                        self.save_cached_catalog(_merge_bundles_by_folder(collected_bundles))
                    if progress_cb:
                        progress_cb(
                            {
                                "type": "catalog_progress",
                                "stage": "bundles",
                                "completed": completed_count,
                                "total": total_count,
                                "message": f"Fetched {completed_count}/{total_count}: {bundle.folder_name}",
                            }
                        )
                    return bundle

            bundles = list(await asyncio.gather(*(_fetch_one(s) for s in unique_stubs)))

        final_bundles = _merge_bundles_by_folder(bundles)
        self.save_cached_catalog(final_bundles)
        if progress_cb:
            progress_cb(
                {
                    "type": "catalog_completed",
                    "total": len(final_bundles),
                    "message": f"Catalog refresh complete ({len(final_bundles)} bundles).",
                }
            )
        return final_bundles

    # ---- Public store products (dec-mwsffuxz-a6dd) -------------------------------------------

    def load_store_products(self) -> list[dict]:
        try:
            raw = json.loads(self.store_products_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    def load_overrides(self) -> dict[str, str]:
        """Hand-edited {local folder_name: product handle} map; a missing or broken file means none."""
        try:
            raw = json.loads(self.overrides_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v}

    async def fetch_store_products(
        self, progress_cb: Callable[[dict], None] | None = None
    ) -> list[dict] | None:
        """Fetches the public product list (/products.json) without cookies.

        Returns the products, or None on failure (the previous cache is kept untouched).
        """
        get = self._make_getter(progress_cb)
        products: list[dict] = []
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=15.0),
            ) as client:
                page = 1
                while True:
                    url = (
                        f"{BASE_STORE_URL}/products.json"
                        f"?limit={STORE_PRODUCTS_PAGE_SIZE}&page={page}"
                    )
                    resp = await get(client, url, f"store products page {page}")
                    batch = resp.json().get("products") or []
                    for p in batch:
                        products.append(
                            {
                                "id": p.get("id"),
                                "handle": p.get("handle"),
                                "title": p.get("title", ""),
                                "body_html": p.get("body_html") or "",
                                "images": [
                                    (img.get("src") or "").split("?", 1)[0]
                                    for img in (p.get("images") or [])
                                    if isinstance(img, dict)
                                ],
                            }
                        )
                    if len(batch) < STORE_PRODUCTS_PAGE_SIZE:
                        break
                    page += 1
        except Exception as exc:
            if progress_cb:
                progress_cb(
                    {
                        "type": "catalog_progress",
                        "stage": "products",
                        "message": f"Could not fetch store product info ({exc}); keeping the previous copy.",
                    }
                )
            return None

        self.store_products_file.parent.mkdir(parents=True, exist_ok=True)
        self.store_products_file.write_text(
            json.dumps(products, ensure_ascii=False), encoding="utf-8"
        )
        return products
