/**
 * FileScout Web API Adapter
 * 
 * Replaces the Electron IPC bridge (window.fileScout) with HTTP/SSE calls
 * to the FastAPI backend running on the same origin.
 * 
 * Drop-in replacement: the React app calls the same methods,
 * but they now hit REST endpoints instead of Electron IPC.
 * Supports Client-Side Local Folder Access (File System Access API)
 * and Network Shared Folders (UNC/SMB).
 */

const API_BASE = '';  // Same origin

// ── Persistent Client Identity ──────────────────────────────────────────────
export function getClientId() {
  let cid = localStorage.getItem('filescout_client_id');
  if (!cid) {
    cid = 'client_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now();
    localStorage.setItem('filescout_client_id', cid);
  }
  return cid;
}

// ── In-Memory Client Directory Registry ─────────────────────────────────────
let _activeLocalFolder = null; // { name, virtualPath, displayPath, files: [] }
const _localFilesRegistry = new Map(); // relativePath -> File object

export function getActiveLocalFolder() {
  return _activeLocalFolder;
}

export function setActiveLocalFolder(folderData) {
  _activeLocalFolder = folderData;
  if (folderData?.files) {
    _localFilesRegistry.clear();
    for (const f of folderData.files) {
      if (f.relativePath) _localFilesRegistry.set(f.relativePath, f.file);
      if (f.name) _localFilesRegistry.set(f.name, f.file);
      const base = (f.name || f.relativePath || '').split('/').pop();
      if (base) _localFilesRegistry.set(base, f.file);
    }
  }
}

