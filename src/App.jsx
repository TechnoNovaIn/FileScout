import React, { useState, useEffect, useRef } from 'react';
import styles from './App.module.css';
import ResultCard from './components/ResultCard';
import ProgressLog from './components/ProgressLog';
import LanguageSelector from './components/LanguageSelector';
import OcrQualitySelector from './components/OcrQualitySelector';
import FolderBrowser from './components/FolderBrowser';
import webApi from './webApi';

// Use Electron IPC if available, otherwise fall back to web API
const ipc = window.fileScout || webApi;
const isWeb = !window.fileScout;

export default function App() {
  const [folder, setFolder]                   = useState(() => localStorage.getItem('filescout_last_folder') || '');
  const [displayFolder, setDisplayFolder]     = useState(() => localStorage.getItem('filescout_last_display_folder') || '');
  const [keyword, setKeyword]                 = useState('');
  const [searching, setSearching]             = useState(false);
  const [results, setResults]                 = useState(null);
  const [progress, setProgress]               = useState([]);
  const [error, setError]                     = useState('');
  const [searchLanguage, setLang]             = useState('en');
  const [ocrQuality, setOcrQuality]           = useState('balanced');
  const inputRef = useRef(null);

  // Indexing state
  const [indexStatus, setIndexStatus]     = useState(null); // { total_files, indexed_files, is_fully_indexed }
  const [indexing, setIndexing]           = useState(false);
  const [indexProgress, setIndexProgress] = useState([]);
  const [indexResult, setIndexResult]     = useState(null);

  // Folder browser state (web mode only)
  const [browserOpen, setBrowserOpen]     = useState(false);

  // Search enhancement state
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [expandAll, setExpandAll] = useState(true);
  const [recentSearches, setRecentSearches] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('filescout_recent_searches') || '[]');
    } catch {
      return [];
    }
  });

  const getFileCategory = (filename) => {
    if (!filename) return 'other';
    const ext = filename.slice(filename.lastIndexOf('.')).toLowerCase();
    if (ext === '.pdf') return 'pdf';
    if (['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.svg'].includes(ext)) return 'image';
    if (['.docx', '.xlsx', '.doc', '.xls', '.ppt', '.pptx', '.odt', '.rtf'].includes(ext)) return 'document';
    if (['.txt', '.csv', '.tsv', '.log', '.md', '.json', '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.xml', '.sql', '.yaml', '.yml', '.ini', '.conf', '.sh', '.ps1', '.bat'].includes(ext)) return 'code';
    return 'other';
  };

  // Check index status when folder changes
  useEffect(() => {
    if (!ipc) {
      setIndexStatus(null);
      return;
    }
    ipc.indexStatus(folder).then((status) => {
      if (status.ok && (status.indexed_files > 0 || status.total_files > 0)) {
        setIndexStatus(status);
        if (!displayFolder && status.display_folder) {
          setDisplayFolder(status.display_folder);
        }
      } else {
        setIndexStatus(null);
      }
    }).catch(() => setIndexStatus(null));
  }, [folder]);

  // Initial startup: check if this computer already has indexed files
  useEffect(() => {
    if (!isWeb || !webApi.getClientStatus) return;
    webApi.getClientStatus().then((status) => {
      if (status?.ok && status.total_indexed_files > 0) {
        const lastFolder = localStorage.getItem('filescout_last_folder');
        const lastDisplay = localStorage.getItem('filescout_last_display_folder');

        if (lastFolder && lastFolder !== 'client://local') {
          setFolder(lastFolder);
          setDisplayFolder(lastDisplay || lastFolder);
        } else {
          const cid = webApi.getClientId();
          const allPath = `client://${cid}`;
          const allDisp = `[My Computer] All Indexed Files (${status.total_indexed_files} files)`;
          setFolder(allPath);
          setDisplayFolder(allDisp);
          localStorage.setItem('filescout_last_folder', allPath);
          localStorage.setItem('filescout_last_display_folder', allDisp);
        }

        setIndexStatus({
          ok: true,
          total_files: status.total_indexed_files,
          indexed_files: status.total_indexed_files,
          is_fully_indexed: true,
        });
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!ipc) return;
    ipc.onSetFolder((f) => setFolder(f));
    ipc.onSearchResults((data) => {
      setSearching(false);
      if (data.ok) {
        const arr = Array.isArray(data.results)
          ? data.results
          : Array.isArray(data.results?.results)
            ? data.results.results
            : [];
        setResults(arr);
        setError('');
      } else {
        setError(data.error || 'Search failed.');
        setResults([]);
      }
    });
    ipc.onProgress((line) => {
      setProgress((p) => [...p.slice(-199), line]);
    });
    ipc.onIndexProgress((line) => {
      setIndexProgress((p) => [...p.slice(-199), line]);
    });
    ipc.onIndexResults((data) => {
      setIndexing(false);
      if (data.ok) {
        setIndexResult(data);
        // Refresh index status
        if (ipc && folder) {
          ipc.indexStatus(folder).then((status) => {
            if (status.ok) setIndexStatus(status);
          });
        }
      } else {
        setError(data.error || 'Indexing failed.');
      }
    });
    return () => {
      ipc.removeAllListeners('set-folder');
      ipc.removeAllListeners('search-results');
      ipc.removeAllListeners('search-progress');
      ipc.removeAllListeners('index-progress');
      ipc.removeAllListeners('index-results');
    };
  }, [folder]);

  const handleBrowse = async () => {
    if (isWeb) {
      // Open the folder browser modal
      setBrowserOpen(true);
      return;
    }
    if (!ipc) return;
    const f = await ipc.browseFolder();
    if (f) {
      setFolder(f);
      setDisplayFolder(f);
    }
  };

  const handleFolderSelect = (path, dispPath) => {
    setFolder(path);
    setDisplayFolder(dispPath || path);
    if (path) {
      localStorage.setItem('filescout_last_folder', path);
      localStorage.setItem('filescout_last_display_folder', dispPath || path);
    }
  };

  const handleSearch = () => {
    const q = keyword.trim();
    if (!q) { setError('Please enter a keyword.'); return; }

    let activeFolder = (folder || '').trim();
    if (!activeFolder || activeFolder === 'client://local') {
      if (isWeb && webApi.getClientId) {
        activeFolder = `client://${webApi.getClientId()}`;
        setFolder(activeFolder);
      } else {
        setError('Please select a folder first.');
        return;
      }
    }

    // Save to recent searches
    setRecentSearches((prev) => {
      const updated = [q, ...prev.filter((item) => item.toLowerCase() !== q.toLowerCase())].slice(0, 5);
      localStorage.setItem('filescout_recent_searches', JSON.stringify(updated));
      return updated;
    });

    setSelectedCategory('all');
    setError('');
    setResults(null);
    setProgress([]);
    setSearching(true);

    const lang = searchLanguage !== 'en' ? searchLanguage : undefined;
    const searchOnly = indexStatus?.is_fully_indexed || activeFolder.startsWith('client://');
    ipc.runSearch(activeFolder, q, 80, undefined, lang, ocrQuality, searchOnly);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch();
  };

  const handleIndex = (force = false) => {
    if (folder.startsWith('client://') && (!ipc.getActiveLocalFolder || !ipc.getActiveLocalFolder()?.files?.length)) {
      setBrowserOpen(true);
      return;
    }
    if (!folder.trim()) { setError('Please select a folder first.'); return; }
    setError('');
    setIndexing(true);
    setIndexProgress([]);
    setIndexResult(null);
    const lang = searchLanguage !== 'en' ? searchLanguage : undefined;
    ipc.indexFolder(folder.trim(), ocrQuality, force, lang);
  };

  // Allow typing a folder path directly in web mode
  const handleFolderInput = (e) => {
    setFolder(e.target.value);
    setDisplayFolder(e.target.value);
    if (e.target.value) {
      localStorage.setItem('filescout_last_folder', e.target.value);
      localStorage.setItem('filescout_last_display_folder', e.target.value);
    }
  };

  const handleFolderInputKeyDown = (e) => {
    if (e.key === 'Enter') {
      // Trigger index status refresh
      if (folder.trim()) {
        ipc.indexStatus(folder.trim()).then((status) => {
          if (status.ok) setIndexStatus(status);
          else setIndexStatus(null);
        }).catch(() => setIndexStatus(null));
      }
    }
  };

  const totalMatches = Array.isArray(results)
    ? results.reduce((sum, r) => sum + (r.match_count || 0), 0)
    : 0;

  const filteredResults = React.useMemo(() => {
    if (!Array.isArray(results)) return [];
    if (selectedCategory === 'all') return results;
    return results.filter((r) => getFileCategory(r.file || r.filepath) === selectedCategory);
  }, [results, selectedCategory]);

  const categories = React.useMemo(() => {
    if (!Array.isArray(results) || results.length === 0) return [];
    const counts = { all: results.length, pdf: 0, image: 0, document: 0, code: 0, other: 0 };
    for (const r of results) {
      const cat = getFileCategory(r.file || r.filepath);
      counts[cat] = (counts[cat] || 0) + 1;
    }
    const list = [{ id: 'all', label: 'All Types', count: counts.all }];
    if (counts.pdf > 0) list.push({ id: 'pdf', label: '📕 PDFs', count: counts.pdf });
    if (counts.image > 0) list.push({ id: 'image', label: '🖼️ Images', count: counts.image });
    if (counts.document > 0) list.push({ id: 'document', label: '📄 Docs', count: counts.document });
    if (counts.code > 0) list.push({ id: 'code', label: '💻 Code / Text', count: counts.code });
    if (counts.other > 0) list.push({ id: 'other', label: '📦 Other', count: counts.other });
    return list;
  }, [results]);

  const isIndexed = indexStatus?.is_fully_indexed;
  const indexPercent = indexStatus
    ? Math.round((indexStatus.indexed_files / (indexStatus.total_files || 1)) * 100)
    : 0;

  return (
    <div className={styles.root}>
      <header className={styles.titlebar}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>◈</span>
          <span className={styles.logoText}>FileScout</span>
        </div>
        <div className={styles.tagline}>search inside any file</div>
      </header>

      <div className={styles.folderRow}>
        <div className={styles.folderLabel}>FOLDER</div>
        {isWeb ? (
          <input
            className={styles.folderInput}
            type="text"
            value={displayFolder || folder}
            onChange={handleFolderInput}
            onKeyDown={handleFolderInputKeyDown}
            placeholder="Select a folder from your computer, network share, or enter path…"
          />
        ) : (
          <div className={styles.folderPath} title={displayFolder || folder}>
            {(displayFolder || folder) || <span className={styles.placeholder}>No folder selected — right-click a folder or browse below</span>}
          </div>
        )}
        <button className={styles.browseBtn} onClick={handleBrowse}>Browse</button>
      </div>

      {/* Index status bar */}
      {folder && (
        <div className={styles.indexBar}>
          <div className={styles.indexInfo}>
            {isIndexed ? (
              <span className={`${styles.indexBadge} ${styles.Indexed}`}>INDEXED</span>
            ) : (
              <span className={styles.indexBadge}>{indexPercent}% indexed</span>
            )}
            <span className={styles.indexDetail}>
              {indexStatus ? `${indexStatus.indexed_files}/${indexStatus.total_files} files` : 'Ready to index'}
            </span>
          </div>
          {!indexing && (
            <button className={styles.indexBtn} onClick={() => handleIndex(isIndexed)}>
              {isIndexed ? '+ Add / Re-index' : (folder.startsWith('client://') ? '⚡ Upload & Index Files' : 'Index Folder')}
            </button>
          )}
          {indexing && (
            <span className={styles.indexingLabel}>
              <span className={styles.spinner} /> Uploading & Indexing...
            </span>
          )}
        </div>
      )}

      {/* Client files loaded indicator */}
      {folder.startsWith('client://') && ipc.getActiveLocalFolder && ipc.getActiveLocalFolder()?.files && (
        <div style={{
          fontSize: '12px',
          color: '#8b949e',
          marginTop: '-6px',
          marginBottom: '14px',
          padding: '6px 12px',
          background: 'rgba(255, 255, 255, 0.03)',
          border: '1px solid rgba(255, 255, 255, 0.06)',
          borderRadius: '6px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between'
        }}>
          <span>📁 <strong>{ipc.getActiveLocalFolder().files.length}</strong> client file(s) loaded into memory</span>
          <button
            type="button"
            onClick={() => setBrowserOpen(true)}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#58a6ff',
              cursor: 'pointer',
              textDecoration: 'underline',
              fontSize: '12px',
              padding: 0
            }}
          >
            View / Change Files
          </button>
        </div>
      )}

      {/* Index progress */}
      {indexing && indexProgress.length > 0 && (
        <div className={styles.indexProgressWrap}>
          <ProgressLog lines={indexProgress} searching={indexing} />
        </div>
      )}

      {/* Index result summary */}
      {indexResult && !indexing && (
        <div className={styles.indexResult}>
          Indexed {indexResult.indexed} file{indexResult.indexed !== 1 ? 's' : ''}
          {indexResult.skipped > 0 && `, ${indexResult.skipped} skipped`}
          {indexResult.errors > 0 && `, ${indexResult.errors} errors`}
          {indexResult.alreadyIndexed > 0 && ` (${indexResult.alreadyIndexed} already cached)`}
          {' '}&mdash; {indexResult.searchTime}s
        </div>
      )}

      <div className={styles.searchRow}>
        <div className={styles.searchWrap}>
          <span className={styles.searchIcon}>⌕</span>
          <input
            ref={inputRef}
            className={styles.searchInput}
            type="text"
            placeholder={isIndexed ? `Type a keyword to search ${indexStatus?.indexed_files || ''} indexed files — press Enter` : "Type a keyword and press Enter…"}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={searching}
            autoFocus
          />
          {keyword && (
            <button className={styles.clearBtn} onClick={() => { setKeyword(''); inputRef.current?.focus(); }}>✕</button>
          )}
        </div>

        <LanguageSelector value={searchLanguage} onChange={setLang} />
        <OcrQualitySelector value={ocrQuality} onChange={setOcrQuality} />

        <button
          className={styles.searchBtn}
          onClick={handleSearch}
          disabled={searching || !keyword.trim() || (!folder && !isIndexed)}
        >
          {searching ? <span className={styles.spinner} /> : 'Search'}
        </button>
      </div>

      {/* Recent Searches Pills */}
      {recentSearches.length > 0 && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '6px 24px 8px',
          background: 'var(--bg2)',
          borderBottom: '1px solid var(--border)',
          fontSize: '11px',
          color: 'var(--muted)',
          flexWrap: 'wrap'
        }}>
          <span style={{ fontWeight: 600 }}>Recent:</span>
          {recentSearches.map((term, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => {
                setKeyword(term);
                let activeFolder = (folder || '').trim();
                if (!activeFolder || activeFolder === 'client://local') {
                  if (isWeb && webApi.getClientId) {
                    activeFolder = `client://${webApi.getClientId()}`;
                    setFolder(activeFolder);
                  }
                }
                const lang = searchLanguage !== 'en' ? searchLanguage : undefined;
                const searchOnly = indexStatus?.is_fully_indexed || activeFolder.startsWith('client://');
                setError('');
                setResults(null);
                setProgress([]);
                setSearching(true);
                ipc.runSearch(activeFolder, term, 80, undefined, lang, ocrQuality, searchOnly);
              }}
              style={{
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid rgba(255, 255, 255, 0.12)',
                color: '#58a6ff',
                borderRadius: '12px',
                padding: '2px 9px',
                fontSize: '11px',
                cursor: 'pointer',
                transition: 'all 0.15s ease'
              }}
              onMouseEnter={(e) => e.target.style.background = 'rgba(88, 166, 255, 0.15)'}
              onMouseLeave={(e) => e.target.style.background = 'rgba(255, 255, 255, 0.05)'}
            >
              {term}
            </button>
          ))}
          <button
            type="button"
            onClick={() => {
              setRecentSearches([]);
              localStorage.removeItem('filescout_recent_searches');
            }}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--muted)',
              fontSize: '10px',
              cursor: 'pointer',
              marginLeft: '4px',
              textDecoration: 'underline'
            }}
            title="Clear search history"
          >
            clear
          </button>
        </div>
      )}

      {error && <div className={styles.error}>⚠ {error}</div>}

      <div className={styles.body}>
        <div className={styles.resultsPane}>
          {results === null && !searching && (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>◈</div>
              <div className={styles.emptyTitle}>Ready to scout</div>
              <div className={styles.emptyDesc}>
                {isIndexed
                  ? `Instant search ready across your indexed files. Type any keyword above.`
                  : "Select a folder or files from your computer, index them, then search"}
              </div>
            </div>
          )}

          {Array.isArray(results) && results.length === 0 && (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>∅</div>
              <div className={styles.emptyTitle}>No matches found</div>
              <div className={styles.emptyDesc}>Try a different keyword or folder</div>
            </div>
          )}

          {Array.isArray(results) && results.length > 0 && (
            <>
              <div className={styles.resultsSummary}>
                <span className={styles.badge}>{filteredResults.length} of {results.length} file{results.length !== 1 ? 's' : ''}</span>
                <span className={styles.badge2}>{totalMatches} match{totalMatches !== 1 ? 'es' : ''}</span>
                <span className={styles.summaryKeyword}>for "{keyword}"</span>
                {searchLanguage !== 'en' && (
                  <span className={styles.summaryLang}>{searchLanguage.toUpperCase()}</span>
                )}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px' }}>
                  <button
                    type="button"
                    onClick={() => setExpandAll((prev) => !prev)}
                    style={{
                      background: 'rgba(255, 255, 255, 0.06)',
                      border: '1px solid var(--border)',
                      borderRadius: '4px',
                      color: 'var(--text)',
                      fontSize: '11px',
                      padding: '3px 9px',
                      cursor: 'pointer'
                    }}
                    title={expandAll ? "Collapse all snippet blocks" : "Expand all snippet blocks"}
                  >
                    {expandAll ? '⤡ Collapse All' : '⤢ Expand All'}
                  </button>
                </div>
              </div>

              {/* Category Filter Chips */}
              {categories.length > 1 && (
                <div style={{
                  display: 'flex',
                  gap: '8px',
                  padding: '8px 24px 10px',
                  borderBottom: '1px solid var(--border)',
                  background: 'var(--bg2)',
                  alignItems: 'center',
                  flexWrap: 'wrap'
                }}>
                  <span style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase' }}>Filter:</span>
                  {categories.map((cat) => (
                    <button
                      key={cat.id}
                      type="button"
                      onClick={() => setSelectedCategory(cat.id)}
                      style={{
                        background: selectedCategory === cat.id ? 'var(--accent)' : 'rgba(255, 255, 255, 0.05)',
                        color: selectedCategory === cat.id ? '#0d1117' : 'var(--text)',
                        border: '1px solid ' + (selectedCategory === cat.id ? 'var(--accent)' : 'var(--border)'),
                        borderRadius: '12px',
                        padding: '2px 10px',
                        fontSize: '11px',
                        fontWeight: selectedCategory === cat.id ? 700 : 500,
                        cursor: 'pointer',
                        transition: 'all 0.15s'
                      }}
                    >
                      {cat.label} ({cat.count})
                    </button>
                  ))}
                </div>
              )}

              <div className={styles.resultsList}>
                {filteredResults.map((r, i) => (
                  <ResultCard key={i} result={r} keyword={keyword} forceExpanded={expandAll} />
                ))}
              </div>
            </>
          )}
        </div>

        {(searching || progress.length > 0) && (
          <ProgressLog lines={progress} searching={searching} />
        )}
      </div>

      {/* Folder browser modal (web mode only) */}
      {isWeb && (
        <FolderBrowser
          isOpen={browserOpen}
          onSelect={handleFolderSelect}
          onClose={() => setBrowserOpen(false)}
          api={ipc}
        />
      )}
    </div>
  );
}
