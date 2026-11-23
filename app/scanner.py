from pathlib import Path
from app.models import FileCategory, LocalBundleInfo
from app.naming import classify_filename


def scan_local_directory(root_path: Path) -> dict[str, LocalBundleInfo]:
    result: dict[str, LocalBundleInfo] = {}
    try:
        if not root_path.exists() or not root_path.is_dir():
            return result
        entries = sorted(root_path.iterdir())
    except OSError:
        return result

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            folder_name = entry.name
            icon_file: str | None = None
            unity_files: list[str] = []
            source_zip_files: list[str] = []

            for file_path in sorted(entry.iterdir()):
                if not file_path.is_file() or file_path.name.endswith(".part"):
                    continue
                cat = classify_filename(file_path.name)
                if cat == FileCategory.ICON:
                    icon_file = file_path.name
                elif cat == FileCategory.UNITY:
                    unity_files.append(file_path.name)
                elif cat == FileCategory.SOURCE_ZIP:
                    source_zip_files.append(file_path.name)

            result[folder_name] = LocalBundleInfo(
                folder_name=folder_name,
                folder_path=str(entry),
                icon_file=icon_file,
                unity_files=unity_files,
                source_zip_files=source_zip_files,
            )
        except OSError:
            continue
    return result
