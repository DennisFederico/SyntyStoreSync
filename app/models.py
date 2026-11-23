from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class FileCategory(str, Enum):
    ICON = "icon"
    UNITY = "unity"
    SOURCE_ZIP = "source_zip"
    IGNORED = "ignored"


class SyncStatus(str, Enum):
    SYNCED = "synced"
    UPDATE_AVAILABLE = "update_available"
    PARTIAL = "partial"
    MISSING = "missing"


class RemoteAssetFile(BaseModel):
    filename: str
    download_url: str
    category: FileCategory
    size_bytes: int | None = None
    unity_version: tuple[int, ...] = ()
    pkg_version: tuple[int, ...] = ()


class RemoteBundle(BaseModel):
    bundle_id: str
    title: str
    folder_name: str
    store_thumbnail_url: str | None = None
    files: list[RemoteAssetFile] = Field(default_factory=list)
    # Public Synty Store product this purchase maps to (dec-mwsffuxz-a6dd); None when unmatched.
    product_id: int | None = None
    product_handle: str | None = None


class LocalBundleInfo(BaseModel):
    folder_name: str
    folder_path: str
    icon_file: str | None = None
    unity_files: list[str] = Field(default_factory=list)
    source_zip_files: list[str] = Field(default_factory=list)


class BundleSyncItem(BaseModel):
    folder_name: str
    title: str
    prefix: str
    status: SyncStatus
    thumbnail_url: str | None = None
    has_local_icon: bool = False
    local_unity_file: str | None = None
    remote_unity_file: str | None = None
    unity_update_available: bool = False
    local_zip_file: str | None = None
    remote_zip_file: str | None = None
    zip_update_available: bool = False
    files_to_download: list[RemoteAssetFile] = Field(default_factory=list)


class AppSettings(BaseModel):
    # Default: <home>/Unity/Synty. The value saved in .data/settings.json wins.
    local_root_path: str = Field(default_factory=lambda: str(Path.home() / "Unity" / "Synty"))
    download_icon: bool = True
    download_unity: bool = True
    download_source_zip: bool = True
    max_concurrent_downloads: int = 2
