from app.catalog import compute_retry_delay, parse_bundle_detail_page, parse_library_page
from app.models import FileCategory


SAMPLE_LIBRARY_HTML = """
<div class="sky-pilot rte">
  <div class="sky-pilot-files-list">
    <a class="sky-pilot-list-item" href="/apps/downloads/customers/9000000000002/orders/900000101/order_items/900000102?return_to=foo">
      <div class="sky-pilot-file-wrapper">
        <img class="sky-pilot-product-thumbnail" src="https://cdn.shopify.com/s/files/1/2231/6843/products/POLYGON_CoffeeShop_ICON.png"/>
        <div class="sky-pilot-file-heading">
          POLYGON - Coffee Shop | FREE
        </div>
      </div>
    </a>
  </div>
  <div class="sky-pilot-pagination">
    <span class="next"><a href="/apps/downloads/orders/9000000000002?logged_in_customer_id=9000000000002&amp;line_items_page=3">Next</a></span>
  </div>
</div>
"""

SAMPLE_DETAIL_HTML = """
<div class="sky-pilot rte">
  <div class="sky-pilot-files-list">
    <div class="sky-pilot-list-item">
      <div class="sky-pilot-file-wrapper">
        <div class="sky-pilot-file-heading">
          POLYGON_CoffeeShop_ICON.png
          <span class="sky-pilot-file-size">(687 KB)</span>
        </div>
        <div class="sky-pilot-actions">
          <a class="button sky-pilot-button" href="/apps/downloads/downloads/900201?email=test%40example.com&amp;order_id=900000101&amp;order_item_id=900000102">Download</a>
        </div>
      </div>
    </div>
    <div class="sky-pilot-list-item">
      <div class="sky-pilot-file-wrapper">
        <div class="sky-pilot-file-heading">
          POLYGON_Coffee_Shop
          <br/>
          <span>SourceFiles | v2</span>
          <span class="sky-pilot-file-size">(1.33 MB)</span>
        </div>
        <div class="sky-pilot-actions">
          <a class="button sky-pilot-button" href="/apps/downloads/downloads/9000202?email=test%40example.com&amp;order_id=900000101&amp;order_item_id=900000102">Download</a>
        </div>
      </div>
    </div>
    <div class="sky-pilot-list-item">
      <div class="sky-pilot-file-wrapper">
        <div class="sky-pilot-file-heading">
          POLYGON_Generic
          <br/>
          <span>SourceFiles | v3</span>
          <span class="sky-pilot-file-size">(85.8 MB)</span>
        </div>
        <div class="sky-pilot-actions">
          <a class="button sky-pilot-button" href="/apps/downloads/downloads/9000203?email=test%40example.com&amp;order_id=900000101&amp;order_item_id=900000102">Download</a>
        </div>
      </div>
    </div>
    <div class="sky-pilot-list-item">
      <div class="sky-pilot-file-wrapper">
        <div class="sky-pilot-file-heading">
          POLYGON_Coffee_Shop
          <br/>
          <span>Unity_2022_3 | v1_2_2</span>
          <span class="sky-pilot-file-size">(106 MB)</span>
        </div>
        <div class="sky-pilot-actions">
          <a class="button sky-pilot-button" href="/apps/downloads/downloads/900204?email=test%40example.com&amp;order_id=900000101&amp;order_item_id=900000102">Download</a>
        </div>
      </div>
    </div>
    <div class="sky-pilot-list-item">
      <div class="sky-pilot-file-wrapper">
        <div class="sky-pilot-file-heading">
          POLYGON_Coffee_Shop
          <br/>
          <span>Unreal_4_25 | v1_01</span>
          <span class="sky-pilot-file-size">(3.52 MB)</span>
        </div>
        <div class="sky-pilot-actions">
          <a class="button sky-pilot-button" href="/apps/downloads/downloads/9000205?email=test%40example.com&amp;order_id=900000101&amp;order_item_id=900000102">Download</a>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def test_parse_library_and_bundle_detail():
    stubs, next_url = parse_library_page(SAMPLE_LIBRARY_HTML)
    assert len(stubs) == 1
    assert stubs[0]["bundle_id"] == "900000101_900000102"
    assert stubs[0]["title"] == "POLYGON - Coffee Shop | FREE"
    assert next_url == "https://syntystore.com/apps/downloads/orders/9000000000002?logged_in_customer_id=9000000000002&line_items_page=3"

    bundle = parse_bundle_detail_page(
        SAMPLE_DETAIL_HTML,
        bundle_id=stubs[0]["bundle_id"],
        title=stubs[0]["title"],
        store_thumbnail_url=stubs[0]["store_thumbnail_url"],
    )
    assert bundle.folder_name == "POLYGON_CoffeeShop"
    # POLYGON_Generic should be filtered out from POLYGON_CoffeeShop
    filenames = [f.filename for f in bundle.files]
    assert "POLYGON_CoffeeShop_ICON.png" in filenames
    assert "POLYGON_Coffee_Shop_SourceFiles_v2.zip" in filenames
    assert "POLYGON_Coffee_Shop_Unity_2022_3_v1_2_2.unitypackage" in filenames
    assert not any("POLYGON_Generic" in name for name in filenames)

    by_cat = {f.category: f for f in bundle.files if f.category != FileCategory.IGNORED}
    assert by_cat[FileCategory.ICON].filename == "POLYGON_CoffeeShop_ICON.png"
    assert by_cat[FileCategory.SOURCE_ZIP].pkg_version == (2,)
    assert by_cat[FileCategory.UNITY].unity_version == (2022, 3)
    assert by_cat[FileCategory.UNITY].pkg_version == (1, 2, 2)


def test_compute_retry_delay():
    assert compute_retry_delay("5", 0) == 5.5
    assert compute_retry_delay(None, 0, base_delay=3.5) == 3.5
    assert compute_retry_delay(None, 2, base_delay=3.5) > compute_retry_delay(None, 1, base_delay=3.5)


def test_refresh_aborts_without_overwriting_cache_when_library_is_empty(tmp_path, monkeypatch):
    import asyncio
    import json

    import httpx
    import pytest

    import app.catalog as catalog_mod
    from app.catalog import CatalogService

    class _Auth:
        def get_customer_id(self):
            return "123"

        def get_cookies(self):
            return {"_shopify_essential": "x"}

        def mark_verified(self):
            raise AssertionError("an empty library must not mark the session verified")

    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps([{"bundle_id": "1", "title": "T", "folder_name": "POLYGON_Test", "files": []}]))

    logged_out_html = "<html><body><a href='/account/login'>Log in</a></body></html>"
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=logged_out_html))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog_mod.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )

    svc = CatalogService(auth_service=_Auth(), cache_file=cache, min_request_interval=0)
    from app.auth import SessionExpiredError

    with pytest.raises(SessionExpiredError, match="session has probably expired"):
        asyncio.run(svc.fetch_remote_catalog())

    assert [b.folder_name for b in svc.load_cached_catalog()] == ["POLYGON_Test"]


def _store_service(tmp_path, monkeypatch, handler):
    import httpx

    import app.catalog as catalog_mod
    from app.catalog import CatalogService

    seen = []

    def _wrapped(req):
        seen.append(req)
        return handler(req)

    transport = httpx.MockTransport(_wrapped)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        catalog_mod.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )
    svc = CatalogService(
        auth_service=None,
        cache_file=tmp_path / "cache.json",
        min_request_interval=0,
        store_products_file=tmp_path / "store_products.json",
        overrides_file=tmp_path / "overrides.json",
    )
    return svc, seen


def test_fetch_store_products_is_cookieless_and_stops_at_short_page(tmp_path, monkeypatch):
    import asyncio

    import httpx

    def handler(req):
        page = int(req.url.params["page"])
        count = 250 if page == 1 else 3
        products = [
            {"id": page * 1000 + i, "handle": f"p{page}-{i}", "title": f"P {i}", "body_html": "<p>x</p>",
             "images": [{"src": f"https://cdn.shopify.com/a/{i}.png?v=9"}], "variants": [{"price": "9.99"}]}
            for i in range(count)
        ]
        return httpx.Response(200, json={"products": products})

    svc, seen = _store_service(tmp_path, monkeypatch, handler)
    products = asyncio.run(svc.fetch_store_products())

    assert len(products) == 253
    assert [r.url.params["page"] for r in seen] == ["1", "2"]
    assert all("cookie" not in r.headers for r in seen)
    assert all(r.url.path == "/products.json" for r in seen)
    assert set(products[0]) == {"id", "handle", "title", "body_html", "images"}  # no price
    assert products[0]["images"] == ["https://cdn.shopify.com/a/0.png"]
    assert len(svc.load_store_products()) == 253


def test_fetch_store_products_failure_keeps_previous_cache(tmp_path, monkeypatch):
    import asyncio
    import json

    import httpx

    svc, _ = _store_service(tmp_path, monkeypatch, lambda req: httpx.Response(500))
    svc.store_products_file.write_text(json.dumps([{"handle": "old"}]), encoding="utf-8")
    messages = []
    assert asyncio.run(svc.fetch_store_products(progress_cb=messages.append)) is None
    assert svc.load_store_products() == [{"handle": "old"}]
    assert any("keeping the previous copy" in m["message"] for m in messages)


def test_load_overrides_tolerates_missing_or_broken_file(tmp_path, monkeypatch):
    svc, _ = _store_service(tmp_path, monkeypatch, lambda req: None)
    assert svc.load_overrides() == {}
    svc.overrides_file.write_text("{not json", encoding="utf-8")
    assert svc.load_overrides() == {}
    svc.overrides_file.write_text('{"POLYGON_X": "polygon-x", "BAD": 3}', encoding="utf-8")
    assert svc.load_overrides() == {"POLYGON_X": "polygon-x"}
