from pathlib import Path
import httpx
import pytest
from app.downloader import DownloadManager
from app.models import BundleSyncItem, FileCategory, RemoteAssetFile, SyncStatus


@pytest.mark.asyncio
async def test_atomic_download_and_old_version_cleanup(tmp_path: Path):
    pack_dir = tmp_path / "POLYGON_Farm"
    pack_dir.mkdir()
    old_unity = pack_dir / "POLYGON_Farm_Unity_2022_3_v1_6_0.unitypackage"
    old_unity.write_bytes(b"old-content")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"new-unity-package-bytes", headers={"content-length": "23"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as mock_client:
        manager = DownloadManager(max_concurrent=1)
        item = BundleSyncItem(
            folder_name="POLYGON_Farm",
            title="POLYGON Farm",
            prefix="POLYGON",
            status=SyncStatus.UPDATE_AVAILABLE,
            files_to_download=[
                RemoteAssetFile(
                    filename="POLYGON_Farm_Unity_2022_3_v1_7_0.unitypackage",
                    download_url="https://example.com/farm_v1_7_0.unitypackage",
                    category=FileCategory.UNITY,
                )
            ],
        )

        paths = await manager.download_bundle(item, tmp_path, client=mock_client)
        assert len(paths) == 1
        new_unity = pack_dir / "POLYGON_Farm_Unity_2022_3_v1_7_0.unitypackage"
        assert new_unity.exists()
        assert new_unity.read_bytes() == b"new-unity-package-bytes"
        assert not old_unity.exists()
        assert not (pack_dir / "POLYGON_Farm_Unity_2022_3_v1_7_0.unitypackage.part").exists()


@pytest.mark.asyncio
async def test_html_answer_is_never_saved_as_a_file(tmp_path: Path):
    from app.downloader import DownloadLinkError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Log in</html>", headers={"content-type": "text/html; charset=utf-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        manager = DownloadManager(max_concurrent=1)
        item = BundleSyncItem(
            folder_name="POLYGON_Farm",
            title="POLYGON Farm",
            prefix="POLYGON",
            status=SyncStatus.MISSING,
            files_to_download=[
                RemoteAssetFile(
                    filename="POLYGON_Farm_ICON.png",
                    download_url="https://example.com/icon",
                    category=FileCategory.ICON,
                )
            ],
        )
        with pytest.raises(DownloadLinkError):
            await manager.download_bundle(item, tmp_path, client=mock_client)
        pack_dir = tmp_path / "POLYGON_Farm"
        assert not (pack_dir / "POLYGON_Farm_ICON.png").exists()
        assert not (pack_dir / "POLYGON_Farm_ICON.png.part").exists()
