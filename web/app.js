/* Blueprint Studio Backend: local browser workspace, with no AI or network connection. */
(() => {
  "use strict";

  const STORAGE_KEY = "blueprint-folders-v1";
  const CATEGORIES = {
    general: "通用", chat: "對話", web: "網站", finance: "金融",
    document: "文件", calendar: "日程", automation: "自動化", research: "研究",
  };
  const MAX_FILE_BYTES = 2 * 1024 * 1024;
  const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
  const MAX_WORKSPACE_BYTES = 2 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  let folders = [];
  let selectedId = null;
  let selectedFilePath = null;
  let statusTimer;
  let saveWarning = false;
  let unreadableStorage = false;
  let validationMessage = "";
  const now = () => new Date().toISOString();
  const uuid = () => globalThis.crypto?.randomUUID?.() || `folder-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  function notify(message, error = false) {
    clearTimeout(statusTimer);
    $("status").textContent = message;
    $("status").classList.toggle("error", error);
    $("status").hidden = false;
    statusTimer = setTimeout(() => { $("status").hidden = true; }, error ? 8000 : 4200);
  }

  function iconUrl(category) {
    const kind = Object.hasOwn(CATEGORIES, category) ? category : "general";
    const svg = globalThis.BlueprintIcons?.[kind];
    return svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : `../assets/icons/${kind}.svg`;
  }

  function currentFolder() {
    return folders.find((folder) => folder.id === selectedId) || null;
  }

  function safePath(value) {
    if (typeof value !== "string" || !value || value.length > 1000 || /[\\:\x00-\x1f]/.test(value) || value.startsWith("/")) {
      throw new Error("檔案路徑必須是資料夾內的相對路徑。");
    }
    const parts = value.split("/");
    if (parts.some((part) => !part || part === "." || part === "..") || parts[0].toLowerCase() === ".blueprint") {
      throw new Error("路徑不能包含 .. 或保留的 .blueprint 目錄。");
    }
    return value;
  }

  function contentBytes(file) {
    return file.encoding === "base64" ? Math.ceil(file.content.length * 3 / 4) : new TextEncoder().encode(file.content).length;
  }

  function totalBytes(files) {
    return files.reduce((sum, file) => sum + contentBytes(file), 0);
  }

  function validateMetadata(folder) {
    if (!folder.name || folder.name.length > 120 || /[<>:"/\\|?*\x00-\x1f]/.test(folder.name) || folder.name.endsWith(".") || [".", "..", ".blueprint"].includes(folder.name) || /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(folder.name)) throw new Error("名稱最多 120 字，不能包含路徑或系統保留字元。");
    if (!folder.id || folder.id.length > 200) throw new Error("資料夾編號無效。");
    if (folder.description.length > 8000) throw new Error("用途最多 8,000 字。");
    if (folder.tags.length > 32 || folder.tags.some((tag) => tag.length > 100)) throw new Error("最多 32 個標籤，每個標籤最多 100 字。");
    if (!Object.hasOwn(CATEGORIES, folder.category)) throw new Error("未支援這個圖案類型。");
  }

  function normalizeFolder(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("資料包內沒有有效的資料夾資料。");
    if (value.name != null && typeof value.name !== "string") throw new Error("資料夾名稱必須是文字。");
    if (value.description != null && typeof value.description !== "string") throw new Error("用途必須是文字。");
    if (value.tags != null && (!Array.isArray(value.tags) || value.tags.some((tag) => typeof tag !== "string"))) throw new Error("標籤格式不正確。");
    const timestamp = now();
    const files = Array.isArray(value.files) ? value.files.map((file) => {
      if (!file || typeof file !== "object" || typeof file.content !== "string") throw new Error("檔案資料格式不正確。");
      if (file.encoding && !["utf-8", "base64"].includes(file.encoding)) throw new Error("未支援的檔案編碼。");
      const normalized = { ...file, path: safePath(file.path), encoding: file.encoding || "utf-8" };
      if (normalized.encoding === "base64" && !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(normalized.content)) throw new Error("二進位檔案的編碼無效。");
      if (normalized.encoding === "utf-8" && contentBytes(normalized) > MAX_FILE_BYTES) throw new Error("每個文字檔案最多可加入 2 MB。");
      return normalized;
    }) : [];
    if (new Set(files.map((file) => file.path)).size !== files.length) throw new Error("資料包包含重複的檔案路徑。");
    if (totalBytes(files) > MAX_TOTAL_BYTES) throw new Error("每個資料夾最多可保存 20 MB 內容。");
    const normalized = {
      ...value,
      id: typeof value.id === "string" && value.id ? value.id : uuid(),
      name: typeof value.name === "string" && value.name.trim() ? value.name : "未命名資料夾",
      description: typeof value.description === "string" ? value.description : "",
      tags: Array.isArray(value.tags) ? value.tags.filter((tag) => typeof tag === "string") : [],
      category: typeof value.category === "string" ? value.category : "general",
      created_at: typeof value.created_at === "string" ? value.created_at : timestamp,
      updated_at: typeof value.updated_at === "string" ? value.updated_at : timestamp,
      files,
    };
    validateMetadata(normalized);
    return normalized;
  }

  function updateSaveState() {
    $("save-state").textContent = validationMessage || (saveWarning ? "尚未保存成功，請下載資料包備份。" : "已儲存至此瀏覽器");
    $("save-state").classList.toggle("warning", saveWarning || Boolean(validationMessage));
  }

  function storageText(values) {
    const serialized = JSON.stringify({ version: 1, folders: values });
    // localStorage commonly counts UTF-16 storage; keep the entire workspace small.
    if (serialized.length * 2 > MAX_WORKSPACE_BYTES) {
      throw new Error("瀏覽器工作區最多約 2 MB。請先下載資料包備份，再移除暫時不用的資料夾。");
    }
    return serialized;
  }

  function persist() {
    try {
      if (unreadableStorage) throw new Error("原有保存資料未能讀取，暫時保留原始資料。");
      localStorage.setItem(STORAGE_KEY, storageText(folders));
      saveWarning = false;
    } catch (_error) {
      if (!saveWarning) notify("瀏覽器未能保存內容，可能空間不足或處於私密模式。請立即下載資料包備份。", true);
      saveWarning = true;
    }
    updateSaveState();
    return !saveWarning;
  }

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      const values = Array.isArray(data) ? data : data.folders;
      if (!Array.isArray(values)) throw new Error("資料格式不正確");
      folders = values.map(normalizeFolder);
      const ids = new Set();
      folders.forEach((folder) => { if (ids.has(folder.id)) folder.id = uuid(); ids.add(folder.id); });
    } catch (_error) {
      saveWarning = true;
      unreadableStorage = true;
      notify("未能讀取瀏覽器保存的資料。原有資料未被覆寫；可先匯入你下載的備份。", true);
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderCards() {
    const query = $("search").value.trim().toLocaleLowerCase();
    const visible = folders.filter((folder) => `${folder.name}\n${folder.description}\n${folder.tags.join(" ")}`.toLocaleLowerCase().includes(query));
    $("folder-count").textContent = String(folders.length);
    $("folder-grid").replaceChildren();
    visible.forEach((folder) => {
      const card = element("button", "folder-card");
      card.type = "button";
      card.classList.toggle("selected", folder.id === selectedId);
      card.setAttribute("aria-pressed", String(folder.id === selectedId));
      const image = element("img", "folder-icon");
      image.src = iconUrl(folder.category);
      image.alt = "";
      card.append(image, element("span", "folder-title", folder.name));
      const blank = !folder.description && !folder.tags.length && !folder.files.length;
      card.append(element("span", "folder-description", folder.description || "用途留待補充"));
      const meta = element("span", "folder-meta");
      if (blank) meta.append(element("span", "empty-badge", "空白"));
      else meta.append(element("span", "file-badge", `${folder.files.length} 個檔案`));
      folder.tags.slice(0, 2).forEach((tag) => meta.append(element("span", "tag", tag)));
      card.append(meta);
      card.addEventListener("click", () => selectFolder(folder.id));
      $("folder-grid").append(card);
    });
    $("library-empty").hidden = folders.length !== 0;
    $("search-empty").hidden = folders.length === 0 || visible.length !== 0;
  }

  function codexPrompt(folder) {
    return `請讀取我提供並已解壓的「${folder.name}」資料夾，先查看 AGENTS.md 及 .blueprint/folder.json，再按我接下來提供的要求開發。保留已有檔案，隨工作進展更新用途、標籤及相關標記；未確定的內容保持空白，不要自行編造。這個資料包的編號是 ${folder.id}。`;
  }

  function updateHeading(folder) {
    $("selected-title").textContent = folder.name;
    $("selected-icon").src = iconUrl(folder.category);
    $("codex-prompt").value = codexPrompt(folder);
    document.querySelectorAll(".icon-option").forEach((button) => {
      const selected = button.dataset.category === folder.category;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  }

  function renderEditor() {
    validationMessage = "";
    const folder = currentFolder();
    $("editor-empty").hidden = Boolean(folder);
    $("editor-content").hidden = !folder;
    if (!folder) return false;
    $("folder-name").value = folder.name;
    $("folder-description").value = folder.description;
    $("folder-tags").value = folder.tags.join(", ");
    $("file-preview").hidden = true;
    selectedFilePath = null;
    updateHeading(folder);
    updateSaveState();
    renderFiles();
  }

  function selectFolder(id, focusName = false) {
    selectedId = id;
    renderCards();
    renderEditor();
    if (focusName) {
      $("folder-name").focus();
      $("folder-name").select();
    }
  }

  function createBlank() {
    const timestamp = now();
    const folder = { id: uuid(), name: "未命名資料夾", description: "", tags: [], category: "general", created_at: timestamp, updated_at: timestamp, files: [] };
    try { storageText([folder, ...folders]); }
    catch (error) { notify(error.message, true); return; }
    folders.unshift(folder);
    $("search").value = "";
    persist();
    selectFolder(folder.id, true);
    if (window.matchMedia("(max-width: 800px)").matches) $("editor-content").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function editFolder() {
    const folder = currentFolder();
    if (!folder) return;
    const updated = {
      ...folder,
      name: $("folder-name").value.trim() || "未命名資料夾",
      description: $("folder-description").value,
      tags: [...new Set($("folder-tags").value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))],
      updated_at: now(),
    };
    try {
      validateMetadata(updated);
      storageText(folders.map((entry) => entry.id === folder.id ? updated : entry));
    } catch (error) {
      validationMessage = error.message;
      updateSaveState();
      return false;
    }
    validationMessage = "";
    Object.assign(folder, updated);
    persist();
    renderCards();
    updateHeading(folder);
    return true;
  }

  function formatBytes(bytes) {
    return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function renderFiles() {
    const folder = currentFolder();
    if (!folder) return;
    $("file-count").textContent = String(folder.files.length);
    $("file-list").replaceChildren();
    if (!folder.files.length) $("file-list").append(element("p", "footer-note", "暫時未加入檔案。"));
    folder.files.forEach((file) => {
      const row = element("div", "file-row");
      const open = element("button", "file-info");
      open.type = "button";
      open.append(element("span", "file-path", file.path), element("span", "file-size", `${formatBytes(contentBytes(file))}${file.encoding === "base64" ? " · 原檔保存" : " · 文字"}`));
      open.addEventListener("click", () => previewFile(file.path));
      const remove = element("button", "file-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `移除 ${file.path}`);
      remove.addEventListener("click", () => {
        if (!confirm(`確定從資料夾移除「${file.path}」？`)) return;
        folder.files = folder.files.filter((entry) => entry.path !== file.path);
        folder.updated_at = now();
        persist();
        renderFiles();
        renderCards();
        if (selectedFilePath === file.path) $("file-preview").hidden = true;
      });
      row.append(open, remove);
      $("file-list").append(row);
    });
  }

  function previewFile(path) {
    const file = currentFolder()?.files.find((entry) => entry.path === path);
    if (!file) return;
    if (file.encoding === "base64") {
      notify("這個檔案會原樣加入 ZIP 資料包，暫不提供內容預覽。");
      return;
    }
    selectedFilePath = path;
    $("preview-title").textContent = path;
    $("preview-content").value = file.content;
    $("file-preview").hidden = false;
  }

  function addFiles(incoming) {
    const folder = currentFolder();
    if (!folder) return;
    const combined = [...folder.files];
    incoming.forEach((file) => {
      safePath(file.path);
      const existing = combined.findIndex((entry) => entry.path === file.path);
      if (existing >= 0) throw new Error(`「${file.path}」已存在。請先移除舊檔案或使用另一個路徑。`);
      if (file.encoding !== "base64" && contentBytes(file) > MAX_FILE_BYTES) throw new Error("每個文字檔案最多可加入 2 MB。");
      combined.push(file);
    });
    if (totalBytes(combined) > MAX_TOTAL_BYTES) throw new Error("每個資料夾最多可保存 20 MB 內容。");
    storageText(folders.map((entry) => entry.id === folder.id ? { ...entry, files: combined } : entry));
    folder.files = combined;
    folder.updated_at = now();
    const stored = persist();
    renderFiles();
    renderCards();
    if (stored) notify(`已加入 ${incoming.length} 個檔案。`);
  }

  function downloadJson(value, name) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = `${name.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_") || "blueprint"}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function importData(file) {
    if (file.size > 40 * 1024 * 1024) throw new Error("資料包太大；請使用 40 MB 以下的 JSON 或 ZIP。");
    let incoming;
    if (/\.zip$/i.test(file.name)) {
      if (!globalThis.BlueprintPackage?.importZip) throw new Error("資料包元件未載入，請使用完整版本。");
      incoming = await globalThis.BlueprintPackage.importZip(file);
    } else {
      const data = JSON.parse(await file.text());
      incoming = Array.isArray(data) ? data : Array.isArray(data.folders) ? data.folders : [data];
    }
    if (!Array.isArray(incoming) || !incoming.length) throw new Error("資料包內沒有資料夾。");
    const normalized = incoming.map(normalizeFolder);
    const occupied = new Set(folders.map((folder) => folder.id));
    normalized.forEach((folder) => {
      if (occupied.has(folder.id)) {
        folder.id = uuid();
        const suffix = "（匯入副本）";
        folder.name = `${folder.name.slice(0, 120 - suffix.length)}${suffix}`;
      }
      occupied.add(folder.id);
    });
    storageText([...normalized, ...folders]);
    folders.unshift(...normalized);
    $("search").value = "";
    const stored = persist();
    selectFolder(normalized[0].id);
    if (stored) notify(`已匯入 ${normalized.length} 個資料夾。相同編號會另存副本。`);
  }

  async function fileToEntry(file) {
    const path = safePath(file.webkitRelativePath || file.name);
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    try {
      const content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      if (!content.includes("\u0000")) {
        if (bytes.length > MAX_FILE_BYTES) throw new Error("每個文字檔案最多可加入 2 MB。");
        return { path, content, encoding: "utf-8" };
      }
    } catch (error) {
      if (error.message === "每個文字檔案最多可加入 2 MB。") throw error;
    }
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    return { path, content: btoa(binary), encoding: "base64" };
  }

  Object.entries(CATEGORIES).forEach(([kind, label]) => {
    const button = element("button", "icon-option");
    button.type = "button";
    button.dataset.category = kind;
    const image = element("img");
    image.src = iconUrl(kind);
    image.alt = "";
    button.append(image, element("span", "", label));
    button.setAttribute("aria-label", `選擇${label}圖案`);
    button.addEventListener("click", () => {
      const folder = currentFolder();
      if (!folder) return;
      folder.category = kind;
      folder.updated_at = now();
      persist();
      updateHeading(folder);
      renderCards();
    });
    $("icon-picker").append(button);
  });

  $("new-button").addEventListener("click", createBlank);
  $("empty-new-button").addEventListener("click", createBlank);
  $("search").addEventListener("input", renderCards);
  $("folder-form").addEventListener("submit", (event) => event.preventDefault());
  ["folder-name", "folder-description", "folder-tags"].forEach((id) => $(id).addEventListener("input", editFolder));
  $("import-button").addEventListener("click", () => $("import-input").click());
  $("import-input").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try { await importData(file); } catch (error) { notify(`匯入失敗：${error.message}`, true); }
    event.target.value = "";
  });
  $("export-all-button").addEventListener("click", () => {
    if (!folders.length) return notify("先建立一個資料夾，再下載備份。");
    downloadJson({ version: 1, exported_at: now(), folders }, "藍圖工作室備份");
  });
  $("export-json-button").addEventListener("click", () => { const folder = currentFolder(); if (folder) downloadJson(folder, folder.name); });
  $("download-zip-button").addEventListener("click", async () => {
    const folder = currentFolder();
    if (!folder) return;
    if (!editFolder()) return notify(validationMessage || "請先修正資料夾資料。", true);
    const button = $("download-zip-button");
    button.disabled = true;
    try {
      if (!globalThis.BlueprintPackage?.exportZip) throw new Error("資料包元件未載入，請使用完整版本。");
      await globalThis.BlueprintPackage.exportZip(folder);
      notify("已準備 ZIP 資料包。解壓後可交畀 Codex 繼續開發。");
    } catch (error) { notify(`下載失敗：${error.message}`, true); }
    finally { button.disabled = false; }
  });
  $("copy-codex-button").addEventListener("click", async () => {
    const folder = currentFolder();
    if (!folder) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(codexPrompt(folder));
      notify("已複製指令。請連同已下載並解壓的資料夾交畀 Codex。");
    } catch (_error) {
      $("codex-details").open = true;
      $("codex-prompt").focus();
      $("codex-prompt").select();
      notify("請長按或按 Ctrl/Cmd+C，複製已選取的指令。");
    }
  });
  $("delete-button").addEventListener("click", () => {
    const folder = currentFolder();
    if (!folder || !confirm(`確定刪除「${folder.name}」及其瀏覽器內的內容？建議先下載資料包。`)) return;
    folders = folders.filter((entry) => entry.id !== folder.id);
    selectedId = null;
    const stored = persist();
    renderCards();
    renderEditor();
    if (stored) notify("已刪除資料夾。");
  });
  $("upload-files-button").addEventListener("click", () => $("files-input").click());
  $("files-input").addEventListener("change", async (event) => {
    const selectedFiles = [...event.target.files];
    if (!selectedFiles.length) return;
    const folderAtStart = selectedId;
    try {
      if (selectedFiles.reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL_BYTES) throw new Error("每個資料夾最多可保存 20 MB 內容。");
      const entries = await Promise.all(selectedFiles.map(fileToEntry));
      if (selectedId !== folderAtStart) throw new Error("資料夾已切換，請重新加入檔案。");
      addFiles(entries);
    } catch (error) { notify(error.message, true); }
    event.target.value = "";
  });
  $("add-text-file-button").addEventListener("click", () => {
    try {
      addFiles([{ path: safePath($("new-file-path").value.trim()), content: $("new-file-content").value, encoding: "utf-8" }]);
      $("new-file-path").value = "";
      $("new-file-content").value = "";
      $("new-file-details").open = false;
    } catch (error) { notify(error.message, true); }
  });
  $("preview-content").addEventListener("input", () => {
    const folder = currentFolder();
    const file = folder?.files.find((entry) => entry.path === selectedFilePath);
    if (!file || file.encoding === "base64") return;
    const content = $("preview-content").value;
    const bytes = new TextEncoder().encode(content).length;
    if (bytes > MAX_FILE_BYTES || totalBytes(folder.files) - contentBytes(file) + bytes > MAX_TOTAL_BYTES) {
      $("preview-content").value = file.content;
      return notify("內容超過限制：每個文字檔 2 MB，每個資料夾 20 MB。", true);
    }
    try {
      const files = folder.files.map((entry) => entry.path === file.path ? { ...entry, content } : entry);
      storageText(folders.map((entry) => entry.id === folder.id ? { ...entry, files } : entry));
    } catch (error) {
      $("preview-content").value = file.content;
      return notify(error.message, true);
    }
    file.content = content;
    folder.updated_at = now();
    persist();
    renderFiles();
  });
  $("close-preview-button").addEventListener("click", () => { $("file-preview").hidden = true; selectedFilePath = null; });

  load();
  renderCards();
  renderEditor();
})();
