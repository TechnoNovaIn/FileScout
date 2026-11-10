const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fileScout', {
  // Search
  runSearch:   (folder, keyword, contextChars, mode, language, ocrQuality, searchOnly) =>
    ipcRenderer.send('run-search', { folder, keyword, contextChars, mode, language, ocrQuality, searchOnly }),
  onSearchResults: (cb) => ipcRenderer.on('search-results',  (_e, v) => cb(v)),

  // Indexing
  indexFolder: (folder, ocrQuality) =>
    ipcRenderer.send('index-folder', { folder, ocrQuality }),
  onIndexResults: (cb) => ipcRenderer.on('index-results', (_e, v) => cb(v)),
  onIndexProgress: (cb) => ipcRenderer.on('index-progress', (_e, v) => cb(v)),
  indexStatus: (folder) => ipcRenderer.invoke('index-status', folder),

  // Progress (shared for search)
  onProgress: (cb) => ipcRenderer.on('search-progress', (_e, v) => cb(v)),

  // File & folder operations
  openFile:    (filePath) => ipcRenderer.send('open-file', filePath),
  browseFolder: () => ipcRenderer.invoke('browse-folder'),
  onSetFolder: (cb) => ipcRenderer.on('set-folder', (_e, v) => cb(v)),

  removeAllListeners: (ch) => ipcRenderer.removeAllListeners(ch),
});
