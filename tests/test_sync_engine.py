from pathlib import Path
from app.models import AppSettings, FileCategory, RemoteAssetFile, RemoteBundle, SyncStatus
from app.scanner import scan_local_directory
from app.sync_engine import compute_sync_items


def test_scan_and_sync_status(tmp_path: Path):
    mil_dir = tmp_path / "POLYGON_Military"
    mil_dir.mkdir()
    (mil_dir / "POLYGON_Military_ICON.png").write_bytes(b"icon")
    (mil_dir / "POLYGON_Military_SourceFiles_v5.zip").write_bytes(b"zip")
    (mil_dir / "POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage").write_bytes(b"unity")

    farm_dir = tmp_path / "POLYGON_Farm"
    farm_dir.mkdir()
    (farm_dir / "POLYGON_Farm_ICON.png").write_bytes(b"icon")
    (farm_dir / "POLYGON_Farm_SourceFiles_v3.zip").write_bytes(b"zip")
    (farm_dir / "POLYGON_Farm_Unity_2022_3_v1_6_0.unitypackage").write_bytes(b"unity")

    local_map = scan_local_directory(tmp_path)
    assert "POLYGON_Military" in local_map
    assert "POLYGON_Farm" in local_map

    remote_bundles = [
        RemoteBundle(
            bundle_id="1",
            title="POLYGON Military",
            folder_name="POLYGON_Military",
            files=[
                RemoteAssetFile(filename="POLYGON_Military_ICON.png", download_url="https://example.com/1", category=FileCategory.ICON),
                RemoteAssetFile(filename="POLYGON_Military_SourceFiles_v5.zip", download_url="https://example.com/2", category=FileCategory.SOURCE_ZIP, pkg_version=(5,)),
                RemoteAssetFile(filename="POLYGON_Military_Unity_2022_3_v1_10_4.unitypackage", download_url="https://example.com/3", category=FileCategory.UNITY, unity_version=(2022, 3), pkg_version=(1, 10, 4)),
            ],
        ),
        RemoteBundle(
            bundle_id="2",
            title="POLYGON Farm",
            folder_name="POLYGON_Farm",
            files=[
                RemoteAssetFile(filename="POLYGON_Farm_ICON.png", download_url="https://example.com/4", category=FileCategory.ICON),
                RemoteAssetFile(filename="POLYGON_Farm_SourceFiles_v4.zip", download_url="https://example.com/5", category=FileCategory.SOURCE_ZIP, pkg_version=(4,)),
                RemoteAssetFile(filename="POLYGON_Farm_Unity_2022_3_v1_7_0.unitypackage", download_url="https://example.com/6", category=FileCategory.UNITY, unity_version=(2022, 3), pkg_version=(1, 7, 0)),
            ],
        ),
        RemoteBundle(
            bundle_id="3",
            title="POLYGON Samurai",
            folder_name="POLYGON_Samurai",
            files=[
                RemoteAssetFile(filename="POLYGON_Samurai_ICON.png", download_url="https://example.com/7", category=FileCategory.ICON),
                RemoteAssetFile(filename="POLYGON_Samurai_Unity_2022_3_v1_0_0.unitypackage", download_url="https://example.com/8", category=FileCategory.UNITY, unity_version=(2022, 3), pkg_version=(1, 0, 0)),
            ],
        ),
    ]

    settings = AppSettings(local_root_path=str(tmp_path))
    items = {item.folder_name: item for item in compute_sync_items(remote_bundles, local_map, settings)}

    assert items["POLYGON_Military"].status == SyncStatus.SYNCED
    assert len(items["POLYGON_Military"].files_to_download) == 0

    assert items["POLYGON_Farm"].status == SyncStatus.UPDATE_AVAILABLE
    assert items["POLYGON_Farm"].unity_update_available is True
    assert items["POLYGON_Farm"].zip_update_available is True
    assert len(items["POLYGON_Farm"].files_to_download) == 2

    assert items["POLYGON_Samurai"].status == SyncStatus.MISSING
    assert len(items["POLYGON_Samurai"].files_to_download) == 2


def test_map_bundles_to_local_agrees_with_compute_sync_items(tmp_path: Path):
    from app.sync_engine import map_bundles_to_local

    # Synty names this pack POLYGON_Holiday_2025 in one place and POLYGON_Holidays_2025 in another.
    (tmp_path / "POLYGON_Holidays_2025").mkdir()
    (tmp_path / "POLYGON_Holidays_2025" / "POLYGON_Holidays_2025_ICON.png").write_bytes(b"icon")
    local_map = scan_local_directory(tmp_path)
    bundles = [
        RemoteBundle(bundle_id="1", title="POLYGON - Holiday 2025", folder_name="POLYGON_Holiday_2025"),
        RemoteBundle(bundle_id="2", title="POLYGON - Not Downloaded", folder_name="POLYGON_Not_Downloaded"),
    ]
    mapped = map_bundles_to_local(bundles, local_map)
    assert {bid: loc.folder_name for bid, loc in mapped.items()} == {"1": "POLYGON_Holidays_2025"}

    items = compute_sync_items(bundles, local_map, AppSettings(local_root_path=str(tmp_path)))
    assert {i.folder_name for i in items} == {"POLYGON_Holidays_2025", "POLYGON_Not_Downloaded"}
