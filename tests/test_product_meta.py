"""Tests for per-package product metadata (dec-mwsffuxz-a6dd). Offline: uses public store data."""

import json
from pathlib import Path

from app.models import LocalBundleInfo, RemoteBundle
from app.product_meta import (
    PRODUCT_FILE_NAME,
    build_record,
    match_products,
    split_tiny_tabs,
    sync_product_files,
    thumb_keys,
    to_markdown,
    write_product_file,
)

FIXTURE = Path(__file__).parent / "fixtures" / "products_sample.json"


def _products() -> list[dict]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))["products"]
    # Same trimmed shape CatalogService.fetch_store_products stores.
    return [
        {
            "id": p["id"],
            "handle": p["handle"],
            "title": p["title"],
            "body_html": p["body_html"],
            "images": [i["src"].split("?", 1)[0] for i in p["images"]],
        }
        for p in raw
    ]


def _bundle(bid: str, title: str, folder: str, thumb: str | None = None) -> RemoteBundle:
    return RemoteBundle(bundle_id=bid, title=title, folder_name=folder, store_thumbnail_url=thumb)


def _handles(matches: dict[str, dict]) -> dict[str, str]:
    return {bid: p["handle"] for bid, p in matches.items()}


# ---- join -----------------------------------------------------------------------------------


def test_match_by_title_ignores_free_suffix_and_case():
    bundles = [
        _bundle("1", "POLYGON - Ancient Empire", "POLYGON_Ancient_Empire"),
        _bundle("2", "POLYGON - Coffee Shop | FREE", "POLYGON_CoffeeShop"),
        _bundle("3", "animation - bow combat", "ANIMATION_Bow_Combat"),
    ]
    matches, unmatched = match_products(bundles, _products())
    assert _handles(matches) == {
        "1": "polygon-ancient-empire",
        "2": "polygon-coffee-shop",
        "3": "animation-bow-combat",
    }
    assert unmatched == []


def test_match_falls_back_to_folder_name_then_thumbnail():
    products = _products()
    by_folder = _bundle("1", "Old Title Nobody Uses", "POLYGON_Sci_Fi_Frontier")
    by_thumb = _bundle(
        "2",
        "Renamed Pack",
        "POLYGON_Renamed",
        "https://cdn.shopify.com/s/files/1/2231/6843/files/"
        "POLYGON_AncientEmpire_ICON_a7e72a2c-5813-4500-8e9b-5af5111f0f5f.png?v=123",
    )
    matches, unmatched = match_products([by_folder, by_thumb], products)
    assert _handles(matches) == {"1": "polygon-sci-fi-frontier", "2": "polygon-ancient-empire"}
    assert unmatched == []


def test_generic_thumbnail_never_matches():
    generic = "https://cdn.shopify.com/s/files/1/2231/6843/files/POLYGON_Icon_UPDATED.png?v=1"
    assert thumb_keys(generic) == []
    assert thumb_keys(generic.replace(".png", "_511002d3-1d63-4bd0-9fd1-542b811360f5.png")) == []
    matches, unmatched = match_products([_bundle("1", "Unknown", "UNKNOWN_Pack", generic)], _products())
    assert matches == {} and [b.bundle_id for b in unmatched] == ["1"]


def test_ambiguous_title_is_unmatched():
    products = _products()
    dup = dict(products[0], handle="polygon-ancient-empire-copy", id=1)
    matches, unmatched = match_products(
        [_bundle("1", "POLYGON - Ancient Empire", "X_Unrelated")], products + [dup]
    )
    assert matches == {} and len(unmatched) == 1


def test_override_wins_and_ignores_unknown_handles():
    bundles = [
        _bundle("1", "POLYGON - Ancient Empire", "POLYGON_Ancient_Empire"),
        _bundle("2", "POLYGON - Coffee Shop", "POLYGON_CoffeeShop"),
    ]
    overrides = {"POLYGON_AncientEmpire_Local": "bundle-heist-city", "POLYGON_CoffeeShop": "no-such-handle"}
    matches, _ = match_products(
        bundles, _products(), overrides, {"1": "POLYGON_AncientEmpire_Local"}
    )
    assert _handles(matches) == {"1": "bundle-heist-city", "2": "polygon-coffee-shop"}


# ---- Tiny Tabs split and record -------------------------------------------------------------


def test_split_tiny_tabs_labels():
    by = {p["handle"]: p for p in _products()}
    _, tabs = split_tiny_tabs(by["polygon-ancient-empire"]["body_html"])
    assert [label for label, _ in tabs] == ["pack content", "technical info", "changelog"]
    desc, tabs = split_tiny_tabs(by["polygon-nature-biomes-season-two"]["body_html"])
    assert tabs == [] and desc == by["polygon-nature-biomes-season-two"]["body_html"]


