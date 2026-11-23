import re
from app.models import (
    AppSettings,
    BundleSyncItem,
    FileCategory,
    LocalBundleInfo,
    RemoteAssetFile,
    RemoteBundle,
    SyncStatus,
)
from app.naming import (
    extract_folder_name,
    parse_unity_and_pkg_version,
    parse_zip_version,
    pick_latest_unity_file,
)


def _norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _singular_norm_key(name: str) -> str:
    tokens = [t.rstrip("s") if len(t) > 3 else t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    return "".join(tokens)


def _find_matching_local_bundle(
    bundle: RemoteBundle,
    local_map: dict[str, LocalBundleInfo],
    norm_local_map: dict[str, LocalBundleInfo],
    singular_local_map: dict[str, LocalBundleInfo],
    matched_local_folders: set[str],
) -> LocalBundleInfo | None:
    candidates: list[str] = [bundle.folder_name]
    for f in bundle.files:
        if f.category != FileCategory.IGNORED:
            candidates.append(extract_folder_name(f.filename))

    for cand in candidates:
        if cand in local_map and cand not in matched_local_folders:
            return local_map[cand]
    for cand in candidates:
        loc = norm_local_map.get(_norm_key(cand))
        if loc and loc.folder_name not in matched_local_folders:
            return loc
    for cand in candidates:
        loc = singular_local_map.get(_singular_norm_key(cand))
        if loc and loc.folder_name not in matched_local_folders:
            return loc
    return None


def _select_target_remote_files(
    bundle: RemoteBundle,
) -> tuple[RemoteAssetFile | None, RemoteAssetFile | None, RemoteAssetFile | None]:
    icons = [f for f in bundle.files if f.category == FileCategory.ICON]
    unitys = [f for f in bundle.files if f.category == FileCategory.UNITY]
    zips = [f for f in bundle.files if f.category == FileCategory.SOURCE_ZIP]

    target_icon = icons[0] if icons else None

    target_unity: RemoteAssetFile | None = None
    if unitys:
        latest_name = pick_latest_unity_file([f.filename for f in unitys])
        target_unity = next((f for f in unitys if f.filename == latest_name), unitys[0])

    target_zip: RemoteAssetFile | None = None
    if zips:
        target_zip = max(zips, key=lambda f: parse_zip_version(f.filename))

    return target_icon, target_unity, target_zip


def map_bundles_to_local(
    remote_bundles: list[RemoteBundle],
    local_map: dict[str, LocalBundleInfo],
) -> dict[str, LocalBundleInfo]:
    """Maps bundle_id -> the existing local folder it binds to (multi-candidate matching).

    Each local folder binds to at most one bundle, in remote_bundles order.
    """
    norm_local_map: dict[str, LocalBundleInfo] = {_norm_key(k): v for k, v in local_map.items()}
    singular_local_map: dict[str, LocalBundleInfo] = {
        _singular_norm_key(k): v for k, v in local_map.items()
    }
    matched_local_folders: set[str] = set()
    result: dict[str, LocalBundleInfo] = {}
    for bundle in remote_bundles:
        local = _find_matching_local_bundle(
            bundle, local_map, norm_local_map, singular_local_map, matched_local_folders
        )
        if local:
            matched_local_folders.add(local.folder_name)
            result[bundle.bundle_id] = local
    return result


def compute_sync_items(
    remote_bundles: list[RemoteBundle],
    local_map: dict[str, LocalBundleInfo],
    settings: AppSettings,
) -> list[BundleSyncItem]:
    items: list[BundleSyncItem] = []
    local_by_bundle = map_bundles_to_local(remote_bundles, local_map)
    matched_local_folders: set[str] = {loc.folder_name for loc in local_by_bundle.values()}

    for bundle in remote_bundles:
        local = local_by_bundle.get(bundle.bundle_id)
        folder = local.folder_name if local else bundle.folder_name

        prefix = folder.split("_")[0].upper() if "_" in folder else "OTHER"

        remote_icon, remote_unity, remote_zip = _select_target_remote_files(bundle)

        local_icon = local.icon_file if local else None
        local_unity = pick_latest_unity_file(local.unity_files) if (local and local.unity_files) else None
        local_zip = (
            max(local.source_zip_files, key=parse_zip_version)
            if (local and local.source_zip_files)
            else None
        )

        files_to_download: list[RemoteAssetFile] = []
        unity_update = False
        zip_update = False
        missing_any = False
        has_any_local = bool(local and (local_icon or local_unity or local_zip))

        if settings.download_icon and remote_icon:
            if not local_icon:
                files_to_download.append(remote_icon)
                missing_any = True

        if settings.download_unity and remote_unity:
            if not local_unity:
                files_to_download.append(remote_unity)
                missing_any = True
            else:
                r_u_ver = parse_unity_and_pkg_version(remote_unity.filename)
                l_u_ver = parse_unity_and_pkg_version(local_unity)
                if (r_u_ver[1], r_u_ver[0]) > (l_u_ver[1], l_u_ver[0]):
                    unity_update = True
                    files_to_download.append(remote_unity)

        if settings.download_source_zip and remote_zip:
            if not local_zip:
                files_to_download.append(remote_zip)
                missing_any = True
            else:
                if parse_zip_version(remote_zip.filename) > parse_zip_version(local_zip):
                    zip_update = True
                    files_to_download.append(remote_zip)

        if not has_any_local:
            status = SyncStatus.MISSING
        elif unity_update or zip_update:
            status = SyncStatus.UPDATE_AVAILABLE
        elif missing_any:
            status = SyncStatus.PARTIAL
        else:
            status = SyncStatus.SYNCED

        thumbnail_url = (
            f"/api/local-icon/{folder}" if local_icon else bundle.store_thumbnail_url
        )

        items.append(
            BundleSyncItem(
                folder_name=folder,
                title=bundle.title,
                prefix=prefix,
                status=status,
                thumbnail_url=thumbnail_url,
                has_local_icon=bool(local_icon),
                local_unity_file=local_unity,
                remote_unity_file=remote_unity.filename if remote_unity else None,
                unity_update_available=unity_update,
                local_zip_file=local_zip,
                remote_zip_file=remote_zip.filename if remote_zip else None,
                zip_update_available=zip_update,
                files_to_download=files_to_download,
            )
        )

    # Also surface any local folders not yet in remote_bundles (e.g. before first remote catalog refresh)
    for folder, local in local_map.items():
        if folder in matched_local_folders:
            continue
        prefix = folder.split("_")[0].upper() if "_" in folder else "OTHER"
        local_icon = local.icon_file
        local_unity = pick_latest_unity_file(local.unity_files) if local.unity_files else None
        local_zip = max(local.source_zip_files, key=parse_zip_version) if local.source_zip_files else None
        has_all = bool(local_icon and (local_unity or local_zip))
        items.append(
            BundleSyncItem(
                folder_name=folder,
                title=f"{folder.replace('_', ' ')} (Local Only)",
                prefix=prefix,
                status=SyncStatus.SYNCED if has_all else SyncStatus.PARTIAL,
                thumbnail_url=f"/api/local-icon/{folder}" if local_icon else None,
                has_local_icon=bool(local_icon),
                local_unity_file=local_unity,
                remote_unity_file=local_unity,
                unity_update_available=False,
                local_zip_file=local_zip,
                remote_zip_file=local_zip,
                zip_update_available=False,
                files_to_download=[],
            )
        )

    return sorted(items, key=lambda x: x.folder_name.lower())