export function getLocalFileBlob(relativePath) {
  if (!relativePath) return null;
  const clean = relativePath.replace(/\\/g, '/').replace(/^client:\/\/[^\/]+\/[^\/]+\//, '');
  const basename = clean.split('/').pop();
  return _localFilesRegistry.get(clean) || _localFilesRegistry.get(basename) || _localFilesRegistry.get(relativePath);
}

// ── Event listener registry (mimics ipcRenderer.on / removeAllListeners) ────

const _listeners = {};

function _addListener(channel, cb) {
  if (!_listeners[channel]) _listeners[channel] = [];
  _listeners[channel].push(cb);
}

function _emit(channel, data) {
  (_listeners[channel] || []).forEach((cb) => cb(data));
}

function _removeAllListeners(channel) {
  delete _listeners[channel];
}


// ── SSE helper ──────────────────────────────────────────────────────────────

function _streamSSE(url, body, progressChannel, resultChannel) {
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then((res) => {
      if (!res.ok) {
        return res.json().then((err) => {
          _emit(resultChannel, { ok: false, error: err.detail || 'Request failed' });
        });
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      function read() {
        reader.read().then(({ done, value }) => {
          if (done) return;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try {
              const parsed = JSON.parse(line.slice(6));

              if (parsed.type === 'progress') {
                _emit(progressChannel, parsed.line);
              } else if (parsed.type === 'result') {
                const data = parsed.data;
                _emit(resultChannel, {
                  ok: true,
                  results: data.results || [],
                  searchTime: data.search_time_seconds,
                  language: data.language,
                  totalMatched: data.total_files_matched,
                  // index-specific fields
                  totalFiles: data.total_files,
                  alreadyIndexed: data.already_indexed,
                  indexed: data.indexed,
                  skipped: data.skipped,
                  errors: data.errors,
                });
              } else if (parsed.type === 'error') {
                _emit(resultChannel, { ok: false, error: parsed.error });
              }
            } catch {
              // skip malformed SSE lines
            }
          }

          read();
        });
      }

      read();
    })
    .catch((err) => {
      _emit(resultChannel, { ok: false, error: err.message });
    });
}


// ── Public API (compatible with window.fileScout) ───────────────────────────

const webApi = {
  getClientId,
  getActiveLocalFolder,
  setActiveLocalFolder,
  getLocalFileBlob,

  // ── Search ──
  runSearch(folder, keyword, contextChars, _mode, language, ocrQuality, searchOnly) {
    const cid = getClientId();
    const targetFolder = (folder && folder !== 'client://local') ? folder : `client://${cid}`;
    _streamSSE(
      `${API_BASE}/api/search/stream`,
      {
        folder: targetFolder,
        keyword,
        context: contextChars || 80,
        language,
        ocrQuality,
        searchOnly: !!searchOnly,
        clientId: cid,
      },
      'search-progress',
      'search-results'
    );
  },

  onSearchResults(cb) {
    _addListener('search-results', cb);
  },

  // ── Indexing ──
  async indexFolder(folder, ocrQuality, force, language) {
    // Check if this is a client local folder
    if (folder.startsWith('client://')) {
      if (_activeLocalFolder?.files?.length > 0) {
        await this.indexClientFiles(_activeLocalFolder.name, _activeLocalFolder.files, ocrQuality, language);
        return;
      } else {
        _emit('index-progress', 'No local files loaded in memory to upload.');
        _emit('index-progress', 'Please click "Browse", choose your folder or files from your computer, then click Index.');
        _emit('index-results', {
          ok: false,
          error: 'Please click "Browse" and re-select your folder or files to index.',
        });
        return;
      }
    }

    const cid = getClientId();
    _streamSSE(
      `${API_BASE}/api/index/stream`,
      { folder, ocrQuality: ocrQuality || 'balanced', force: !!force, clientId: cid },
      'index-progress',
      'index-results'
    );
  },

  async indexClientFiles(folderName, files, ocrQuality, language) {
    const cid = getClientId();
    const supportedExts = new Set([
      'pdf', 'jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp', 'webp', 'svg',
      'docx', 'xlsx', 'txt', 'csv', 'tsv', 'log', 'md', 'json', 'py', 'js',
      'html', 'css', 'xml', 'sql', 'yaml', 'yml', 'ini', 'conf', 'sh', 'ps1', 'bat', 'cmd', 'rtf'
    ]);

    const targetFiles = files.filter((f) => {
      const ext = f.name.split('.').pop().toLowerCase();
      return supportedExts.has(ext);
    });

    _emit('index-progress', `Found ${targetFiles.length} supported files in ${folderName}`);

    let indexedCount = 0;
    let skippedCount = 0;
    let errorCount = 0;
    const start = Date.now();

    for (let i = 0; i < targetFiles.length; i++) {
      const f = targetFiles[i];
      _emit('index-progress', `[${i + 1}/${targetFiles.length}] Scanning: ${f.relativePath}`);

      try {
        const formData = new FormData();
        formData.append('file', f.file, f.name);
        formData.append('client_id', cid);
        formData.append('folder_name', folderName);
        formData.append('relative_path', f.relativePath);
        formData.append('ocr_quality', ocrQuality || 'balanced');
        if (language && language !== 'en') {
          formData.append('language', language);
        }

        const res = await fetch(`${API_BASE}/api/client/process-file`, {
          method: 'POST',
          body: formData,
        });

        if (!res.ok) {
          errorCount++;
          _emit('index-progress', `ERROR: ${f.relativePath} (HTTP ${res.status})`);
          continue;
        }

        const data = await res.json();
        if (data.ok && data.indexed) {
          indexedCount++;
          _emit('index-progress', `FOUND: ${f.relativePath} (${data.ocr_method}, ${Math.round((data.confidence || 1) * 100)}%)`);
        } else {
          skippedCount++;
          _emit('index-progress', `SKIP: ${f.relativePath} (${data.reason || 'no text'})`);
        }
      } catch (e) {
        errorCount++;
        _emit('index-progress', `ERROR: ${f.relativePath} - ${e.message}`);
      }
    }

    const elapsed = Math.round((Date.now() - start) / 1000);
    _emit('index-results', {
      ok: true,
      totalFiles: targetFiles.length,
      indexed: indexedCount,
      skipped: skippedCount,
      errors: errorCount,
      alreadyIndexed: 0,
      searchTime: elapsed,
    });
  },

  onIndexResults(cb) {
    _addListener('index-results', cb);
  },

  onIndexProgress(cb) {
    _addListener('index-progress', cb);
  },

  // ── Index status ──
  async indexStatus(folder) {
    try {
      const cid = getClientId();
      const folderParam = folder ? encodeURIComponent(folder) : '';
      const res = await fetch(
        `${API_BASE}/api/index-status?folder=${folderParam}&clientId=${encodeURIComponent(cid)}`
      );
      if (!res.ok) {
        const err = await res.json();
        return { ok: false, error: err.detail };
      }
      const data = await res.json();
      if (folder && folder.startsWith('client://') && _activeLocalFolder?.files?.length > 0) {
        const supportedCount = _activeLocalFolder.files.filter(f => f.is_supported).length;
        data.total_files = supportedCount || _activeLocalFolder.files.length;
        data.is_fully_indexed = data.indexed_files > 0 && data.indexed_files >= data.total_files;
      }
      return data;
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  // ── Progress (shared for search) ──
  onProgress(cb) {
    _addListener('search-progress', cb);
  },

  // ── File operations ──
  openFile(filePath) {
    if (!filePath) return;
    // If client local file
    if (filePath.startsWith('client://')) {
      const parts = filePath.replace('client://', '').split('/');
      // parts: [client_id, folder_name, ...subpaths]
      const relPath = parts.slice(2).join('/');
      const blob = getLocalFileBlob(relPath);
      if (blob) {
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        return;
      }
      // If blob not in browser memory, load from server client storage
      const cid = getClientId();
      const url = `${API_BASE}/api/client/file?path=${encodeURIComponent(filePath)}&clientId=${encodeURIComponent(cid)}`;
      window.open(url, '_blank');
      return;
    }

    // Remote or server file
    const url = `${API_BASE}/api/file?path=${encodeURIComponent(filePath)}`;
    window.open(url, '_blank');
  },

  async getClientStatus() {
    try {
      const cid = getClientId();
      const res = await fetch(`${API_BASE}/api/client/status?clientId=${encodeURIComponent(cid)}`);
      if (!res.ok) return { ok: false, total_indexed_files: 0, folders: [] };
      return await res.json();
    } catch (e) {
      return { ok: false, total_indexed_files: 0, folders: [] };
    }
  },

  // ── Browse for folder ──
  async browseFolder() {
    return null;
  },

  onSetFolder(cb) {
    _addListener('set-folder', cb);
  },

  // ── Browse API for folder navigation ──
  async browsePath(path) {
    try {
      const res = await fetch(
        `${API_BASE}/api/browse?path=${encodeURIComponent(path || '/')}`
      );
      if (!res.ok) {
        const err = await res.json();
        return { ok: false, error: err.detail };
      }
      const data = await res.json();
      return { ok: true, ...data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  // ── Network Shares API ──
  async getNetworkShares() {
    try {
      const res = await fetch(`${API_BASE}/api/network-shares`);
      return await res.json();
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  async addNetworkShare(name, unc_path, description) {
    try {
      const res = await fetch(`${API_BASE}/api/network-shares`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, unc_path, description }),
      });
      return await res.json();
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  // ── Client Indexed Folders API ──
  async getClientFolders() {
    try {
      const cid = getClientId();
      const res = await fetch(`${API_BASE}/api/client/folders?clientId=${encodeURIComponent(cid)}`);
      if (!res.ok) return { ok: false, folders: [] };
      return await res.json();
    } catch (e) {
      return { ok: false, folders: [] };
    }
  },

  async getClientFolderFiles(folderName) {
    try {
      const cid = getClientId();
      const res = await fetch(
        `${API_BASE}/api/client/folder-files?clientId=${encodeURIComponent(cid)}&folder_name=${encodeURIComponent(folderName)}`
      );
      if (!res.ok) return { ok: false, files: [] };
      return await res.json();
    } catch (e) {
      return { ok: false, files: [] };
    }
  },

  async deleteClientFolder(folderName) {
    try {
      const cid = getClientId();
      const res = await fetch(
        `${API_BASE}/api/client/folder?clientId=${encodeURIComponent(cid)}&folder_name=${encodeURIComponent(folderName)}`,
        { method: 'DELETE' }
      );
      return await res.json();
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  async clearClientIndex() {
    try {
      const cid = getClientId();
      const res = await fetch(`${API_BASE}/api/client/clear?client_id=${encodeURIComponent(cid)}`, {
        method: 'POST',
      });
      return await res.json();
    } catch (e) {
      return { ok: false, error: e.message };
    }
  },

  removeAllListeners(ch) {
    _removeAllListeners(ch);
  },
};

export default webApi;
