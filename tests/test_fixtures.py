"""Parser tests against sanitized real Sky Pilot markup (tests/fixtures/, captured 2026-11-22)."""

from pathlib import Path

from app.catalog import parse_bundle_detail_page, parse_library_page
from app.models import FileCategory

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_real_library_page_parses_all_bundles_and_next_link():
    stubs, next_url = parse_library_page(_read("sky_pilot_library_page.html"))
    assert len(stubs) == 15
    assert len({s["bundle_id"] for s in stubs}) == 15
    for s in stubs:
        assert "/order_items/" in s["detail_url"]
        assert s["title"]
    assert next_url and "line_items_page=2" in next_url and "logged_in_customer_id=" in next_url


def test_real_detail_page_classifies_files():
    bundle = parse_bundle_detail_page(_read("sky_pilot_bundle_detail.html"), "b1", "INTERFACE Military Combat HUD")
    assert bundle.folder_name == "INTERFACE_Military_Combat_HUD"
    by_cat = {f.category: f for f in bundle.files}
    assert by_cat[FileCategory.ICON].filename == "INTERFACE_Military_Combat_HUD_ICON.png"
    assert by_cat[FileCategory.UNITY].unity_version == (2022, 3)
    assert by_cat[FileCategory.UNITY].pkg_version == (1, 1, 1)
    assert by_cat[FileCategory.SOURCE_ZIP].filename.endswith("_Source_Sprites_v2.zip")
    assert by_cat[FileCategory.IGNORED].filename.startswith("INTERFACE_Military_Combat_HUD_Unreal")
    assert all(f.size_bytes for f in bundle.files)
    assert all("/apps/downloads/downloads/" in f.download_url for f in bundle.files)
