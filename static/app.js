const state = {
  items: [],
  counts: { all: 0, missing: 0, update_available: 0, partial: 0, synced: 0 },
  settings: {
    local_root_path: "", // filled from /api/state on load
    download_icon: true,
    download_unity: true,
    download_source_zip: true,
    max_concurrent_downloads: 2,
  },
  statusFilter: "all",
  prefixFilter: "ALL",
  searchQuery: "",
  selectedFolders: new Set(),
  activeDownloads: new Map(),
};

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let val = bytes;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

function shortVersion(filename) {
  if (!filename) return null;
  const mUnity = filename.match(/(20\d{2}[._]\d+(?:[._]\d+)?).*?_v(\d+(?:[._]\d+)*)/i);
  if (mUnity) {
    return `Unity ${mUnity[1].replaceAll("_", ".")} (v${mUnity[2].replaceAll("_", ".")})`;
  }
  const mVer = filename.match(/_v(\d+(?:[._]\d+)*)/i);
  if (mVer) {
    return `v${mVer[1].replaceAll("_", ".")}`;
  }
  return "Present";
}

async function fetchState() {
  const res = await fetch("/api/state");
  const data = await res.json();
  applyServerState(data);
}

function formatWhen(value, withTime) {
  if (!value) return "?";
  const d = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(d.getTime())) return "?";
  return withTime
    ? d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

// Three local-only states (dec-001): no network call is made to decide them.
function renderAuthBadge(data) {
  const badge = document.getElementById("authBadge");
  const session = data.session || { state: data.authenticated ? "saved" : "none" };
  const counts = `${data.remote_bundle_count} Remote / ${data.local_folder_count} Local`;
  if (session.state === "verified") {
    badge.textContent = `Verified ${formatWhen(session.verified_at, true)} · ${counts}`;
    badge.className = "auth-badge connected";
    badge.title = "The last catalog refresh succeeded with this session.";
  } else if (session.state === "saved") {
    badge.textContent = `Session saved (expires ${formatWhen(session.cookie_expires_at, false)}) · ${counts}`;
    badge.className = "auth-badge saved";
    badge.title = "Not yet confirmed by Synty Store. Refresh the remote catalog to verify.";
  } else if (session.state === "expired") {
    badge.textContent = "Session Expired · Login Required";
    badge.className = "auth-badge disconnected";
    badge.title = "Synty Store rejected the saved session, or its cookie has expired.";
  } else {
    badge.textContent = "Login Required";
    badge.className = "auth-badge disconnected";
    badge.title = "No Synty Store session saved yet.";
  }
}

function applyServerState(data) {
  state.items = data.items || [];
  state.counts = data.counts || state.counts;
  state.settings = data.settings || state.settings;

  renderAuthBadge(data);

  document.getElementById("localPathInput").value = state.settings.local_root_path;
  document.getElementById("toggleIcon").checked = state.settings.download_icon;
  document.getElementById("toggleUnity").checked = state.settings.download_unity;
  document.getElementById("toggleZip").checked = state.settings.download_source_zip;

  for (const key of ["all", "missing", "update_available", "partial", "synced"]) {
    const el = document.getElementById(`count-${key}`);
    if (el) el.textContent = state.counts[key] ?? 0;
  }

  const notice = document.getElementById("emptyNotice");
  if (!data.has_cached_catalog) {
    notice.classList.remove("hidden");
    notice.innerHTML = `
      <strong>Remote Catalog Not Yet Cached</strong><br/>
      Showing <b>${data.local_folder_count}</b> local folders from <code>${state.settings.local_root_path}</code>.
      Click <b>"Refresh Remote Catalog"</b> in the top-right to crawl your Synty Store library and check for missing packs or updates.
    `;
  } else {
    notice.classList.add("hidden");
  }

  renderGrid();
}

