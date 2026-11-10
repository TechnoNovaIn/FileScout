import React, { useState, useEffect, useRef, useMemo } from 'react';
import styles from './FolderBrowser.module.css';

export default function FolderBrowser({ isOpen, onSelect, onClose, api }) {
  const [browserMode, setBrowserMode]     = useState('client'); // 'client' | 'network' | 'server'
  const [currentPath, setCurrentPath]     = useState('/mnt/unc/192.168.0.116/Users');
  const [displayPath, setDisplayPath]     = useState('\\\\192.168.0.116\\Users');
  const [entries, setEntries]             = useState([]);
  const [parentPath, setParentPath]       = useState(null);
  const [quickAccess, setQuickAccess]     = useState([]);
  const [loading, setLoading]             = useState(false);
  const [error, setError]                 = useState('');
  const [manualPath, setManualPath]       = useState('');
  const [filterQuery, setFilterQuery]     = useState('');
  const [activeTab, setActiveTab]         = useState('all'); // 'all' | 'folders' | 'files' | 'supported'
  const [sortBy, setSortBy]               = useState('name_asc');
  const [stats, setStats]                 = useState(null);
  const [selectedItem, setSelectedItem]   = useState(null);
  const [isRemote, setIsRemote]           = useState(true);

  // Client local folder state
  const [clientFolder, setClientFolder]   = useState(null); // { name, virtualPath, displayPath, files: [] }
  const [savedClientFolders, setSavedClientFolders] = useState([]);
  const fileInputRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      // Check existing active local folder in webApi
      if (api.getActiveLocalFolder) {
        const existing = api.getActiveLocalFolder();
        if (existing) {
          setClientFolder(existing);
        }
      }
      loadRootsAndInitialPath();
    }
  }, [isOpen]);

  const loadRootsAndInitialPath = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.browsePath('/');
      if (res.ok) {
        setIsRemote(!!res.is_remote);
        if (Array.isArray(res.quick_access)) {
          setQuickAccess(res.quick_access);
          const firstShare = res.quick_access.find((q) => q.type === 'network');
          if (firstShare) {
            setCurrentPath(firstShare.path);
            setDisplayPath(firstShare.display || firstShare.path);
          }
        }
        // If remote, default to client mode
        if (res.is_remote) {
          setBrowserMode('client');
        } else {
          setBrowserMode('server');
          setCurrentPath('/mnt/c');
          setDisplayPath('C:\\');
          loadPath('/mnt/c');
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }

    // Load any previously indexed folders for this client
    refreshSavedClientFolders();
  };

  const refreshSavedClientFolders = async () => {
    if (api.getClientFolders) {
      try {
        const cfRes = await api.getClientFolders();
        if (cfRes?.ok && Array.isArray(cfRes.folders)) {
          setSavedClientFolders(cfRes.folders);
        } else {
          setSavedClientFolders([]);
        }
      } catch {}
    }
  };

  const handleDeleteSavedFolder = async (e, folderName) => {
    e.stopPropagation();
    if (!window.confirm(`Delete indexed folder "${folderName}" from database?`)) return;
    if (api.deleteClientFolder) {
      await api.deleteClientFolder(folderName);
      if (clientFolder?.name === folderName) {
        setClientFolder(null);
        if (api.setActiveLocalFolder) api.setActiveLocalFolder(null);
      }
      refreshSavedClientFolders();
    }
  };

  const handleClearAllIndex = async () => {
    if (!window.confirm('Are you sure you want to delete ALL indexed files for this machine? This action cannot be undone.')) return;
    if (api.clearClientIndex) {
      await api.clearClientIndex();
      setClientFolder(null);
      if (api.setActiveLocalFolder) api.setActiveLocalFolder(null);
      setSavedClientFolders([]);
      if (onSelect) onSelect('', '');
    }
  };

  const loadPath = async (path) => {
    setLoading(true);
    setError('');
    setFilterQuery('');
    setSelectedItem(null);
    const result = await api.browsePath(path);
    setLoading(false);

    if (result.ok) {
      setCurrentPath(result.current);
      setDisplayPath(result.display_path || result.current);
      setParentPath(result.parent);
      setManualPath(result.display_path || result.current);
      setStats(result.stats || null);

      if (Array.isArray(result.quick_access) && result.quick_access.length > 0) {
        setQuickAccess(result.quick_access);
      }
      setEntries(result.entries || []);
    } else {
      setError(result.error || 'Failed to browse directory');
    }
  };

  const handleNavigate = (path) => {
    if (path === 'client://local') {
      setBrowserMode('client');
      return;
    }
    setBrowserMode(path.startsWith('/mnt/unc') || path.startsWith('\\\\') || path.startsWith('//') ? 'network' : 'server');
    loadPath(path);
  };

  const handleGoUp = () => {
    if (parentPath) loadPath(parentPath);
  };

  const handleSelectFolder = () => {
    if (browserMode === 'client' && clientFolder) {
      onSelect(clientFolder.virtualPath, clientFolder.displayPath);
    } else {
      onSelect(currentPath, displayPath);
    }
    onClose();
  };

  const handleManualGo = () => {
    if (manualPath.trim()) {
      handleNavigate(manualPath.trim());
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleManualGo();
    if (e.key === 'Escape') onClose();
  };

  const handleEntryClick = (entry) => {
    setSelectedItem(entry);
    if (entry.type === 'directory') {
      handleNavigate(entry.path);
    }
  };

  const handleOpenFile = (e, filePath) => {
    e.stopPropagation();
    api.openFile(filePath);
  };

  // ── Native Browser Folder Selection for Client Machine ───────────────────
  const getFileCategory = (filename) => {
    const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase();
    if (ext === '.pdf') return 'pdf';
    if (['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.svg'].includes(ext)) return 'image';
    if (['.docx', '.xlsx'].includes(ext)) return 'document';
    if (['.txt', '.csv', '.tsv', '.log', '.md', '.json', '.py', '.js', '.html', '.css', '.xml', '.sql', '.yaml', '.yml', '.ini', '.conf', '.sh', '.ps1', '.bat'].includes(ext)) return 'text';
    return 'other';
  };

  const handlePickLocalFolder = async () => {
    setError('');
    if ('showDirectoryPicker' in window) {
      try {
        const dirHandle = await window.showDirectoryPicker({ mode: 'read' });
        setLoading(true);

        const scannedFiles = [];
        async function scanDir(handle, prefix = '') {
          for await (const entry of handle.values()) {
            const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
            if (entry.kind === 'file') {
              try {
                const f = await entry.getFile();
                const cat = getFileCategory(entry.name);
                scannedFiles.push({
                  name: entry.name,
                  relativePath: rel,
                  size: f.size,
                  size_formatted: (f.size / 1024).toFixed(1) + ' KB',
                  file: f,
                  type: 'file',
                  category: cat,
                  is_supported: cat !== 'other',
                });
              } catch {}
            } else if (entry.kind === 'directory') {
              if (!entry.name.startsWith('.') && entry.name !== 'node_modules' && entry.name !== '__pycache__') {
                await scanDir(entry, rel);
              }
            }
          }
        }

        await scanDir(dirHandle);
        setLoading(false);

        const cid = api.getClientId ? api.getClientId() : 'client';
        const folderData = {
          name: dirHandle.name,
          virtualPath: `client://${cid}/${dirHandle.name}`,
          displayPath: `[My Computer] ${dirHandle.name}`,
          files: scannedFiles,
        };

        if (api.setActiveLocalFolder) {
          api.setActiveLocalFolder(folderData);
        }
        setClientFolder(folderData);
        setBrowserMode('client');
        setSelectedItem(null);
        if (onSelect) {
          onSelect(folderData.virtualPath, folderData.displayPath);
        }
      } catch (err) {
        setLoading(false);
        if (err.name !== 'AbortError') {
          setError('Could not open folder picker: ' + err.message);
        }
      }
    } else {
      // Fallback: trigger input file picker
      fileInputRef.current?.click();
    }
  };

  const handleFilesSelected = (fileList, isFolder = false) => {
    if (!fileList || fileList.length === 0) return;

    let folderName = 'Selected Files';
    if (isFolder) {
      const firstRel = fileList[0].webkitRelativePath || fileList[0].name;
      folderName = firstRel.split('/')[0] || 'My Folder';
    } else {
      folderName = fileList.length === 1 ? fileList[0].name : `${fileList.length} Selected Files`;
    }

    const scannedFiles = fileList.map((f) => {
      const rel = f.webkitRelativePath || f.name;
      const cat = getFileCategory(f.name);
      return {
        name: f.name,
        relativePath: rel,
        size: f.size,
        size_formatted: (f.size / 1024).toFixed(1) + ' KB',
        file: f,
        type: 'file',
        category: cat,
        is_supported: cat !== 'other',
      };
    });

    const cid = api.getClientId ? api.getClientId() : 'client';
    const folderData = {
      name: folderName,
      virtualPath: `client://${cid}/${folderName}`,
      displayPath: `[My Computer] ${folderName}`,
      files: scannedFiles,
    };

    if (api.setActiveLocalFolder) {
      api.setActiveLocalFolder(folderData);
    }
    setClientFolder(folderData);
    setBrowserMode('client');
    setSelectedItem(null);

    // Keep parent in sync immediately
    if (onSelect) {
      onSelect(folderData.virtualPath, folderData.displayPath);
    }
  };

  const handleFileInputChange = (e) => {
    handleFilesSelected(Array.from(e.target.files || []), true);
  };

  const handleSelectSavedFolder = async (sf) => {
    setLoading(true);
    let files = [];
    if (api.getClientFolderFiles) {
      try {
        const res = await api.getClientFolderFiles(sf.folder_name);
        if (res?.ok && Array.isArray(res.files)) {
          files = res.files;
        }
      } catch {}
    }
    setLoading(false);
    const folderData = {
      name: sf.folder_name,
      virtualPath: sf.virtual_path,
      displayPath: sf.display_path,
      files: files,
    };
    if (api.setActiveLocalFolder) {
      api.setActiveLocalFolder(folderData);
    }
    setClientFolder(folderData);
    setBrowserMode('client');
    setSelectedItem(null);
    if (onSelect) {
      onSelect(folderData.virtualPath, folderData.displayPath);
    }
  };

  // Filter and Sort entries
  const visibleEntries = useMemo(() => {
    let list = browserMode === 'client' && clientFolder ? clientFolder.files : entries;

    if (activeTab === 'folders') {
      list = list.filter((e) => e.type === 'directory');
    } else if (activeTab === 'files') {
      list = list.filter((e) => e.type === 'file');
    } else if (activeTab === 'supported') {
      list = list.filter((e) => e.type === 'file' && e.is_supported);
    }

    if (filterQuery.trim()) {
      const q = filterQuery.toLowerCase().trim();
      list = list.filter((e) => e.name.toLowerCase().includes(q));
    }

    const sorted = [...list];
    if (sortBy === 'name_asc') {
      sorted.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
        return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
      });
    } else if (sortBy === 'name_desc') {
      sorted.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
        return b.name.localeCompare(a.name, undefined, { numeric: true, sensitivity: 'base' });
      });
    } else if (sortBy === 'size_desc') {
      sorted.sort((a, b) => {
        if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
        return (b.size || 0) - (a.size || 0);
      });
    }
    return sorted;
  }, [entries, clientFolder, browserMode, activeTab, filterQuery, sortBy]);

  const getItemIcon = (entry) => {
    if (entry.type === 'directory') {
      if (entry.path?.startsWith('/mnt/unc')) return '🌐';
      return '📁';
    }
    switch (entry.category) {
      case 'pdf': return '📕';
      case 'image': return '🖼️';
      case 'text': return '📄';
      case 'document': return '📑';
      case 'spreadsheet': return '📊';
      default: return '📄';
    }
  };

  if (!isOpen) return null;

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        {/* Hidden Fallback Input */}
        <input
          type="file"
          ref={fileInputRef}
          webkitdirectory="true"
          directory="true"
          multiple
          onChange={handleFileInputChange}
          style={{ display: 'none' }}
        />

        {/* Header */}
        <div className={styles.header}>
          <div className={styles.headerTitleWrap}>
            <span className={styles.titleIcon}>📂</span>
            <span className={styles.title}>FileScout Network Explorer</span>
            <span className={styles.systemBadge}>
              {isRemote ? 'Remote LAN Access' : 'Host Server Access'}
            </span>
          </div>
          <button className={styles.closeBtn} onClick={onClose} title="Close">✕</button>
        </div>

        {/* Source Mode Selector */}
        <div style={{
          display: 'flex',
          gap: '8px',
          padding: '10px 20px',
          background: 'rgba(255, 255, 255, 0.03)',
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)'
        }}>
          <button
            type="button"
            className={`${styles.quickChip} ${browserMode === 'client' ? styles.quickChipActive : ''}`}
            onClick={() => setBrowserMode('client')}
            style={{ padding: '6px 14px', fontSize: '12px', fontWeight: 600 }}
          >
            💻 My Computer (This PC)
          </button>
          <button
            type="button"
            className={`${styles.quickChip} ${browserMode === 'network' ? styles.quickChipActive : ''}`}
            onClick={() => {
              setBrowserMode('network');
              const netShare = quickAccess.find((q) => q.type === 'network')?.path || '\\\\192.168.0.116\\Users';
              handleNavigate(netShare);
            }}
            style={{ padding: '6px 14px', fontSize: '12px', fontWeight: 600 }}
          >
            🌐 Network Shared Folders
          </button>
          {!isRemote && (
            <button
              type="button"
              className={`${styles.quickChip} ${browserMode === 'server' ? styles.quickChipActive : ''}`}
              onClick={() => {
                setBrowserMode('server');
                handleNavigate('/mnt/c');
              }}
              style={{ padding: '6px 14px', fontSize: '12px', fontWeight: 600 }}
            >
              🖥️ Host Server Drives
            </button>
          )}
        </div>

        {/* Quick Access Bar */}
        {quickAccess.length > 0 && (
          <div className={styles.quickBar}>
            <span className={styles.quickLabel}>QUICK SHORTCUTS:</span>
            <div className={styles.quickChips}>
              {quickAccess.map((item) => {
                const isActive = (browserMode === 'client' && item.path === 'client://local') ||
                                 (currentPath === item.path);
                let icon = '📁';
                if (item.type === 'client_local') icon = '💻';
                else if (item.type === 'network') icon = '🌐';
                else if (item.icon === 'windows') icon = '🖥️';

                return (
                  <button
                    key={item.path}
                    type="button"
                    className={`${styles.quickChip} ${isActive ? styles.quickChipActive : ''}`}
                    onClick={() => {
                      if (item.path === 'client://local') {
                        setBrowserMode('client');
                        handlePickLocalFolder();
                      } else {
                        handleNavigate(item.path);
                      }
                    }}
                    title={`${item.name} (${item.display || item.path})`}
                  >
                    <span className={styles.chipIcon}>{icon}</span>
                    <span className={styles.chipName}>{item.name}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Mode 1: Client Local Mode View */}
        {browserMode === 'client' && (
          <div style={{ padding: '16px 20px', background: 'rgba(0,0,0,0.2)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: '#0d1117',
              border: '1px dashed #30363d',
              borderRadius: '10px',
              padding: '14px 18px',
              flexWrap: 'wrap',
              gap: '12px'
            }}>
              <div>
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#f0f6fc', marginBottom: '4px' }}>
                  {clientFolder ? `Selected: ${clientFolder.name}` : 'Select Files or Folder from This Computer'}
                </div>
                <div style={{ fontSize: '12px', color: '#8b949e' }}>
                  {clientFolder
                    ? `✓ ${clientFolder.files.length} file(s) loaded (${clientFolder.files.filter(f => f.is_supported).length} searchable). Review below, then click "Select This Folder".`
                    : 'Choose an entire directory or select multiple files (PDF, images, Office, text) to search.'}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <label
                  style={{
                    background: '#238636',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '9px 15px',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    userSelect: 'none'
                  }}
                  title="Choose an entire directory from your computer"
                >
                  <span>📁</span>
                  <span>{clientFolder ? 'Change Folder' : 'Pick Folder'}</span>
                  <input
                    type="file"
                    webkitdirectory="true"
                    directory="true"
                    multiple
                    onChange={(e) => handleFilesSelected(Array.from(e.target.files || []), true)}
                    style={{ display: 'none' }}
                  />
                </label>
                <label
                  style={{
                    background: '#1f6feb',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '9px 15px',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    userSelect: 'none'
                  }}
                  title="Choose individual files from your computer"
                >
                  <span>📄</span>
                  <span>Pick Files</span>
                  <input
                    type="file"
                    multiple
                    onChange={(e) => handleFilesSelected(Array.from(e.target.files || []), false)}
                    style={{ display: 'none' }}
                  />
                </label>
              </div>
            </div>

            {/* Previously Indexed Folders on this computer */}
            {savedClientFolders.length > 0 && (
              <div style={{
                marginTop: '12px',
                padding: '12px 14px',
                background: '#161b22',
                borderRadius: '8px',
                border: '1px solid #30363d'
              }}>
                <div style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  color: '#8b949e',
                  textTransform: 'uppercase',
                  marginBottom: '8px',
                  letterSpacing: '0.5px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span>📚</span>
                    <span>Previously Indexed Folders on This Machine:</span>
                  </div>
                  <button
                    type="button"
                    onClick={handleClearAllIndex}
                    style={{
                      background: 'transparent',
                      border: '1px solid rgba(248, 81, 73, 0.4)',
                      borderRadius: '4px',
                      color: '#f85149',
                      fontSize: '10px',
                      padding: '2px 8px',
                      cursor: 'pointer',
                      transition: 'all 0.15s ease'
                    }}
                    title="Delete all indexed data for this computer"
                  >
                    🗑️ Clear All
                  </button>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  <button
                    type="button"
                    onClick={() => {
                      const cid = api.getClientId ? api.getClientId() : 'client';
                      const totalCnt = savedClientFolders.reduce((sum, f) => sum + (f.file_count || 0), 0);
                      const allData = {
                        name: 'All Indexed Files',
                        virtualPath: `client://${cid}`,
                        displayPath: `[My Computer] All Indexed Files (${totalCnt} files)`,
                        files: []
                      };
                      if (api.setActiveLocalFolder) api.setActiveLocalFolder(allData);
                      setClientFolder(allData);
                      if (onSelect) onSelect(allData.virtualPath, allData.displayPath);
                      onClose();
                    }}
                    style={{
                      background: 'rgba(56, 139, 253, 0.15)',
                      border: '1px solid #388bfd',
                      color: '#58a6ff',
                      borderRadius: '6px',
                      padding: '7px 12px',
                      fontSize: '12px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      fontWeight: 600,
                      transition: 'all 0.15s ease'
                    }}
                    title="Search across all indexed files on this computer"
                  >
                    <span>🔍</span>
                    <span>All My Indexed Files</span>
                    <span style={{
                      fontSize: '11px',
                      color: '#58a6ff',
                      background: 'rgba(56, 139, 253, 0.2)',
                      padding: '1px 7px',
                      borderRadius: '10px'
                    }}>
                      {savedClientFolders.reduce((sum, f) => sum + (f.file_count || 0), 0)} files
                    </span>
                  </button>
                  {savedClientFolders.map((sf) => {
                    const isSelected = clientFolder?.name === sf.folder_name;
                    return (
                      <div
                        key={sf.virtual_path}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          background: isSelected ? 'rgba(35, 134, 54, 0.25)' : '#21262d',
                          border: isSelected ? '1px solid #2ea043' : '1px solid #30363d',
                          borderRadius: '6px',
                          overflow: 'hidden'
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => handleSelectSavedFolder(sf)}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            color: '#f0f6fc',
                            padding: '7px 11px',
                            fontSize: '12px',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            transition: 'all 0.15s ease'
                          }}
                          title={`Select ${sf.folder_name} (${sf.file_count} files already indexed)`}
                        >
                          <span>📁</span>
                          <span style={{ fontWeight: 600 }}>{sf.folder_name}</span>
                          <span style={{
                            fontSize: '11px',
                            color: '#3fb950',
                            background: 'rgba(63, 185, 80, 0.12)',
                            padding: '1px 7px',
                            borderRadius: '10px',
                            fontWeight: 500
                          }}>
                            {sf.file_count} files
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleDeleteSavedFolder(e, sf.folder_name)}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            borderLeft: '1px solid rgba(255, 255, 255, 0.08)',
                            color: '#8b949e',
                            padding: '7px 9px',
                            fontSize: '12px',
                            cursor: 'pointer',
                            transition: 'color 0.15s ease'
                          }}
                          title={`Delete ${sf.folder_name} from index`}
                          onMouseEnter={(e) => e.target.style.color = '#f85149'}
                          onMouseLeave={(e) => e.target.style.color = '#8b949e'}
                        >
                          ✕
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Mode 2: Network & Server Path Input */}
        {browserMode !== 'client' && (
          <div className={styles.pathRow}>
            <div className={styles.inputWrapper}>
              <span className={styles.inputPrefix}>📍</span>
              <input
                ref={inputRef}
                className={styles.pathInput}
                value={manualPath}
                onChange={(e) => setManualPath(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Enter network UNC path (e.g. \\\\192.168.0.116\\Users or \\\\server\\share)"
              />
              {manualPath && (
                <button
                  type="button"
                  className={styles.clearBtn}
                  onClick={() => setManualPath('')}
                >
                  ✕
                </button>
              )}
            </div>
            <button type="button" className={styles.goBtn} onClick={handleManualGo}>
              Connect
            </button>
          </div>
        )}

        {error && <div className={styles.error}>⚠ {error}</div>}

        {/* Toolbar: Filter Tabs + Search */}
        <div className={styles.toolbar}>
          <div className={styles.filterTabs}>
            <button
              type="button"
              className={`${styles.tabBtn} ${activeTab === 'all' ? styles.tabBtnActive : ''}`}
              onClick={() => setActiveTab('all')}
            >
              All ({visibleEntries.length})
            </button>
            <button
              type="button"
              className={`${styles.tabBtn} ${activeTab === 'supported' ? styles.tabBtnActive : ''}`}
              onClick={() => setActiveTab('supported')}
              title="Files with OCR & text search support"
            >
              ✨ Searchable
            </button>
          </div>

          <div className={styles.toolControls}>
            <div className={styles.filterInputWrapper}>
              <span className={styles.filterIcon}>🔍</span>
              <input
                className={styles.filterInput}
                value={filterQuery}
                onChange={(e) => setFilterQuery(e.target.value)}
                placeholder="Filter files..."
              />
            </div>

            <select
              className={styles.sortSelect}
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
            >
              <option value="name_asc">Name (A-Z)</option>
              <option value="name_desc">Name (Z-A)</option>
              <option value="size_desc">Size (Largest)</option>
            </select>
          </div>
        </div>

        {/* Directory & File Listing */}
        <div className={styles.list}>
          {browserMode !== 'client' && parentPath && (
            <div className={styles.entryUp} onClick={handleGoUp}>
              <span className={styles.entryIcon}>⬆</span>
              <span className={styles.entryName}>.. (Go up one level)</span>
            </div>
          )}

          {loading && (
            <div className={styles.loading}>
              <span className={styles.spinner}></span>
              <span>Scanning files and folders...</span>
            </div>
          )}

          {!loading && visibleEntries.length === 0 && (
            <div className={styles.empty}>
              {browserMode === 'client' && !clientFolder ? (
                <span>Click "Pick Folder" or "Pick Files" above to select files from your computer.</span>
              ) : filterQuery ? (
                <span>🔍 No files match "{filterQuery}".</span>
              ) : (
                <span>📁 No files found in this selection.</span>
              )}
            </div>
          )}

          {!loading && visibleEntries.map((entry) => {
            const isDir = entry.type === 'directory';
            const icon = getItemIcon(entry);
            const isSelected = selectedItem?.relativePath === entry.relativePath || selectedItem?.path === entry.path;

            return (
              <div
                key={entry.relativePath || entry.path}
                className={`${styles.entry} ${isDir ? styles.entryDir : styles.entryFile} ${isSelected ? styles.entrySelected : ''}`}
                onClick={() => handleEntryClick(entry)}
                onDoubleClick={() => isDir && handleNavigate(entry.path)}
                title={entry.relativePath || entry.display_path || entry.path}
              >
                <span className={styles.entryIcon}>{icon}</span>
                <span className={styles.entryName}>{entry.relativePath || entry.name}</span>

                {!isDir && (
                  <div className={styles.fileMeta}>
                    {entry.is_supported && (
                      <span className={styles.supportedBadge}>✨ Searchable</span>
                    )}
                    {entry.size_formatted && (
                      <span className={styles.fileSize}>{entry.size_formatted}</span>
                    )}
                    <button
                      type="button"
                      className={styles.previewBtn}
                      onClick={(e) => handleOpenFile(e, entry.relativePath || entry.path)}
                    >
                      Preview ↗
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className={styles.footer}>
          <div className={styles.selectedBox}>
            <span className={styles.selectedLabel}>Target Search Folder:</span>
            <span className={styles.selectedValue}>
              {browserMode === 'client' && clientFolder
                ? clientFolder.displayPath
                : displayPath || currentPath}
            </span>
          </div>
          <button
            type="button"
            className={styles.selectBtn}
            onClick={handleSelectFolder}
            disabled={browserMode === 'client' && !clientFolder}
          >
            ✓ Select This Folder
          </button>
        </div>
      </div>
    </div>
  );
}
