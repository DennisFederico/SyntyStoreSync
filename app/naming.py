import re
from pathlib import PurePath
from app.models import FileCategory

_ICON_STRIP_RE = re.compile(r"_icon\.(png|jpg|jpeg)$", re.IGNORECASE)
_ICON_CLASSIFY_RE = re.compile(r"(_icon|icons)\.(png|jpg|jpeg)$", re.IGNORECASE)
_UNREAL_RE = re.compile(r"(unreal|ureal|_ue\d|_ue_|uproject|godot)", re.IGNORECASE)
_SOURCE_ZIP_RE = re.compile(r"_source[_]?files|_source[_]?sprites", re.IGNORECASE)
_UNITY_SUFFIX_RE = re.compile(
    r"(?:_Unity(?:_Project)?|_20\d{2}_\d+).*$", re.IGNORECASE
)
_SOURCE_SUFFIX_RE = re.compile(
    r"(?:_Pack)?(?:_Unity)?_Source[_]?(?:Files|Sprites).*$", re.IGNORECASE
)
_UNITY_VER_RE = re.compile(
    r"(?:Unity_(?:Project_)?)?(20\d{2}[._]\d+(?:[._]\d+)?)", re.IGNORECASE
)
_PKG_VER_RE = re.compile(r"_v(\d+(?:[._]\d+)*)", re.IGNORECASE)
_HAS_EXT_RE = re.compile(r"\.(png|jpg|jpeg|unitypackage|zip|txt|pdf)$", re.IGNORECASE)


def normalize_sky_pilot_filename(raw: str) -> str:
    """Normalizes raw Sky Pilot heading text (with or without pipes/extensions) into a canonical filename."""
    cleaned = re.sub(r"\s*\|\s*", "_", raw.strip())
    cleaned = re.sub(r"\s+", "_", cleaned.strip(" _-|"))
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        return ""
    if _HAS_EXT_RE.search(cleaned):
        return cleaned

    lower = cleaned.lower()
    if _UNREAL_RE.search(lower):
        return f"{cleaned}.zip"
    if _SOURCE_ZIP_RE.search(lower):
        return f"{cleaned}.zip"
    if "unity" in lower or _UNITY_VER_RE.search(cleaned):
        return f"{cleaned}.unitypackage"
    if "icon" in lower:
        return f"{cleaned}.png"
    return f"{cleaned}.zip"


def classify_filename(filename: str) -> FileCategory:
    lower = filename.lower()
    if _ICON_CLASSIFY_RE.search(lower):
        return FileCategory.ICON
    if lower.endswith(".unitypackage"):
        return FileCategory.UNITY
    if _UNREAL_RE.search(lower):
        return FileCategory.IGNORED
    if lower.endswith(".zip") and _SOURCE_ZIP_RE.search(lower):
        return FileCategory.SOURCE_ZIP
    return FileCategory.IGNORED


def extract_folder_name(filename: str) -> str:
    name = PurePath(filename).name
    if _ICON_STRIP_RE.search(name):
        return _ICON_STRIP_RE.sub("", name)

    stem = PurePath(name).stem
    if name.lower().endswith(".unitypackage"):
        cleaned = _UNITY_SUFFIX_RE.sub("", stem)
        return cleaned

    if name.lower().endswith(".zip") and _SOURCE_ZIP_RE.search(name):
        cleaned = _SOURCE_SUFFIX_RE.sub("", stem)
        return cleaned

    return stem


def _to_int_tuple(raw: str) -> tuple[int, ...]:
    parts = re.split(r"[._]", raw.strip("._"))
    return tuple(int(p) for p in parts if p.isdigit())


def parse_unity_and_pkg_version(filename: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    u_match = _UNITY_VER_RE.search(filename)
    p_match = _PKG_VER_RE.search(filename)
    unity_ver = _to_int_tuple(u_match.group(1)) if u_match else (0,)
    pkg_ver = _to_int_tuple(p_match.group(1)) if p_match else (0,)
    return (unity_ver, pkg_ver)


def parse_zip_version(filename: str) -> tuple[int, ...]:
    p_match = _PKG_VER_RE.search(filename)
    return _to_int_tuple(p_match.group(1)) if p_match else (0,)


def pick_latest_unity_file(filenames: list[str]) -> str | None:
    if not filenames:
        return None
    return max(
        filenames,
        key=lambda f: (
            parse_unity_and_pkg_version(f)[1],
            parse_unity_and_pkg_version(f)[0],
        ),
    )
