import React, { useState } from 'react';
import styles from './ResultCard.module.css';
import webApi from '../webApi';

const FILE_ICONS = {
  '.pdf':  '⬡',
  '.jpg':  '◻', '.jpeg': '◻', '.png': '◻', '.tiff': '◻', '.webp': '◻', '.svg': '◻',
  '.html': '⟨⟩', '.htm': '⟨⟩', '.jsx': '⟨⟩', '.tsx': '⟨⟩', '.vue': '⟨⟩',
  '.js':   'JS', '.ts':  'TS',
  '.css':  '✦',
  '.json': '{ }', '.xml': '< >',
  '.csv':  '⊞', '.tsv': '⊞',
  '.txt':  '≡', '.md': '≡', '.log': '≡',
  '.py':   '🐍',
};

function getIcon(filepath) {
  if (!filepath) return '◈';
  const ext = filepath.slice(filepath.lastIndexOf('.')).toLowerCase();
  return FILE_ICONS[ext] || '◈';
}

function getTypeLabel(filepath) {
  if (!filepath) return 'FILE';
  const ext = filepath.slice(filepath.lastIndexOf('.')).toLowerCase();
  return ext.replace('.', '').toUpperCase() || 'FILE';
}

function getConfidenceColor(conf) {
  if (conf >= 0.8) return '#40c080';
  if (conf >= 0.5) return '#f0c040';
  return '#e05050';
}

export default function ResultCard({ result, keyword, forceExpanded }) {
  const [expanded, setExpanded] = useState(true);
  const [copiedIdx, setCopiedIdx] = useState(null);

  React.useEffect(() => {
    if (typeof forceExpanded === 'boolean') {
      setExpanded(forceExpanded);
    }
  }, [forceExpanded]);

  const openFile = (e) => {
    e.stopPropagation();
    const targetPath = result.filepath || result.file;
    if (window.fileScout) {
      window.fileScout.openFile(targetPath);
    } else if (webApi && webApi.openFile) {
      webApi.openFile(targetPath);
    }
  };

  const fileStr = result.file || result.filepath || '';
  let filename = 'Unknown file';
  let directory = '';

  if (fileStr.startsWith('client://')) {
    const parts = fileStr.replace('client://', '').split('/');
    filename = parts[parts.length - 1];
    const folderName = parts[1] || '';
    const sub = parts.slice(2, -1).join('/');
    directory = `[My Computer] ${folderName}${sub ? '/' + sub : ''}`;
  } else {
    filename = fileStr ? fileStr.split(/[\\/]/).pop() : 'Unknown file';
    directory = fileStr.includes('/') || fileStr.includes('\\')
      ? fileStr.slice(0, fileStr.lastIndexOf(fileStr.includes('/') ? '/' : '\\'))
      : '';
  }

  const confidence = result.confidence;
  const extractionMethod = result.extraction_method || '';
  const detectedLang = result.detected_language || '';

  return (
    <div className={styles.card}>
      <div className={styles.header} onClick={() => setExpanded(e => !e)}>
        <div className={styles.fileIcon}>{getIcon(fileStr)}</div>
        <div className={styles.fileInfo}>
          <div className={styles.fileName}>
            {filename}
            <span className={styles.typeTag}>{getTypeLabel(fileStr)}</span>
          </div>
          <div className={styles.metaRow}>
            {directory && <span className={styles.filePath}>{directory}</span>}
            {extractionMethod && (
              <span className={styles.methodTag}>{extractionMethod}</span>
            )}
            {detectedLang && detectedLang !== 'en' && (
              <span className={styles.langTag}>{detectedLang.toUpperCase()}</span>
            )}
          </div>
        </div>
        {confidence > 0 && (
          <span className={styles.confBadge} style={{ color: getConfidenceColor(confidence) }}>
            {Math.round(confidence * 100)}%
          </span>
        )}
        <div className={styles.matchBadge}>{result.match_count} match{result.match_count !== 1 ? 'es' : ''}</div>
        <button className={styles.openBtn} onClick={openFile} title="Open file">↗</button>
        <div className={styles.chevron} style={{ transform: expanded ? 'rotate(90deg)' : 'rotate(0)' }}>›</div>
      </div>

      {expanded && Array.isArray(result.matches) && (
        <div className={styles.snippets}>
          {result.matches.map((m, i) => {
            const rawSnippet = m?.snippet || m?.match_text || m?.word || '';
            const matchText = m?.match_text || m?.word || keyword || '';
            const hasOffsets = typeof m?.match_start === 'number' && typeof m?.match_end === 'number' && m?.snippet;

            return (
              <div key={i} className={styles.snippet}>
                <span className={styles.snippetNum}>{i + 1}</span>
                <span className={styles.snippetText}>
                  {hasOffsets ? (
                    <>
                      <span className={styles.context}>…{m.snippet.slice(0, m.match_start)}</span>
                      <mark className={styles.highlight}>{matchText}</mark>
                      <span className={styles.context}>{m.snippet.slice(m.match_end)}…</span>
                    </>
                  ) : (
                    <>
                      <span className={styles.context}>…</span>
                      <mark className={styles.highlight}>{matchText || rawSnippet}</mark>
                      <span className={styles.context}>…</span>
                    </>
                  )}
                </span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    const textToCopy = m?.snippet || m?.match_text || keyword;
                    if (navigator.clipboard) {
                      navigator.clipboard.writeText(textToCopy);
                    }
                    setCopiedIdx(i);
                    setTimeout(() => setCopiedIdx(null), 1500);
                  }}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: copiedIdx === i ? '#3fb950' : 'var(--muted)',
                    fontSize: '11px',
                    cursor: 'pointer',
                    padding: '2px 6px',
                    marginLeft: 'auto',
                    flexShrink: 0,
                    opacity: 0.7
                  }}
                  title="Copy snippet text"
                >
                  {copiedIdx === i ? '✓ Copied' : '📋'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
