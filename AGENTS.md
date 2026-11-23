# AGENTS.md
Rules for AI coding agents (and humans) working on SyntyStoreSync. Read README.md first.

## Project rules

### Windows & Login Constraints

- Commands spawned inside an AI agent's background tool runner on Windows run with `CREATE_NO_WINDOW` (hidden desktop session).
- Whenever interactive Playwright login (`scripts/login.py`) is required, either ask the user to run `.\login.bat` in their own interactive terminal or spawn the process with `subprocess.CREATE_NEW_CONSOLE` from the local server (`app/auth.py` uses `subprocess.Popen`, because `uvicorn --reload` runs a SelectorEventLoop on Windows that cannot spawn asyncio subprocesses).
- Portal discovery (`scripts/discover_portal.py`, `.\discover_portal.bat`) is an opt-in developer tool. Its captures contain personal data, and it never writes the session file.
- Login rules (dec-001): the app never handles credentials, and `.data/synty_session.json` holds only the customer ID plus the storefront `_shopify_essential` cookie.

### Core Domain & Architectural Invariants

1. **On-Demand Network Operations & Rate-Limit Resilience:**
   - Never crawl Synty Store automatically on page load or server startup (`GET /api/state`).
   - Startup must read `.data/remote_catalog_cache.json` and scan the local disk (`app/scanner.py`). Remote crawling is strictly triggered by `POST /api/catalog/refresh`.
   - Maintain polite request pacing (`min_request_interval`) and automatic `HTTP 429` exponential backoff/retry + incremental checkpointing in `app/catalog.py`.
2. **Canonical Folder Naming & Multi-Candidate Matching (`<PREFIX>_<Pack_Name>`):**
   - Every bundle belongs in its own folder under the configured local Synty path (e.g., `POLYGON_Military`, `POLYGON_CoffeeShop`).
   - Derive folder names from `_ICON` filenames first (`app/naming.py:extract_folder_name`), falling back to Unity package or SourceFiles zip names.
   - Always preserve multi-candidate matching (`app/sync_engine.py:_find_matching_local_bundle`) across `_ICON`, `.unitypackage`, and `.zip` stems plus singular/plural normalization so minor naming variations between Synty's files (`POLYGON_Holiday_2025` vs `POLYGON_Holidays_2025`) bind to the existing local folder rather than creating duplicates.
3. **Selective File Filtering & Secondary Pack Exclusion:**
   - Only sync three categories (`FileCategory`):
     - `ICON`: `*_ICON.png`, `*_ICON.jpg`, `*_Icon.png`, `*Icons.png`
     - `UNITY`: Latest `*.unitypackage` (compared by `(pkg_version, unity_version)` tuple in `app/naming.py`)
     - `SOURCE_ZIP`: Latest `*_SourceFiles*.zip`, `*_Source_Files*.zip`, `*_Source_Sprites*.zip`
   - Always ignore Unreal Engine (`*Unreal*`, `*Ureal*`, `*_UE*`) and Godot (`*Godot*`) files.
   - Filter out shared secondary attachments such as `POLYGON_Generic` when parsing non-Generic bundles in `app/catalog.py` so they do not overwrite the bundle's own `SourceFiles` zip.
4. **Atomic `.part` Downloads, Safe Upgrades & Scoped Deletions:**
   - All downloads in `app/downloader.py` must stream to `<filename>.part` first.
   - Inspect `Content-Disposition` / redirected CloudFront URLs to preserve exact server filenames when provided.
   - Delete superseded older `.unitypackage` or `.zip` versions **only after** the new `.part` file has finished downloading 100% and before renaming `.part` to the final filename.
   - Folder deletion (`POST /api/bundles/delete`) and Source Zip deletion (`POST /api/bundles/delete-zip`) in `app/main.py` must strictly validate that the target folder is a direct child of `settings.local_root_path`.
5. **Uncropped Thumbnail Rendering (`object-fit: contain`):**
   - In `static/styles.css`, `.thumb-frame img` must **always** use `object-fit: contain` (never `object-fit: cover`). Synty Store uses both tall portrait cover art and square `1024x1024` `_ICON` images; both must render completely without cropping.

### Testing & Verification

- Keep business logic in pure, easily testable functions (`app/naming.py`, `app/catalog.py`, `app/sync_engine.py`).
- Run unit tests before claiming any task is complete:
  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_naming.py tests/test_sync_engine.py tests/test_catalog.py tests/test_auth.py tests/test_fixtures.py tests/test_product_meta.py -v
  ```
- Note on Sandboxed Testing: In restricted Windows sandboxes where loopback sockets (`socket.socketpair()`) are blocked, `pytest-asyncio` tests (`tests/test_downloader.py`) may require running outside the sandbox or in the user's terminal.
