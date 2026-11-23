# SyntyStoreSync

**SyntyStoreSync** is a local Python + FastAPI web application that synchronizes your owned [Synty Store](https://syntystore.com/) 3D & 2D game asset bundles into a clean, version-tracked offline directory on your drive.

*Note*: This is an AI-driven, Human supervised and reviewed development.

---

## The Problem It Solves

If you own dozens or hundreds of packs on [Synty Store](https://syntystore.com/) (via individual purchases, bundles, or Synty Pass), keeping a local offline library for Unity development quickly becomes tedious:

1. **Manual Paginated Downloading:** Synty Store's "My Downloads" portal (powered by Sky Pilot) paginates your library ~15 packs per page and requires clicking into each pack individually to see its files.
2. **Silent Version Updates:** When Synty updates a pack (e.g., `v1.9.0` → `v1.10.4` or `Unity 2021.3` → `Unity 2022.3`), there is no overview showing which of your local folders are outdated.
3. **Mixed Engine Clutter:** Each bundle page mixes Unity packages (`.unitypackage`), Source Files (`.zip`), Unreal Engine archives (`Unreal_4_25`, `Unreal_5_3`), Godot packs, and shared `POLYGON_Generic` attachments.
4. **Disk Space Management:** Source files (`.zip` FBX/OBJ/Textures) can take tens of gigabytes across a large library, and deleting or re-downloading them selectively by hand is error-prone.

**SyntyStoreSync** solves all of this by providing a local visual dashboard (`http://127.0.0.1:8765`) that compares your Synty Store library against your local asset directory, highlights **Missing**, **Update Available**, **Partial**, and **Synced** packs, and downloads or cleans up files with a single click.

---

## Key Features

- **100% On-Demand Crawling & Instant Startup:**
  - Opening the dashboard loads immediately from your local disk scan and cached catalog (`.data/remote_catalog_cache.json`).
  - Synty Store is **only** crawled when you explicitly click **"Refresh Remote Catalog"**, using polite request pacing, automatic `HTTP 429` rate-limit backoff/retry, and incremental checkpointing.
- **Guided Browser Authentication:**
  - Log into Synty Store in a real Chrome window (`Login to Synty Store` button or `login.bat`) using the emailed code or **Continue with Shop**. The window closes by itself once *My Downloads* has loaded.
  - The app never sees your email or code. It saves only your customer ID and one storefront session cookie in `.data/synty_session.json`. A Chrome profile in `.data/chrome_profile/` keeps you signed in for later logins.
  - The header badge shows `Login Required`, `Session saved (expires …)`, `Verified …` (after a successful catalog refresh) or `Session Expired`.
- **Canonical Folder Organization (`<PREFIX>_<Pack_Name>`):**
  - Organizes every asset bundle into its own dedicated subfolder (e.g., `POLYGON_Military`, `POLYGON_CoffeeShop`, `SIMPLE_Cars`, `INTERFACE_Apocalypse_HUD`, `ANIMATION_Bow_Combat`, `SIDEKICK_Starter`).
  - Uses multi-candidate matching (`_ICON`, `.unitypackage`, and `.zip` stems + singular/plural normalization) so existing local folders always bind cleanly even when Synty's internal filenames vary slightly (`POLYGON_Holiday_2025` vs `POLYGON_Holidays_2025`).
- **Selective Engine & File Filtering:**
  - Syncs only the three Unity-relevant categories (each individually toggleable in the toolbar):
    - **Icon** (`*_ICON.png` / `.jpg`)
    - **Unity Package** (automatically picks the newest `(pkg_version, unity_version)` `.unitypackage`)
    - **Source Files** (automatically picks the newest `*_SourceFiles*.zip` / `*_Source_Sprites*.zip` while ignoring secondary `POLYGON_Generic` attachments on non-Generic packs)
  - Automatically ignores Unreal Engine (`*Unreal*`, `*_UE*`) and Godot files.
- **Uncropped Thumbnail Previews (`object-fit: contain`):**
  - Renders both tall portrait Synty Store cover images and square `1024×1024` `_ICON` files completely without cropping.
  - Uses the Synty Store CDN thumbnail when an asset is missing locally, and automatically switches to serving your local high-res `_ICON` file once downloaded.
- **Atomic `.part` Downloads & Safe Upgrades:**
  - Streams downloads to `<filename>.part` first, preserves exact server `Content-Disposition` filenames, and deletes superseded older `.unitypackage` / `.zip` versions **only after** the new version finishes downloading 100%.
  - Broadcasts real-time download progress and transfer speed (`MB/s`) via Server-Sent Events (SSE).
- **Per-Card & Disk Cleanup Actions:**
  - **🌐 Product Page:** Opens the official Synty Store product description page in a new browser tab (with fallback to the bundle's Sky Pilot download page).
  - **📁 Open Folder:** Opens the bundle's local folder (or the root Synty folder from the top bar) in **Windows Explorer** or **macOS Finder** and brings the window to the foreground.
  - **🗑️ Delete Source Zip Only (Inline):** A compact trash button on the **Source Zip** checklist row lets you delete just the local `.zip` archive to reclaim disk space while keeping the `.unitypackage` and `_ICON`.
  - **🗑️ Delete Entire Asset Folder:** Deletes the bundle's local subfolder, immediately reverting its status to `MISSING` and switching its preview back to the Synty Store CDN thumbnail.

---

## Installation & Setup

### Prerequisites
- **Python 3.11+** (tested with Python 3.13)
- **Google Chrome** (preferred). Falls back to **Microsoft Edge**, then to Playwright's bundled **Chromium**.

### 1. Clone the Repository & Create a Virtual Environment

**Windows (PowerShell):**
```powershell
git clone <your-repo-url> SyntyStoreSync
cd SyntyStoreSync
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\playwright install chromium
```

**macOS / Linux (Bash / Zsh):**
```bash
git clone <your-repo-url> SyntyStoreSync
cd SyntyStoreSync
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/playwright install chromium
chmod +x run.sh
```

---

## Usage

### 1. Start the Application

- **Windows:** Double-click **`run.bat`** or run `.\run.bat` in PowerShell.
- **macOS / Linux:** Run `./run.sh` in your terminal.

The local web interface will open automatically at **`http://127.0.0.1:8765`**.

### 2. Set Your Local Synty Directory
- In the top header bar, enter the path to your local Synty archive folder (e.g., `D:\Unity\Synty` on Windows or `~/Unity/Synty` on macOS/Linux) and click **"Rescan Local"**.

### 3. Log In & Refresh Your Remote Catalog
1. Click **"Login to Synty Store"** (or run `.\login.bat` on Windows).
2. Sign into your Synty Store account in the browser window that opens, navigate to **My Downloads**, and close the browser window when your library loads.
3. Back in the SyntyStoreSync dashboard, click **"Refresh Remote Catalog"** to crawl your owned bundles and compare them against your local folder.

### Product info files (`synty_product.json`)
Each **Refresh Remote Catalog** also makes one request for Synty Store's public product list
(`syntystore.com/products.json`, no cookies). The app matches each purchase to its product by title, then
writes `synty_product.json` into every bundle folder that already exists on disk. New downloads get theirs
when they finish, from the cached list. The app never creates a folder just for this file.

```json
{ "title": "...", "slug": "...", "product_id": 123, "url": "https://syntystore.com/products/...",
  "description": "Markdown", "content": "Markdown (the Pack Content tab), or null" }
```
- A pack that matches no product gets no file. Refresh prints the unmatched titles in the server console.
- To fix a miss by hand, create `.data/product_overrides.json`: `{ "<local folder name>": "<product slug>" }`.

### 4. Filter, Download, Update & Clean Up
- Filter cards by status (**All**, **Missing**, **Update Available**, **Partial**, **Synced**) or prefix (**POLYGON**, **SIMPLE**, **INTERFACE**, **ANIMATION**, **SIDEKICK**).
- Toggle **Sync Targets** (`Icon`, `Unity`, `Source Files`) depending on what you want to keep on disk.
- Click **Download / Update** on individual cards, or click **Select Actionable → Download Selected (N)** for batch syncing.

---

## Project Structure

```text
SyntyStoreSync/
├── app/
│   ├── __init__.py
│   ├── auth.py          # Minimal session store (.data/synty_session.json), status & login launcher
│   ├── catalog.py       # Sky Pilot library crawler, rate-limit backoff & HTML parser
│   ├── downloader.py    # Async httpx download queue, .part atomic writes & SSE events
│   ├── main.py          # FastAPI REST & SSE endpoints + OS folder/product-page handlers
│   ├── models.py        # Pydantic data models & enums
│   ├── naming.py        # Filename normalizer, classifier & version tuple parser
│   ├── product_meta.py  # Purchase-to-store-product join & synty_product.json writer
│   ├── scanner.py       # Local directory scanner
│   └── sync_engine.py   # Multi-candidate remote-vs-local comparator & status engine
├── scripts/
│   ├── browser_launch.py   # Shared Chrome/Edge/Chromium launcher (persistent .data/chrome_profile)
│   ├── login.py            # Guided login: saves the minimal session, auto-closes
│   └── discover_portal.py  # Opt-in developer tool: captures portal traffic to .data/discovery/
├── static/
│   ├── index.html       # Single-page dashboard layout
│   ├── styles.css       # Dark theme with uncropped (object-fit: contain) cards
│   └── app.js           # Frontend state, filtering, bulk actions & SSE progress drawer
├── tests/
│   ├── test_catalog.py     # Unit tests for Sky Pilot HTML parsing & retry backoff
│   ├── test_downloader.py  # Unit tests for atomic .part downloads & version cleanup
│   ├── test_naming.py      # Unit tests for filename classification & version comparison
│   ├── test_product_meta.py # Unit tests for product matching, Tiny Tabs split & file writes
│   └── test_sync_engine.py # Unit tests for local scanner & sync engine
├── login.bat            # Standalone Windows login helper
├── discover_portal.bat  # Developer portal-discovery helper (captures personal data)
├── run.bat              # Windows one-click launcher
├── run.sh               # macOS / Linux launcher
├── AGENTS.md            # Architectural invariants & guidelines for AI coding assistants
└── requirements.txt
```

---

## Privacy & Security Note

The app never handles your credentials: you sign in inside Chrome. Everything it keeps lives in the local `.data/` directory, which `.gitignore` excludes:
- `synty_session.json`: your customer ID and one storefront cookie (`_shopify_essential`).
- `chrome_profile/`: the Chrome profile used only by the login window, which keeps you signed in.
- `store_products_cache.json`, `product_overrides.json`: public store product data and your optional manual matches.
- `remote_catalog_cache.json`: your catalog. Synty's download links embed your account email and work without a session, so treat this file as private.
- `discovery/` (only if you run `discover_portal.bat`): raw portal captures with personal data. Delete it when done.

Test fixtures in `tests/fixtures/` are sanitized (see `tests/fixtures/README.md`).

---

## Running Unit Tests

```powershell
# Windows
.\.venv\Scripts\python.exe -m pytest -v

# macOS / Linux
./.venv/bin/python -m pytest -v
```
