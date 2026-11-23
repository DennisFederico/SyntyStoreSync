import asyncio
import re
import time
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import unquote
import httpx
from app.models import BundleSyncItem, FileCategory, RemoteAssetFile
from app.naming import classify_filename

_CD_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', re.IGNORECASE)


def _extract_server_filename(resp: httpx.Response, fallback: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    if cd:
        m = _CD_FILENAME_RE.search(cd)
        if m:
            candidate = Path(unquote(m.group(1).strip())).name
            if candidate:
                return candidate
    url_str = unquote(str(resp.url))
    m_url = _CD_FILENAME_RE.search(url_str)
    if m_url:
        candidate = Path(m_url.group(1).strip()).name
        if candidate:
            return candidate
    return fallback


class DownloadLinkError(RuntimeError):
    """The store answered a download link with a web page instead of a file."""


class DownloadManager:
    def __init__(self, max_concurrent: int = 2) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._subscribers: list[asyncio.Queue] = []
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._cancelled = False

    def subscribe_events(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(queue)

        async def _gen():
            try:
                while True:
                    event = await queue.get()
                    yield event
            finally:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

        return _gen()

    def broadcast(self, event: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)

    def _broadcast(self, event: dict) -> None:
        self.broadcast(event)

    def cancel_all(self) -> None:
        self._cancelled = True
        for task in list(self._active_tasks.values()):
            task.cancel()
        self._active_tasks.clear()
        self._broadcast({"type": "cancelled_all"})

    async def download_bundle(
        self,
        item: BundleSyncItem,
        root_path: Path,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[Path]:
        self._cancelled = False
        target_dir = root_path / item.folder_name
        target_dir.mkdir(parents=True, exist_ok=True)
        downloaded_paths: list[Path] = []

        for remote_file in item.files_to_download:
            if self._cancelled:
                break
            path = await self._download_single_file(
                folder_name=item.folder_name,
                remote_file=remote_file,
                target_dir=target_dir,
                cookies=cookies,
                headers=headers,
                client=client,
            )
            if path:
                downloaded_paths.append(path)

        self._broadcast(
            {
                "type": "bundle_completed",
                "folder_name": item.folder_name,
                "files": [p.name for p in downloaded_paths],
            }
        )
        return downloaded_paths

    async def _download_single_file(
        self,
        folder_name: str,
        remote_file: RemoteAssetFile,
        target_dir: Path,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> Path | None:
        final_path = target_dir / remote_file.filename
        part_path = target_dir / f"{remote_file.filename}.part"

        async with self._semaphore:
            self._broadcast(
                {
                    "type": "file_started",
                    "folder_name": folder_name,
                    "filename": remote_file.filename,
                }
            )

            owns_client = client is None
            http_client = client or httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(120.0, connect=30.0),
                cookies=cookies,
                headers=headers,
            )

            try:
                async with http_client.stream("GET", remote_file.download_url) as resp:
                    resp.raise_for_status()
                    # Download links carry their own auth (no session cookie needed, verified
                    # 2026-11-22). An HTML answer means the link is stale or revoked: never save it.
                    if resp.headers.get("content-type", "").lower().startswith("text/html"):
                        raise DownloadLinkError(
                            "Synty Store returned a web page instead of the file. "
                            "Refresh the remote catalog, and log in again if it keeps failing."
                        )
                    resolved_name = _extract_server_filename(resp, remote_file.filename)
                    final_path = target_dir / resolved_name
                    part_path = target_dir / f"{resolved_name}.part"

                    total_bytes = int(resp.headers.get("content-length", 0)) or remote_file.size_bytes or 0
                    downloaded_bytes = 0
                    start_time = time.monotonic()
                    last_emit = 0.0

                    with part_path.open("wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 256):
                            if self._cancelled:
                                raise asyncio.CancelledError()
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            now = time.monotonic()
                            if now - last_emit >= 0.25 or (total_bytes and downloaded_bytes >= total_bytes):
                                elapsed = max(now - start_time, 0.001)
                                speed_bps = int(downloaded_bytes / elapsed)
                                self._broadcast(
                                    {
                                        "type": "file_progress",
                                        "folder_name": folder_name,
                                        "filename": resolved_name,
                                        "downloaded_bytes": downloaded_bytes,
                                        "total_bytes": total_bytes,
                                        "speed_bps": speed_bps,
                                    }
                                )
                                last_emit = now

                # Remove older files of the same category before renaming new version
                self._cleanup_superseded_files(target_dir, remote_file, resolved_name)
                if final_path.exists():
                    final_path.unlink()
                part_path.rename(final_path)

                self._broadcast(
                    {
                        "type": "file_completed",
                        "folder_name": folder_name,
                        "filename": resolved_name,
                    }
                )
                return final_path

            except Exception as exc:
                if part_path.exists():
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                self._broadcast(
                    {
                        "type": "file_error",
                        "folder_name": folder_name,
                        "filename": remote_file.filename,
                        "error": str(exc),
                    }
                )
                raise
            finally:
                if owns_client:
                    await http_client.aclose()

    @staticmethod
    def _cleanup_superseded_files(
        target_dir: Path, new_file: RemoteAssetFile, resolved_name: str
    ) -> None:
        if new_file.category not in (FileCategory.UNITY, FileCategory.SOURCE_ZIP):
            return
        for existing in target_dir.iterdir():
            if not existing.is_file() or existing.name.endswith(".part"):
                continue
            if existing.name == resolved_name:
                continue
            if classify_filename(existing.name) == new_file.category:
                try:
                    existing.unlink()
                except OSError:
                    pass
