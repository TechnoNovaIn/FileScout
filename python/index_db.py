#!/usr/bin/env python3
import sqlite3
import os
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Optional
import threading
import time

sys_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(sys_dir, 'index.db')

from search_engine import find_matches_multilingual

class IndexDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._index_lock = threading.Lock()
        self._create_tables()

    def _create_tables(self):
        with self._index_lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
            except Exception:
                pass
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filepath TEXT UNIQUE,
                    filename TEXT,
                    extension TEXT,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ocr_method TEXT,
                    confidence REAL,
                    language TEXT,
                    content_hash TEXT,
                    full_text TEXT,
                    client_id TEXT
                )
            ''')

            # Ensure columns exist on existing installations
            cursor.execute("PRAGMA table_info(files)")
            existing_cols = [col['name'] for col in cursor.fetchall()]
            if 'full_text' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE files ADD COLUMN full_text TEXT")
                except Exception:
                    pass
            if 'client_id' not in existing_cols:
                try:
                    cursor.execute("ALTER TABLE files ADD COLUMN client_id TEXT")
                except Exception:
                    pass

            # Primary FTS5 full-text search table with porter stemmer and unicode61 tokenizer
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    filepath UNINDEXED,
                    filename,
                    content,
                    tokenize='porter unicode61'
                )
            ''')

            # Secondary Trigram FTS5 table for instant substring / infix searches inside codes & numbers
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS files_trigram USING fts5(
                    filepath UNINDEXED,
                    content,
                    tokenize='trigram'
                )
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_language ON files(language)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_filepath ON files(filepath)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_content_hash ON files(content_hash)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_client_id ON files(client_id)
            ''')
            
            self.conn.commit()

    def add_file(self, filepath: str, filename: str, extension: str, 
                 ocr_method: str, confidence: float, language: str,
                 content_hash: str, text: str = "", words: List[str] = None, positions: List[int] = None,
                 client_id: str = None):
        """Add or update an indexed file with full text in SQLite FTS5 & Trigram indices."""
        if not text and words:
            text = " ".join(words)
        
        clean_text = text or ""

        with self._index_lock:
            cursor = self.conn.cursor()
            
            try:
                cursor.execute('SELECT id FROM files WHERE filepath = ?', (filepath,))
                existing = cursor.fetchone()
                if existing:
                    file_id = existing['id']
                    cursor.execute('''
                        UPDATE files SET filename = ?, extension = ?, ocr_method = ?,
                                         confidence = ?, language = ?, content_hash = ?,
                                         full_text = ?, client_id = COALESCE(?, client_id),
                                         indexed_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (filename, extension, ocr_method, confidence, language, content_hash, clean_text, client_id, file_id))
                else:
                    cursor.execute('''
                        INSERT INTO files (filepath, filename, extension, ocr_method, 
                                         confidence, language, content_hash, full_text, client_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (filepath, filename, extension, ocr_method, confidence, 
                          language, content_hash, clean_text, client_id))
                    file_id = cursor.lastrowid
                
                # 1. Update FTS5 virtual table
                try:
                    cursor.execute('DELETE FROM files_fts WHERE filepath = ?', (filepath,))
                    cursor.execute('''
                        INSERT INTO files_fts (filepath, filename, content)
                        VALUES (?, ?, ?)
                    ''', (filepath, filename, clean_text))
                except Exception as fts_err:
                    print(f"Warning: FTS5 update error for {filepath}: {fts_err}")

                # 2. Update Trigram virtual table for substring lookups
                try:
                    cursor.execute('DELETE FROM files_trigram WHERE filepath = ?', (filepath,))
                    cursor.execute('''
                        INSERT INTO files_trigram (filepath, content)
                        VALUES (?, ?)
                    ''', (filepath, clean_text))
                except Exception as tri_err:
                    print(f"Warning: Trigram update error for {filepath}: {tri_err}")

                self.conn.commit()
                return file_id
            except sqlite3.Error as e:
                self.conn.rollback()
                print(f"Database error adding file {filepath}: {e}")
                return None

    def search(self, keyword: str, language: str = None, folder: str = None, context_chars: int = 80, client_id: str = None) -> List[Dict]:
        """Search indexed documents using FTS5 (BM25 ranked), Trigram substring matching, and fallback."""
        if not keyword or not keyword.strip():
            return []

        kw = keyword.strip()
        tokens = [re.sub(r'[^\w\-]', '', w) for w in kw.split() if w]
        if not tokens:
            return []

        cursor = self.conn.cursor()

        # Build FTS5 query with prefix and phrase support
        if len(tokens) == 1:
            token = tokens[0]
            fts_query = f'"{token}" OR {token}*'
        else:
            and_part = " AND ".join(f'{t}*' for t in tokens)
            phrase = " ".join(tokens)
            fts_query = f'("{phrase}") OR ({and_part})'

        # Folder and language clauses
        folder_clauses = []
        sql_params = []
        # If client_id is given and folder refers to the client root or is empty, omit folder clause to search all client files
        is_client_root = client_id and (not folder or folder in ('all', 'client://', 'client://local', f'client://{client_id}'))
        if folder and not is_client_root:
            f_slash = folder.rstrip('/\\').replace('\\', '/')
            b_slash = folder.rstrip('/\\').replace('/', '\\')
            folder_clauses.append('(f.filepath LIKE ? OR f.filepath LIKE ? OR f.filepath LIKE ? OR f.filepath LIKE ?)')
            sql_params.extend([f_slash + '/%', f_slash + '%', b_slash + '\\%', b_slash + '%'])

        lang_clause = ''
        if language and language != 'en':
            lang_clause = ' AND f.language = ?'

        # Client isolation clause
        client_clause = ''
        client_params = []
        if client_id:
            client_clause = 'f.client_id = ?'
            client_params = [client_id]
        else:
            client_clause = '(f.client_id IS NULL OR f.client_id = "")'

        # 1. Primary FTS5 Search (Ranked by BM25 relevance score)
        fts_where = ['files_fts MATCH ?']
        fts_params = [fts_query]

        if folder_clauses:
            fts_where.extend(folder_clauses)
            fts_params.extend(sql_params)
        if lang_clause:
            fts_where.append('f.language = ?')
            fts_params.append(language)
        if client_clause:
            fts_where.append(client_clause)
            fts_params.extend(client_params)

        full_fts_sql = f'''
            SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                   f.confidence, f.language, f.full_text, bm25(files_fts) as rank
            FROM files_fts
            JOIN files f ON files_fts.filepath = f.filepath
            WHERE {' AND '.join(fts_where)}
            ORDER BY rank ASC, f.confidence DESC
            LIMIT 100
        '''

        matched_files = []
        try:
            cursor.execute(full_fts_sql, tuple(fts_params))
            matched_files = cursor.fetchall()
        except sqlite3.Error:
            safe_query = " ".join(f'"{t}"' for t in tokens)
            try:
                fts_params[0] = safe_query
                cursor.execute(full_fts_sql, tuple(fts_params))
                matched_files = cursor.fetchall()
            except sqlite3.Error:
                matched_files = []

        # 2. Trigram Substring Search (for infixes like middle of chassis number, partial phone numbers, codes)
        if not matched_files and len(tokens[0]) >= 3:
            trigram_where = ['files_trigram MATCH ?']
            trigram_params = [f'"{kw}"']

            if folder_clauses:
                trigram_where.extend(folder_clauses)
                trigram_params.extend(sql_params)
            if lang_clause:
                trigram_where.append('f.language = ?')
                trigram_params.append(language)
            if client_clause:
                trigram_where.append(client_clause)
                trigram_params.extend(client_params)

            trigram_sql = f'''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, f.full_text, 0.0 as rank
                FROM files_trigram
                JOIN files f ON files_trigram.filepath = f.filepath
                WHERE {' AND '.join(trigram_where)}
                ORDER BY f.confidence DESC, f.id
                LIMIT 50
            '''
            try:
                cursor.execute(trigram_sql, tuple(trigram_params))
                matched_files = cursor.fetchall()
            except sqlite3.Error:
                matched_files = []

        # 3. Fallback Substring / LIKE Search
        if not matched_files:
            like_where = ['(f.full_text LIKE ? OR f.filename LIKE ?)']
            like_params = [f'%{kw}%', f'%{kw}%']
            if folder_clauses:
                like_where.extend(folder_clauses)
                like_params.extend(sql_params)
            if lang_clause:
                like_where.append('f.language = ?')
                like_params.append(language)
            if client_clause:
                like_where.append(client_clause)
                like_params.extend(client_params)

            like_sql = f'''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, f.full_text, 0.0 as rank
                FROM files f
                WHERE {' AND '.join(like_where)}
                ORDER BY f.confidence DESC, f.id
                LIMIT 50
            '''
            try:
                cursor.execute(like_sql, tuple(like_params))
                matched_files = cursor.fetchall()
            except sqlite3.Error:
                matched_files = []

        # 4. Build rich snippet matches for each file
        results = []
        for row in matched_files:
            full_text = row['full_text'] or ""
            filename = row['filename']
            filepath = row['filepath']

            matches = []
            if full_text:
                matches = find_matches_multilingual(full_text, kw, context_chars, language)
                if not matches and tokens:
                    matches = find_matches_multilingual(full_text, tokens[0], context_chars, language)

            # Fallback if match occurred in filename itself
            if not matches and kw.lower() in filename.lower():
                idx = filename.lower().find(kw.lower())
                matches = [{
                    "snippet": filename,
                    "match_start": idx,
                    "match_end": idx + len(kw),
                    "match_text": filename[idx:idx + len(kw)]
                }]

            if matches:
                results.append({
                    'file_id': row['id'],
                    'file': filename,
                    'filepath': filepath,
                    'filename': filename,
                    'extension': row['extension'],
                    'ocr_method': row['ocr_method'] or 'indexed',
                    'confidence': row['confidence'] or 0.0,
                    'language': row['language'] or 'en',
                    'match_count': len(matches),
                    'matches': matches[:20],
                })

        return results

    def prune_missing_files(self, folder: str, current_filepaths: Set[str]) -> int:
        """Prune records for files that were moved or deleted from disk."""
        f_slash = folder.rstrip('/\\').replace('\\', '/')
        b_slash = folder.rstrip('/\\').replace('/', '\\')
        with self._index_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT filepath FROM files WHERE filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ? OR filepath LIKE ?",
                (f_slash + '/%', f_slash + '%', b_slash + '\\%', b_slash + '%')
            )
            db_paths = [row['filepath'] for row in cursor.fetchall()]
            missing = [p for p in db_paths if p not in current_filepaths]
            if missing:
                placeholders = ','.join('?' for _ in missing)
                cursor.execute(f"DELETE FROM files WHERE filepath IN ({placeholders})", tuple(missing))
                try:
                    cursor.execute(f"DELETE FROM files_fts WHERE filepath IN ({placeholders})", tuple(missing))
                except Exception:
                    pass
                try:
                    cursor.execute(f"DELETE FROM files_trigram WHERE filepath IN ({placeholders})", tuple(missing))
                except Exception:
                    pass
                self.conn.commit()
            return len(missing)

    def get_all_files(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM files ORDER BY indexed_at DESC')
        return [dict(row) for row in cursor.fetchall()]

    def is_file_indexed(self, filepath: str, content_hash: str) -> bool:
        """Check if a file is already indexed with the same content hash and has extracted text."""
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT id FROM files WHERE filepath = ? AND content_hash = ? AND full_text IS NOT NULL',
            (filepath, content_hash)
        )
        return cursor.fetchone() is not None

    def clear_index(self):
        with self._index_lock:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM files')
            try:
                cursor.execute('DELETE FROM files_fts')
            except Exception:
                pass
            try:
                cursor.execute('DELETE FROM files_trigram')
            except Exception:
                pass
            try:
                cursor.execute('DELETE FROM content')
                cursor.execute('DELETE FROM inverted_index')
            except Exception:
                pass
            self.conn.commit()

    def get_client_files_count(self, client_id: str, folder_prefix: str = None) -> int:
        """Count indexed files belonging to a specific client session."""
        cursor = self.conn.cursor()
        is_client_root = not folder_prefix or folder_prefix in ('all', 'client://', 'client://local', f'client://{client_id}')
        if not is_client_root:
            cursor.execute(
                'SELECT count(*) FROM files WHERE client_id = ? AND (filepath LIKE ? OR filename LIKE ?)',
                (client_id, f"{folder_prefix}%", f"{folder_prefix}%")
            )
        else:
            cursor.execute('SELECT count(*) FROM files WHERE client_id = ?', (client_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

    def delete_client_folder(self, client_id: str, folder_name: str) -> int:
        """Remove indexed files for a specific client folder."""
        prefix = f"client://{client_id}/{folder_name}/"
        with self._index_lock:
            cursor = self.conn.cursor()
            cursor.execute(
                'SELECT filepath FROM files WHERE client_id = ? AND (filepath LIKE ? OR filepath = ?)',
                (client_id, f"{prefix}%", f"client://{client_id}/{folder_name}")
            )
            paths = [row['filepath'] for row in cursor.fetchall()]
            if paths:
                placeholders = ','.join('?' for _ in paths)
                cursor.execute(f"DELETE FROM files WHERE filepath IN ({placeholders})", tuple(paths))
                try:
                    cursor.execute(f"DELETE FROM files_fts WHERE filepath IN ({placeholders})", tuple(paths))
                except Exception:
                    pass
                try:
                    cursor.execute(f"DELETE FROM files_trigram WHERE filepath IN ({placeholders})", tuple(paths))
                except Exception:
                    pass
                self.conn.commit()
            return len(paths)

    def clear_client_files(self, client_id: str):
        """Remove indexed files for a specific client session."""
        with self._index_lock:
            cursor = self.conn.cursor()
            cursor.execute('SELECT filepath FROM files WHERE client_id = ?', (client_id,))
            paths = [row['filepath'] for row in cursor.fetchall()]
            if paths:
                placeholders = ','.join('?' for _ in paths)
                cursor.execute(f"DELETE FROM files WHERE filepath IN ({placeholders})", tuple(paths))
                try:
                    cursor.execute(f"DELETE FROM files_fts WHERE filepath IN ({placeholders})", tuple(paths))
                except Exception:
                    pass
                try:
                    cursor.execute(f"DELETE FROM files_trigram WHERE filepath IN ({placeholders})", tuple(paths))
                except Exception:
                    pass
                self.conn.commit()

    def close(self):
        self.conn.close()

if __name__ == '__main__':
    db = IndexDatabase()
    print("IndexDatabase initialized with FTS5 and Trigram.")
    db.close()