def test_build_record_fields_and_content_by_label():
    by = {p["handle"]: p for p in _products()}
    rec = build_record(by["polygon-ancient-empire"])
    assert list(rec) == ["title", "slug", "product_id", "url", "description", "content"]
    assert rec["slug"] == "polygon-ancient-empire"
    assert rec["url"] == "https://syntystore.com/products/polygon-ancient-empire"
    assert rec["product_id"] == by["polygon-ancient-empire"]["id"]
    assert "Greek-Roman" in rec["description"] and "Key Features" in rec["description"]
    assert "Buildings (x237)" in rec["content"]
    # Technical Info / Changelog are never stored.
    assert "Changelog" not in rec["content"] and "Compatib" not in rec["description"]

    # Tab 1 is FAQs here; content is picked by label (Complete Animation List).
    emotes = build_record(by["animation-emotes-and-taunts"])
    assert emotes["content"] and "FAQ" not in emotes["content"][:200]

    # Only Technical Info / Change Log tabs, or no tabs at all -> content is null.
    assert build_record(by["polygon-coffee-shop"])["content"] is None
    assert build_record(by["polygon-nature-biomes-season-two"])["content"] is None


def test_markdown_has_no_iframe_or_nbsp():
    md = to_markdown(
        '<p><iframe src="https://www.youtube.com/embed/x"></iframe></p><p>A\xa0B</p><ul><li>One</li></ul>'
    )
    assert "iframe" not in md and "youtube" not in md and "\xa0" not in md
    assert md == "A B\n\n- One"


def test_markdown_bold_regressions():
    # Real POLYGON - Nightclubs markup (2026-11-23 refresh): nested bold, consecutive bold
    # paragraphs, a "-Including" typo and an empty <b></b>.
    md = to_markdown(
        '<p><span><strong><b>Synty® Studios</b> presents: POLYGON - Nightclubs</strong></span></p>'
        "\n<p><strong>Full demo scene.</strong></p>\n<p><strong>◼ Assets ◼</strong></p>"
        "\n<p><strong>Characters (x8)</strong><b></b></p>\n<p>-Including Beards, Hats.</p>"
    )
    assert md == (
        "**Synty® Studios presents: POLYGON - Nightclubs**\n\n"
        "**Full demo scene.**\n\n"
        "**◼ Assets ◼**\n\n"
        "**Characters (x8)**\n\n"
        "- Including Beards, Hats."
    )


# ---- writing --------------------------------------------------------------------------------


def test_write_product_file_only_on_change_and_never_creates_folders(tmp_path):
    rec = {"title": "T", "slug": "t", "product_id": 1, "url": "u", "description": "d", "content": None}
    folder = tmp_path / "POLYGON_Test"
    assert write_product_file(folder, rec) is False
    assert not folder.exists()

    folder.mkdir()
    assert write_product_file(folder, rec) is True
    target = folder / PRODUCT_FILE_NAME
    assert json.loads(target.read_text(encoding="utf-8")) == rec
    assert write_product_file(folder, rec) is False  # unchanged -> no rewrite
    assert write_product_file(folder, dict(rec, description="new")) is True
    assert not (folder / (PRODUCT_FILE_NAME + ".part")).exists()


def test_sync_product_files_writes_only_downloaded_folders(tmp_path):
    (tmp_path / "POLYGON_Ancient_Empire").mkdir()
    local_map = {
        "POLYGON_Ancient_Empire": LocalBundleInfo(
            folder_name="POLYGON_Ancient_Empire", folder_path=str(tmp_path / "POLYGON_Ancient_Empire")
        )
    }
    bundles = [
        _bundle("1", "POLYGON - Ancient Empire", "POLYGON_Ancient_Empire"),
        _bundle("2", "POLYGON - Coffee Shop | FREE", "POLYGON_CoffeeShop"),  # not downloaded
        _bundle("3", "POLYGON - Not In Store", "POLYGON_Not_In_Store"),
    ]
    summary = sync_product_files(bundles, _products(), {}, tmp_path, local_map)
    assert summary == {"matched": 2, "unmatched": 1, "unmatched_downloaded": [], "written": 1}
    assert (tmp_path / "POLYGON_Ancient_Empire" / PRODUCT_FILE_NAME).exists()
    assert not (tmp_path / "POLYGON_CoffeeShop").exists()
    assert bundles[0].product_handle == "polygon-ancient-empire" and bundles[0].product_id
    assert bundles[2].product_handle is None
    # Second run: nothing changes on disk.
    assert sync_product_files(bundles, _products(), {}, tmp_path, local_map)["written"] == 0