function getFilteredItems() {
  const q = state.searchQuery.trim().toLowerCase();
  return state.items.filter((item) => {
    if (state.statusFilter !== "all" && item.status !== state.statusFilter) return false;
    if (state.prefixFilter !== "ALL" && item.prefix !== state.prefixFilter) return false;
    if (q) {
      const hay = `${item.folder_name} ${item.title}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function updateBulkButton() {
  const btn = document.getElementById("downloadSelectedBtn");
  const count = state.selectedFolders.size;
  btn.textContent = `Download Selected (${count})`;
  btn.disabled = count === 0;
}

function renderGrid() {
  const grid = document.getElementById("bundleGrid");
  const filtered = getFilteredItems();
  grid.innerHTML = "";

  for (const item of filtered) {
    const card = document.createElement("div");
    const isSelected = state.selectedFolders.has(item.folder_name);
    card.className = `bundle-card${isSelected ? " selected" : ""}`;

    const statusLabels = {
      synced: "Synced",
      update_available: "Update Available",
      partial: "Partial",
      missing: "Missing",
    };

    const thumbHtml = item.thumbnail_url
      ? `<img src="${item.thumbnail_url}" alt="${item.folder_name}" loading="lazy" />`
      : `<div class="thumb-placeholder">No Preview</div>`;

    const iconRow = item.has_local_icon
      ? `<span class="val ok">Local</span>`
      : `<span class="val miss">Missing</span>`;

    let unityRow = `<span class="val na">N/A</span>`;
    if (item.unity_update_available) {
      unityRow = `<span class="val diff">${shortVersion(item.local_unity_file)} &rarr; ${shortVersion(item.remote_unity_file)}</span>`;
    } else if (item.local_unity_file) {
      unityRow = `<span class="val ok">${shortVersion(item.local_unity_file)}</span>`;
    } else if (item.remote_unity_file) {
      unityRow = `<span class="val miss">Missing (${shortVersion(item.remote_unity_file)})</span>`;
    }

    const inlineZipDelBtn = item.local_zip_file
      ? `<button class="btn-inline-delete card-del-zip-btn" title="Delete local Source Zip (${item.local_zip_file}) only">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <polyline points="3 6 5 6 21 6"></polyline>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
            <path d="M10 11v6"></path>
            <path d="M14 11v6"></path>
            <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
          </svg>
        </button>`
      : "";

    let zipRow = `<span class="val na">N/A</span>`;
    if (item.zip_update_available) {
      zipRow = `<span class="val diff">${shortVersion(item.local_zip_file)} &rarr; ${shortVersion(item.remote_zip_file)} ${inlineZipDelBtn}</span>`;
    } else if (item.local_zip_file) {
      zipRow = `<span class="val ok">${shortVersion(item.local_zip_file)} ${inlineZipDelBtn}</span>`;
    } else if (item.remote_zip_file) {
      zipRow = `<span class="val miss">Missing (${shortVersion(item.remote_zip_file)})</span>`;
    }

    const canDownload = item.files_to_download && item.files_to_download.length > 0;
    const hasLocalFolder = item.status !== "missing";
    const btnLabel =
      item.status === "update_available"
        ? `Update (${item.files_to_download.length})`
        : canDownload
        ? `Download (${item.files_to_download.length})`
        : "Up to Date";

    card.innerHTML = `
      <div class="thumb-frame">
        <input type="checkbox" class="card-select-checkbox" ${isSelected ? "checked" : ""} />
        <span class="status-badge status-${item.status}">${statusLabels[item.status] || item.status}</span>
        ${thumbHtml}
      </div>
      <div class="card-body">
        <div>
          <div class="folder-name">${item.folder_name}</div>
          <div class="bundle-title">${item.title}</div>
        </div>
        <ul class="file-checklist">
          <li class="file-row"><span class="label">Icon</span>${iconRow}</li>
          <li class="file-row"><span class="label">Unity</span>${unityRow}</li>
          <li class="file-row"><span class="label">Source Zip</span>${zipRow}</li>
        </ul>
      </div>
      <div class="card-footer">
        <span class="bundle-title">${item.prefix}</span>
        <div class="card-actions">
          <button class="btn btn-secondary btn-icon card-web-btn" title="Open Synty Store product page">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9"></circle>
              <line x1="3.6" y1="9" x2="20.4" y2="9"></line>
              <line x1="3.6" y1="15" x2="20.4" y2="15"></line>
              <path d="M11.5 3a17 17 0 0 0 0 18"></path>
              <path d="M12.5 3a17 17 0 0 1 0 18"></path>
            </svg>
          </button>
          <button class="btn btn-secondary btn-icon card-open-btn" title="Open asset folder in Explorer / Finder" ${hasLocalFolder ? "" : "disabled"}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"></path>
            </svg>
          </button>
          <button class="btn btn-sm ${canDownload ? "btn-primary" : "btn-secondary"} card-dl-btn" ${canDownload ? "" : "disabled"}>
            ${btnLabel}
          </button>
          <button class="btn btn-secondary btn-icon btn-icon-danger card-delete-btn" title="Delete local asset folder" ${hasLocalFolder ? "" : "disabled"}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
              <path d="M10 11v6"></path>
              <path d="M14 11v6"></path>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
            </svg>
          </button>
        </div>
      </div>
    `;

    const checkbox = card.querySelector(".card-select-checkbox");
    checkbox.addEventListener("change", (e) => {
      if (e.target.checked) {
        state.selectedFolders.add(item.folder_name);
        card.classList.add("selected");
      } else {
        state.selectedFolders.delete(item.folder_name);
        card.classList.remove("selected");
      }
      updateBulkButton();
    });

    const webBtn = card.querySelector(".card-web-btn");
    webBtn.addEventListener("click", () => {
      window.open(`/api/bundles/product-page/${encodeURIComponent(item.folder_name)}`, "_blank", "noopener");
    });

    const openBtn = card.querySelector(".card-open-btn");
    openBtn.addEventListener("click", async () => {
      const res = await fetch("/api/open-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_name: item.folder_name }),
      });
      if (!res.ok) {
        const err = await res.json();
        alert(`Could not open folder: ${err.detail || res.statusText}`);
      }
    });

    const dlBtn = card.querySelector(".card-dl-btn");
    dlBtn.addEventListener("click", () => startDownloads([item.folder_name]));

    const delZipBtn = card.querySelector(".card-del-zip-btn");
    if (delZipBtn) {
      delZipBtn.addEventListener("click", async () => {
        if (!confirm(`Delete local Source Zip "${item.local_zip_file}" from "${item.folder_name}"?`)) {
          return;
        }
        delZipBtn.disabled = true;
        try {
          const res = await fetch("/api/bundles/delete-zip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder_name: item.folder_name }),
          });
          if (!res.ok) {
            const err = await res.json();
            alert(`Could not delete Source Zip: ${err.detail || res.statusText}`);
          } else {
            const data = await res.json();
            applyServerState(data);
          }
        } finally {
          delZipBtn.disabled = false;
        }
      });
    }

    const deleteBtn = card.querySelector(".card-delete-btn");
    deleteBtn.addEventListener("click", async () => {
      if (!confirm(`Delete local folder "${item.folder_name}" and all its downloaded files?`)) {
        return;
      }
      deleteBtn.disabled = true;
      try {
        const res = await fetch("/api/bundles/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_name: item.folder_name }),
        });
        if (!res.ok) {
          const err = await res.json();
          alert(`Could not delete folder: ${err.detail || res.statusText}`);
        } else {
          state.selectedFolders.delete(item.folder_name);
          const data = await res.json();
          applyServerState(data);
        }
      } finally {
        deleteBtn.disabled = false;
      }
    });

    grid.appendChild(card);
  }
  updateBulkButton();
}

async function saveSettingsFromUI() {
  const payload = {
    local_root_path: document.getElementById("localPathInput").value.trim(),
    download_icon: document.getElementById("toggleIcon").checked,
    download_unity: document.getElementById("toggleUnity").checked,
    download_source_zip: document.getElementById("toggleZip").checked,
    max_concurrent_downloads: state.settings.max_concurrent_downloads || 2,
  };
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  applyServerState(data);
}

async function startDownloads(folderNames) {
  if (!folderNames.length) return;
  document.getElementById("downloadDrawer").classList.remove("hidden");
  await fetch("/api/downloads/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_names: folderNames }),
  });
}

function renderDownloadDrawer() {
  const list = document.getElementById("downloadList");
  const summary = document.getElementById("drawerSummary");
  list.innerHTML = "";
  const entries = Array.from(state.activeDownloads.values());
  summary.textContent = `${entries.length} file(s) tracked`;

  for (const d of entries) {
    const pct = d.total_bytes > 0 ? Math.min(100, Math.round((d.downloaded_bytes / d.total_bytes) * 100)) : d.done ? 100 : 0;
    const speedText = d.done
      ? "Completed"
      : d.error
      ? `Error: ${d.error}`
      : `${formatBytes(d.downloaded_bytes)} / ${d.total_bytes ? formatBytes(d.total_bytes) : "?"} (${formatBytes(d.speed_bps)}/s)`;

    const row = document.createElement("div");
    row.className = "dl-item";
    row.innerHTML = `
      <div class="dl-top">
        <strong>${d.folder_name} / ${d.filename}</strong>
        <span>${speedText}</span>
      </div>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" style="width: ${pct}%"></div>
      </div>
    `;
    list.appendChild(row);
  }
}

function connectSSE() {
  const es = new EventSource("/api/downloads/stream");
  es.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "catalog_progress") {
        const banner = document.getElementById("crawlBanner");
        banner.classList.remove("hidden");
        document.getElementById("crawlMessage").textContent = msg.message;
      } else if (msg.type === "catalog_completed") {
        document.getElementById("crawlBanner").classList.add("hidden");
      } else if (msg.type === "file_started" || msg.type === "file_progress") {
        const key = `${msg.folder_name}/${msg.filename}`;
        state.activeDownloads.set(key, {
          folder_name: msg.folder_name,
          filename: msg.filename,
          downloaded_bytes: msg.downloaded_bytes || 0,
          total_bytes: msg.total_bytes || 0,
          speed_bps: msg.speed_bps || 0,
          done: false,
        });
        document.getElementById("downloadDrawer").classList.remove("hidden");
        renderDownloadDrawer();
      } else if (msg.type === "file_completed") {
        const key = `${msg.folder_name}/${msg.filename}`;
        const prev = state.activeDownloads.get(key) || { folder_name: msg.folder_name, filename: msg.filename };
        prev.done = true;
        state.activeDownloads.set(key, prev);
        renderDownloadDrawer();
      } else if (msg.type === "file_error") {
        const key = `${msg.folder_name}/${msg.filename}`;
        state.activeDownloads.set(key, {
          folder_name: msg.folder_name,
          filename: msg.filename,
          downloaded_bytes: 0,
          total_bytes: 0,
          speed_bps: 0,
          error: msg.error,
        });
        renderDownloadDrawer();
      } else if (msg.type === "download_link_invalid") {
        alert(`Downloads stopped: ${msg.message}`);
      } else if (msg.type === "bundle_completed") {
        fetchState();
      }
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };
}

document.addEventListener("DOMContentLoaded", () => {
  fetchState();
  connectSSE();

  document.getElementById("searchInput").addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderGrid();
  });

  document.querySelectorAll("#statusTabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#statusTabs .tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.statusFilter = btn.dataset.status;
      renderGrid();
    });
  });

  document.querySelectorAll("#prefixPills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#prefixPills .pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.prefixFilter = btn.dataset.prefix;
      renderGrid();
    });
  });

  document.getElementById("rescanBtn").addEventListener("click", saveSettingsFromUI);
  document.getElementById("openFolderBtn").addEventListener("click", async () => {
    const res = await fetch("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Could not open folder: ${err.detail || res.statusText}`);
    }
  });
  document.getElementById("toggleIcon").addEventListener("change", saveSettingsFromUI);
  document.getElementById("toggleUnity").addEventListener("change", saveSettingsFromUI);
  document.getElementById("toggleZip").addEventListener("change", saveSettingsFromUI);

  document.getElementById("loginBtn").addEventListener("click", async () => {
    const btn = document.getElementById("loginBtn");
    btn.disabled = true;
    btn.textContent = "Waiting for Login Browser...";
    try {
      const res = await fetch("/api/auth/login", { method: "POST" });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          detail = (await res.json()).detail || detail;
        } catch (_) {}
        alert(`Login failed: ${detail}`);
        return;
      }
      const data = await res.json();
      applyServerState(data);
      if (!data.authenticated) {
        alert(
          "The login window closed, but no Synty Store session was found.\n" +
            "Sign in and open My Downloads before closing the browser."
        );
      }
    } catch (err) {
      alert(`Login failed: ${err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Login to Synty Store";
    }
  });

  document.getElementById("refreshCatalogBtn").addEventListener("click", async () => {
    const btn = document.getElementById("refreshCatalogBtn");
    const banner = document.getElementById("crawlBanner");
    btn.disabled = true;
    banner.classList.remove("hidden");
    document.getElementById("crawlMessage").textContent = "Starting Synty Store catalog crawl...";
    try {
      const res = await fetch("/api/catalog/refresh", { method: "POST" });
      if (!res.ok) {
        const err = await res.json();
        alert(`Catalog refresh failed: ${err.detail || res.statusText}`);
        fetchState(); // a 401 marks the session expired: update the badge
      } else {
        const data = await res.json();
        applyServerState(data);
      }
    } finally {
      btn.disabled = false;
      banner.classList.add("hidden");
    }
  });

  document.getElementById("selectAllBtn").addEventListener("click", () => {
    for (const item of getFilteredItems()) {
      if (item.files_to_download && item.files_to_download.length > 0) {
        state.selectedFolders.add(item.folder_name);
      }
    }
    renderGrid();
  });

  document.getElementById("deselectAllBtn").addEventListener("click", () => {
    state.selectedFolders.clear();
    renderGrid();
  });

  document.getElementById("downloadSelectedBtn").addEventListener("click", () => {
    const folders = Array.from(state.selectedFolders);
    startDownloads(folders);
  });

  document.getElementById("cancelDownloadsBtn").addEventListener("click", async () => {
    await fetch("/api/downloads/cancel", { method: "POST" });
  });

  document.getElementById("closeDrawerBtn").addEventListener("click", () => {
    document.getElementById("downloadDrawer").classList.add("hidden");
  });
});

