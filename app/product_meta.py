"""Per-package product metadata (dec-mwsffuxz-a6dd).

Joins purchased bundles to public Synty Store products (from ``/products.json``) and builds the
``synty_product.json`` record written into each downloaded bundle folder. Everything here is pure
except ``write_product_file``.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify

from app.models import LocalBundleInfo, RemoteBundle
from app.sync_engine import _norm_key, _singular_norm_key, map_bundles_to_local

PRODUCT_FILE_NAME = "synty_product.json"
STORE_PRODUCT_BASE_URL = "https://syntystore.com/products/"

# Tiny Tabs tab labels (normalized) that hold the pack's content listing.
CONTENT_LABELS = frozenset(
    {"pack content", "pack contents", "bundle content", "animation list", "complete animation list"}
)

# Thumbnail stems shared by many products; they never identify one product.
_GENERIC_STEM_RE = re.compile(r"^(polygon_icon(_updated)?|icon_1024|large_icon_1024|syntystore)$")
_UUID_SUFFIX_RE = re.compile(r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_H5_RE = re.compile(r"<h5\b[^>]*>(.*?)</h5>", re.IGNORECASE | re.DOTALL)


def norm_title(title: str) -> str:
    """Normalizes a library or store title: drops '| FREE'-style suffixes and '&' vs 'and'."""
    cleaned = re.sub(r"\|.*$", "", title or "").lower().replace("&", "and")
    return _norm_key(cleaned)


def _singular_title(title: str) -> str:
    cleaned = re.sub(r"\|.*$", "", title or "").replace("&", "and")
    return _singular_norm_key(cleaned)


def thumb_keys(url: str | None) -> list[str]:
    """Match keys for a Shopify CDN image: full basename (no query), then the UUID-free stem.

    Generic placeholder stems (POLYGON_Icon_UPDATED, Icon_1024, ...) yield no keys.
    """
    if not url:
        return []
    name = urlsplit(url).path.rsplit("/", 1)[-1].lower()
    if not name:
        return []
    stem, _, _ext = name.rpartition(".")
    stem = stem or name
    stripped = _UUID_SUFFIX_RE.sub("", stem)
    if _GENERIC_STEM_RE.match(stripped):
        return []
    keys = [name]
    if stripped != stem:
        keys.append("stem:" + stripped)
    else:
        keys.append("stem:" + stem)
    return keys


def _index(products: list[dict]) -> dict[str, dict[str, set[str]]]:
    idx: dict[str, dict[str, set[str]]] = {"title": {}, "singular": {}, "thumb": {}}
    for p in products:
        handle = p.get("handle")
        if not handle:
            continue
        idx["title"].setdefault(norm_title(p.get("title", "")), set()).add(handle)
        idx["singular"].setdefault(_singular_title(p.get("title", "")), set()).add(handle)
        for img in p.get("images") or []:
            src = img.get("src") if isinstance(img, dict) else img
            for key in thumb_keys(src):
                idx["thumb"].setdefault(key, set()).add(handle)
    return idx


def _unique(hits: set[str] | None) -> str | None:
    return next(iter(hits)) if hits and len(hits) == 1 else None


def match_products(
    bundles: list[RemoteBundle],
    products: list[dict],
    overrides: dict[str, str] | None = None,
    local_folder_by_bundle: dict[str, str] | None = None,
) -> tuple[dict[str, dict], list[RemoteBundle]]:
    """Maps bundle_id -> product dict. Returns (matches, unmatched bundles).

    Order: override -> unique title -> unique singular title / folder name -> unique thumbnail key.
    A key that hits several products never matches.
    """
    overrides = overrides or {}
    local_folder_by_bundle = local_folder_by_bundle or {}
    by_handle = {p["handle"]: p for p in products if p.get("handle")}
    idx = _index(products)

    matches: dict[str, dict] = {}
    unmatched: list[RemoteBundle] = []
    for b in bundles:
        handle: str | None = None
        local_folder = local_folder_by_bundle.get(b.bundle_id)
        for key in (local_folder, b.folder_name):
            if key and overrides.get(key) in by_handle:
                handle = overrides[key]
                break
        if handle is None:
            handle = _unique(idx["title"].get(norm_title(b.title)))
        if handle is None:
            handle = _unique(idx["singular"].get(_singular_title(b.title)))
        if handle is None:
            for folder in (b.folder_name, local_folder):
                if folder:
                    handle = _unique(idx["title"].get(_norm_key(folder))) or _unique(
                        idx["singular"].get(_singular_norm_key(folder))
                    )
                    if handle:
                        break
        if handle is None:
            for key in thumb_keys(b.store_thumbnail_url):
                handle = _unique(idx["thumb"].get(key))
                if handle:
                    break
        if handle is None:
            unmatched.append(b)
        else:
            matches[b.bundle_id] = by_handle[handle]
    return matches, unmatched


def _norm_label(label_html: str) -> str:
    text = BeautifulSoup(label_html, "html.parser").get_text(" ").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", text)).strip()


def split_tiny_tabs(body_html: str) -> tuple[str, list[tuple[str, str]]]:
    """Splits body_html the way Tiny Tabs does on the store (heading_type 5, first_tab_excluded).

    Returns (description_html, [(normalized_label, tab_html), ...]).
    """
    body_html = body_html or ""
    heads = list(_H5_RE.finditer(body_html))
    if not heads:
        return body_html, []
    description = body_html[: heads[0].start()]
    tabs: list[tuple[str, str]] = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body_html)
        tabs.append((_norm_label(m.group(1)), body_html[m.end() : end]))
    return description, tabs


def _move_edge_breaks_out(tag) -> None:
    def _edge(index: int):
        # First/last child, skipping whitespace-only text.
        children = tag.contents if index == 0 else list(reversed(tag.contents))
        for child in children:
            if getattr(child, "name", None) is None and not str(child).strip():
                continue
            return child
        return None

    while (last := _edge(-1)) is not None and getattr(last, "name", None) == "br":
        tag.insert_after(last.extract())
    while (first := _edge(0)) is not None and getattr(first, "name", None) == "br":
        tag.insert_before(first.extract())


def to_markdown(html: str) -> str:
    """Converts a store HTML fragment to tidy Markdown (no iframes/scripts, no NBSPs)."""
    html = (html or "").replace("&nbsp;", " ").replace("\xa0", " ")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["iframe", "script", "style", "noscript", "meta"]):
        tag.decompose()
    # Bold nested in bold (<strong><b>X</b> y</strong>) would render as "****X** y**".
    for tag in soup.find_all(["b", "strong"]):
        if tag.find_parent(["b", "strong"]) or not tag.get_text(strip=True):
            tag.unwrap()
    bolds = [t for t in soup.find_all(["b", "strong"])]
    for tag in bolds:
        if tag.parent is None:
            continue  # already merged into a previous sibling
        # Adjacent bold siblings (<strong>A </strong><strong>B</strong>) would render as "**A****B**".
        sib = tag.next_sibling
        while sib is not None and getattr(sib, "name", None) in ("b", "strong"):
            nxt = sib.next_sibling
            for child in list(sib.contents):
                tag.append(child.extract())
            sib.decompose()
            sib = nxt
    # <br>s at the edges of bold/italic are dropped by markdownify, gluing text to "**".
    # Innermost first, so a <br> inside <strong><em>…<br></em></strong> bubbles all the way out.
    for tag in reversed(soup.find_all(["b", "strong", "em", "i"])):
        _move_edge_breaks_out(tag)
    md = markdownify(str(soup), heading_style="ATX", bullets="-")
    md = md.replace("\xa0", " ").replace("​", "")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n[ \t]+(?=\*\*)", "\n", md)
    md = re.sub(r"(?m)^-(?=[A-Za-z])", "- ", md)  # store typo "-Including" -> list item
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def build_record(product: dict) -> dict:
    """Builds the synty_product.json record: title, slug, product_id, url, description, content."""
    description_html, tabs = split_tiny_tabs(product.get("body_html", ""))
    content_html = next((html for label, html in tabs if label in CONTENT_LABELS), None)
    content = to_markdown(content_html) if content_html is not None else None
    handle = product["handle"]
    return {
        "title": product.get("title", ""),
        "slug": handle,
        "product_id": product.get("id"),
        "url": STORE_PRODUCT_BASE_URL + handle,
        "description": to_markdown(description_html),
        "content": content or None,
    }


def write_product_file(folder: Path, record: dict) -> bool:
    """Atomically writes <folder>/synty_product.json when its content changed. Never creates folders.

    Returns True when the file was (re)written.
    """
    if not folder.is_dir():
        return False
    target = folder / PRODUCT_FILE_NAME
    data = (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        if target.read_bytes() == data:
            return False
    except OSError:
        pass
    part = folder / (PRODUCT_FILE_NAME + ".part")
    part.write_bytes(data)
    os.replace(part, target)
    return True


def sync_product_files(
    remote_bundles: list[RemoteBundle],
    products: list[dict],
    overrides: dict[str, str],
    local_root: Path,
    local_map: dict[str, LocalBundleInfo],
) -> dict:
    """Matches bundles to products, stores the match on each bundle, and writes synty_product.json
    into every existing local folder of a matched bundle. No network.

    Returns a summary: matched / unmatched counts, unmatched downloaded titles, files written.
    """
    local_by_bundle = map_bundles_to_local(remote_bundles, local_map)
    matches, unmatched = match_products(
        remote_bundles,
        products,
        overrides,
        {bid: loc.folder_name for bid, loc in local_by_bundle.items()},
    )
    written = 0
    for b in remote_bundles:
        product = matches.get(b.bundle_id)
        b.product_id = product.get("id") if product else None
        b.product_handle = product.get("handle") if product else None
        local = local_by_bundle.get(b.bundle_id)
        if product and local and write_product_file(local_root / local.folder_name, build_record(product)):
            written += 1
    return {
        "matched": len(matches),
        "unmatched": len(unmatched),
        "unmatched_downloaded": sorted(b.title for b in unmatched if b.bundle_id in local_by_bundle),
        "written": written,
    }


def summary_message(summary: dict) -> str:
    msg = (
        f"Product info: {summary['matched']} matched, {summary['unmatched']} unmatched, "
        f"{summary['written']} file(s) written."
    )
    if summary["unmatched_downloaded"]:
        msg += " No store match for: " + ", ".join(summary["unmatched_downloaded"])
    return msg
