"""
FileScout Web API — FastAPI backend for browser-based deployment.
Replaces the Electron IPC layer with REST + SSE endpoints.
Serves the React build as static files on the same port.
"""
import os
import sys
import json
import time
import asyncio
import mimetypes
import re
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor

# Ensure the python/ directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query, HTTPException, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
    HTMLResponse,
    JSONResponse,
)
from pydantic import BaseModel

# ── Import FileScout search modules ─────────────────────────────────────────
from search import (
    search_folder,
    search_folder_from_db,
    index_folder as _index_folder_sync,
    index_status as _index_status_sync,
    collect_files,
    extract_text,
    get_db,
    _cache_key,
    SUPPORTED,
)
from search_engine import find_matches_multilingual
from processors.language_processor import (
    detect_language,
    resolve_ocr_languages,
    normalize_unicode,
)

# ── Path Normalization & System Discovery Helpers ───────────────────────────

SHARES_FILE = Path(__file__).resolve().parent.parent / "network_shares.json"

def is_local_request(request: Request) -> bool:
    """Determine whether request originated from the local host machine or a remote network client."""
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "127.0.0.1"
    
    return client_ip in ("127.0.0.1", "::1", "localhost", "testclient")


def get_configured_shares() -> List[Dict]:
    """Load pre-configured or saved network shares from network_shares.json."""
    if SHARES_FILE.is_file():
        try:
            with open(SHARES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_configured_shares(shares: List[Dict]) -> bool:
    try:
        with open(SHARES_FILE, "w", encoding="utf-8") as f:
            json.dump(shares, f, indent=2)
        return True
    except Exception:
        return False


def ensure_unc_mounted(server: str, share: str) -> str:
    """Mount a UNC share \\\\server\\share under /mnt/unc/server/share if not already mounted."""
    mount_point = f"/mnt/unc/{server}/{share}"
    if os.path.ismount(mount_point):
        return mount_point
    
    try:
        subprocess.run(["sudo", "mkdir", "-p", mount_point], capture_output=True, timeout=5)
        unc_path = f"\\\\{server}\\{share}"
        subprocess.run(["sudo", "mount", "-t", "drvfs", unc_path, mount_point], capture_output=True, timeout=5)
    except Exception:
        pass
    return mount_point


def normalize_system_path(raw_path: Optional[str]) -> str:
    """Normalize any path (Windows drive, Windows UNC, client virtual path, WSL linux path)
    into a valid accessible directory path."""
    if not raw_path:
        return "/"
    p = str(raw_path).strip().strip('"').strip("'")
    if not p:
        return "/"

    # Handle client virtual paths (client://<client_id>/<folder_name>)
    if p.startswith("client://"):
        return p

    # Handle ~
    if p == "~" or p.startswith("~/"):
        home = os.path.expanduser("~")
        p = home + p[1:]

    # Handle Windows Drive letters: C:, C:\..., C:/...
    m = re.match(r"^([a-zA-Z]):(?:[/\\](.*))?$", p)
    if m:
        drive = m.group(1).lower()
        sub = (m.group(2) or "").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{sub}" if sub else f"/mnt/{drive}"

    # Handle Windows UNC paths: \\server\share or //server/share
    m = re.match(r"^[/\\]{2}([^/\\]+)(?:[/\\]([^/\\]+))?(?:[/\\](.*))?$", p)
    if m:
        server = m.group(1)
        share = m.group(2) or ""
        sub = (m.group(3) or "").replace("\\", "/").strip("/")
        if server and share:
            ensure_unc_mounted(server, share)
            return f"/mnt/unc/{server}/{share}/{sub}" if sub else f"/mnt/unc/{server}/{share}"
        elif server:
            return f"/mnt/unc/{server}"

    # Standardize backslashes for unix
    p = p.replace("\\", "/")
    return p


def to_display_path(path: str) -> str:
    """Convert an internal WSL mount path back to user-friendly Windows or UNC notation."""
    p = str(path)
    if p.startswith("client://"):
        parts = p.split("/")
        if len(parts) > 3 and parts[3]:
            return f"[My Computer] {parts[3]}"
        return "[My Computer] All Indexed Files"

    # Check if /mnt/<drive>
    m = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", p)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2)
        if rest:
            return f"{drive}:\\{rest.replace('/', chr(92))}"
        return f"{drive}:\\"

    # Check if /mnt/unc/<server>/<share>
    m = re.match(r"^/mnt/unc/([^/]+)(?:/([^/]+))?(?:/(.*))?$", p)
    if m:
        server = m.group(1)
        share = m.group(2) or ""
        rest = m.group(3) or ""
        if share and rest:
            return f"\\\\{server}\\{share}\\{rest.replace('/', chr(92))}"
        elif share:
            return f"\\\\{server}\\{share}"
        return f"\\\\{server}"

    return p


def format_size(bytes_val: int) -> str:
    """Format byte sizes into clean human-readable units."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"


def get_file_category(ext: str) -> str:
    """Categorize file extensions for UI badges, icons, and filter tabs."""
    ext = ext.lower()
    if ext == ".pdf":
        return "pdf"
    elif ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".svg", ".gif", ".ico"}:
        return "image"
    elif ext in {".txt", ".csv", ".log", ".md", ".json", ".xml", ".yaml", ".yml", ".ini", ".conf", ".sql", ".sh"}:
        return "text"
    elif ext in {".doc", ".docx", ".odt", ".rtf", ".pages"}:
        return "document"
    elif ext in {".xls", ".xlsx", ".ods", ".numbers"}:
        return "spreadsheet"
    elif ext in {".ppt", ".pptx", ".odp", ".key"}:
        return "presentation"
    elif ext in {".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz"}:
        return "archive"
    elif ext in {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".c", ".cpp", ".java", ".go", ".rs", ".php"}:
        return "code"
    elif ext in {".mp3", ".wav", ".ogg", ".flac", ".m4a"}:
        return "audio"
    elif ext in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
        return "video"
    return "other"


def get_quick_access_roots(is_remote: bool = False):
    """Discover available roots: My Computer, Network Shares, and (if local) host partitions."""
    roots = []

    # 1. Always offer "My Computer" (Client local files via browser File System Access API)
    roots.append({
        "name": "My Computer",
        "display": "My Computer (This PC)",
        "path": "client://local",
        "type": "client_local",
        "icon": "desktop",
    })

    # 2. Configured and Discovered Network Shares
    configured_shares = get_configured_shares()
    for s in configured_shares:
        unc = s.get("unc_path") or s.get("display")
        roots.append({
            "name": s.get("name", "Network Share"),
            "display": unc,
            "path": unc,
            "type": "network",
            "icon": "network",
        })

    # Also list any mounted shares under /mnt/unc
    if os.path.exists("/mnt/unc"):
        try:
            for srv in sorted(os.listdir("/mnt/unc")):
                srv_dir = os.path.join("/mnt/unc", srv)
                if os.path.isdir(srv_dir):
                    for shr in sorted(os.listdir(srv_dir)):
                        shr_dir = os.path.join(srv_dir, shr)
                        if os.path.isdir(shr_dir):
                            unc_name = f"\\\\{srv}\\{shr}"
                            if not any(r["path"] == unc_name for r in roots):
                                roots.append({
                                    "name": f"Share ({shr})",
                                    "display": unc_name,
                                    "path": unc_name,
                                    "type": "network",
                                    "icon": "network",
                                })
        except Exception:
            pass

    # 3. Only show host server's local drives if the requester is localhost / administrator!
    if not is_remote:
        # Host drives
        if os.path.exists("/mnt"):
            try:
                for item in sorted(os.listdir("/mnt")):
                    full = os.path.join("/mnt", item)
                    if os.path.isdir(full) and len(item) == 1 and item.isalpha():
                        roots.append({
                            "name": f"Server Drive ({item.upper()}:)",
                            "display": f"{item.upper()}:\\",
                            "path": full,
                            "type": "drive",
                            "icon": "windows",
                        })
            except Exception:
                pass

        # Host admin folders
        user_folders = [
            ("Downloads", "/mnt/c/Users/Admin/Downloads", "C:\\Users\\Admin\\Downloads", "downloads"),
            ("Desktop", "/mnt/c/Users/Admin/Desktop", "C:\\Users\\Admin\\Desktop", "desktop"),
            ("Documents", "/mnt/c/Users/Admin/Documents", "C:\\Users\\Admin\\Documents", "documents"),
            ("FileScout Project", "/mnt/c/Filescout", "C:\\Filescout", "project"),
        ]
        for name, p, disp, icon in user_folders:
            if os.path.isdir(p):
                roots.append({
                    "name": f"Server {name}",
                    "display": disp,
                    "path": p,
                    "type": "favorite",
                    "icon": icon,
                })

    return roots


def scan_directory_sync(norm_path: str, is_remote: bool = False):
    """High-speed synchronous directory scanner using os.scandir."""
    # Virtual client folder handled directly by browser
    if norm_path.startswith("client://"):
        return {
            "current": norm_path,
            "display_path": to_display_path(norm_path),
            "parent": None,
            "parent_display": None,
            "is_client": True,
            "stats": {
                "total_items": 0,
                "folder_count": 0,
                "file_count": 0,
                "supported_count": 0,
                "total_size_bytes": 0,
                "total_size_formatted": "0 B",
            },
            "entries": [],
        }, None

    # When remote clients visit root, return the virtual network roots without 403 error
    if is_remote and norm_path in ("/", "/mnt"):
        network_entries = [
            {
                "name": "My Computer (This PC)",
                "path": "client://local",
                "display_path": "My Computer (This PC)",
                "type": "directory",
                "category": "client",
                "is_supported": False,
            }
        ]
        # Configured shares
        for s in get_configured_shares():
            unc = s.get("unc_path") or s.get("display")
            network_entries.append({
                "name": s.get("name") or unc,
                "path": unc,
                "display_path": unc,
                "type": "directory",
                "category": "network",
                "is_supported": False,
            })
        # Mounted shares under /mnt/unc
        if os.path.exists("/mnt/unc"):
            try:
                for srv in sorted(os.listdir("/mnt/unc")):
                    srv_dir = os.path.join("/mnt/unc", srv)
                    if os.path.isdir(srv_dir):
                        for shr in sorted(os.listdir(srv_dir)):
                            shr_dir = os.path.join(srv_dir, shr)
                            if os.path.isdir(shr_dir):
                                unc_name = f"\\\\{srv}\\{shr}"
                                if not any(e["path"] == unc_name for e in network_entries):
                                    network_entries.append({
                                        "name": f"Share ({shr} on {srv})",
                                        "path": unc_name,
                                        "display_path": unc_name,
                                        "type": "directory",
                                        "category": "network",
                                        "is_supported": False,
                                    })
            except Exception:
                pass

        return {
            "current": "/",
            "display_path": "Network Storage & My Computer",
            "parent": None,
            "parent_display": None,
            "is_virtual_root": True,
            "stats": {
                "total_items": len(network_entries),
                "folder_count": len(network_entries),
                "file_count": 0,
                "supported_count": 0,
                "total_size_bytes": 0,
                "total_size_formatted": "0 B",
            },
            "entries": network_entries,
        }, None

    # Protect server privacy: remote network clients cannot browse host user profiles or system dirs
    if is_remote and (norm_path.startswith("/mnt/c/Users/Admin") or norm_path.startswith("/mnt/c/Windows") or norm_path == "/mnt/c"):
        return None, "Access to host server private files is restricted for remote clients. Please select a folder on your computer or a network share."

    target_path = os.path.abspath(norm_path)
    if not os.path.isdir(target_path):
        return None, f"Not a directory: {target_path}"

    dirs = []
    files = []
    total_size = 0

    try:
        with os.scandir(target_path) as it:
            for entry in it:
                name = entry.name
                if name.startswith("."):
                    continue

                full_path = os.path.join(target_path, name)
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except (PermissionError, OSError):
                    continue

                try:
                    if is_dir:
                        dirs.append({
                            "name": name,
                            "path": full_path,
                            "display_path": to_display_path(full_path),
                            "type": "directory",
                        })
                    else:
                        stat = entry.stat(follow_symlinks=False)
                        size = stat.st_size
                        total_size += size
                        mtime = stat.st_mtime
                        mtime_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
                        ext = os.path.splitext(name)[1].lower()
                        category = get_file_category(ext)
                        is_supported = ext in SUPPORTED

                        files.append({
                            "name": name,
                            "path": full_path,
                            "display_path": to_display_path(full_path),
                            "type": "file",
                            "extension": ext.lstrip("."),
                            "category": category,
                            "size": size,
                            "size_formatted": format_size(size),
                            "mtime": mtime,
                            "mtime_str": mtime_str,
                            "is_supported": is_supported,
                        })
                except (PermissionError, OSError):
                    continue
    except PermissionError:
        return None, f"Permission denied: {target_path}"
    except Exception as e:
        return None, str(e)

    dirs.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())

    parent_path = os.path.dirname(target_path)
    if parent_path == target_path or not parent_path:
        parent_path = None

    return {
        "current": target_path,
        "display_path": to_display_path(target_path),
        "parent": parent_path,
        "parent_display": to_display_path(parent_path) if parent_path else None,
        "stats": {
            "total_items": len(dirs) + len(files),
            "folder_count": len(dirs),
            "file_count": len(files),
            "supported_count": sum(1 for f in files if f["is_supported"]),
            "total_size_bytes": total_size,
            "total_size_formatted": format_size(total_size),
        },
        "entries": dirs + files,
    }, None



# ── App setup ────────────────────────────────────────────────────────────────
app = FastAPI(title="FileScout API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Thread pool for CPU-bound search/index work
_executor = ThreadPoolExecutor(max_workers=8)

# ── Pydantic models ─────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    folder: Optional[str] = ""
    keyword: str
    context: int = 80
    language: Optional[str] = None
    ocrQuality: str = "balanced"
    searchOnly: bool = False
    clientId: Optional[str] = None


class IndexRequest(BaseModel):
    folder: Optional[str] = ""
    ocrQuality: str = "balanced"
    force: bool = False
    clientId: Optional[str] = None


class NetworkShareModel(BaseModel):
    name: str
    unc_path: str
    description: Optional[str] = ""


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "filescout"}


@app.get("/api/roots")
def get_roots(request: Request):
    """Return available system drives, WSL locations, and shared folders."""
    remote = not is_local_request(request)
    return {"ok": True, "roots": get_quick_access_roots(is_remote=remote), "is_remote": remote}


@app.get("/api/network-shares")
def list_network_shares():
    """List configured and mounted network shares."""
    return {"ok": True, "shares": get_configured_shares()}


@app.post("/api/network-shares")
def add_network_share(share: NetworkShareModel):
    """Register and mount a new network share."""
    shares = get_configured_shares()
    unc = share.unc_path.strip()
    
    # Check if already present
    for s in shares:
        if s.get("unc_path", "").lower() == unc.lower():
            return {"ok": True, "share": s, "message": "Share already registered"}

    m = re.match(r"^[/\\]{2}([^/\\]+)(?:[/\\]([^/\\]+))?", unc)
    if m:
        server = m.group(1)
        sh_name = m.group(2) or ""
        if server and sh_name:
            ensure_unc_mounted(server, sh_name)

    new_entry = {
        "id": f"share-{int(time.time())}",
        "name": share.name or unc,
        "unc_path": unc,
        "display": unc,
        "description": share.description or "",
    }
    shares.append(new_entry)
    save_configured_shares(shares)
    return {"ok": True, "share": new_entry}


# ── Search endpoint ──────────────────────────────────────────────────────────

@app.post("/api/search")
async def search_endpoint(request: Request, req: SearchRequest):
    """Run a keyword search on a folder or client session. Returns results as JSON."""
    remote = not is_local_request(request)
    cid = req.clientId
    raw_folder = (req.folder or "").strip()

    is_client = raw_folder.startswith("client://") or bool(cid and (not raw_folder or raw_folder in ('all', 'client://', 'client://local')))

    if is_client and cid:
        if not raw_folder or raw_folder in ('all', 'client://', 'client://local', f"client://{cid}"):
            folder = f"client://{cid}"
        else:
            folder = raw_folder

        if folder.startswith("client://"):
            folder_cid = folder.replace("client://", "").split("/")[0]
            if folder_cid and folder_cid not in ("local", cid):
                raise HTTPException(status_code=403, detail="Forbidden: You cannot access files indexed by another computer.")
    else:
        folder = normalize_system_path(raw_folder or "/")
        if remote and (folder.startswith("/mnt/c/Users/Admin") or folder.startswith("/mnt/c/Windows") or folder == "/mnt/c"):
            raise HTTPException(status_code=403, detail="Forbidden: Remote network clients cannot access host server private files.")
        if not os.path.isdir(folder):
            raise HTTPException(status_code=400, detail=f"Not a directory: {raw_folder}")

    loop = asyncio.get_event_loop()
    start = time.time()

    try:
        if is_client or req.searchOnly:
            results = await loop.run_in_executor(
                _executor,
                lambda: search_folder_from_db(
                    folder, req.keyword, req.context, req.language, client_id=cid
                ),
            )
        else:
            results = await loop.run_in_executor(
                _executor,
                lambda: search_folder(
                    folder,
                    req.keyword,
                    req.context,
                    req.language,
                    req.ocrQuality,
                    client_id=cid,
                ),
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = round(time.time() - start, 2)
    return {
        "ok": True,
        "keyword": req.keyword,
        "folder": folder,
        "display_folder": to_display_path(folder),
        "language": req.language or "en",
        "total_files_matched": len(results),
        "search_time_seconds": elapsed,
        "results": results,
    }


# ── Search with SSE progress ────────────────────────────────────────────────

@app.post("/api/search/stream")
async def search_stream(request: Request, req: SearchRequest):
    """Run search with Server-Sent Events for progress updates."""
    remote = not is_local_request(request)
    cid = req.clientId
    raw_folder = (req.folder or "").strip()

    is_client = raw_folder.startswith("client://") or bool(cid and (not raw_folder or raw_folder in ('all', 'client://', 'client://local')))

    if is_client and cid:
        if not raw_folder or raw_folder in ('all', 'client://', 'client://local', f"client://{cid}"):
            folder = f"client://{cid}"
        else:
            folder = raw_folder

        if folder.startswith("client://"):
            folder_cid = folder.replace("client://", "").split("/")[0]
            if folder_cid and folder_cid not in ("local", cid):
                raise HTTPException(status_code=403, detail="Forbidden: You cannot access files indexed by another computer.")
    else:
        folder = normalize_system_path(raw_folder or "/")
        if remote and (folder.startswith("/mnt/c/Users/Admin") or folder.startswith("/mnt/c/Windows") or folder == "/mnt/c"):
            raise HTTPException(status_code=403, detail="Forbidden: Remote network clients cannot access host server private files.")
        if not os.path.isdir(folder):
            raise HTTPException(status_code=400, detail=f"Not a directory: {raw_folder}")

    async def event_generator():
        import io
        from contextlib import redirect_stderr

        loop = asyncio.get_event_loop()
        progress_lines = []
        original_stderr = sys.stderr

        class ProgressCapture(io.TextIOBase):
            def write(self, s):
                if s.strip():
                    progress_lines.append(s.strip())
                try:
                    original_stderr.write(s)
                except Exception:
                    pass
                return len(s)

            def flush(self):
                try:
                    original_stderr.flush()
                except Exception:
                    pass

        result_holder = [None]
        error_holder = [None]

        def run_search():
            capture = ProgressCapture()
            sys.stderr = capture
            try:
                start = time.time()
                if is_client or req.searchOnly:
                    results = search_folder_from_db(
                        folder, req.keyword, req.context, req.language, client_id=cid
                    )
                else:
                    results = search_folder(
                        folder, req.keyword, req.context,
                        req.language, req.ocrQuality,
                        client_id=cid,
                    )
                elapsed = round(time.time() - start, 2)
                result_holder[0] = {
                    "ok": True,
                    "keyword": req.keyword,
                    "folder": folder,
                    "display_folder": to_display_path(folder),
                    "language": req.language or "en",
                    "total_files_matched": len(results),
                    "search_time_seconds": elapsed,
                    "results": results,
                }
            except Exception as e:
                error_holder[0] = str(e)
            finally:
                sys.stderr = original_stderr

        future = loop.run_in_executor(_executor, run_search)

        sent_idx = 0
        while not future.done():
            await asyncio.sleep(0.3)
            while sent_idx < len(progress_lines):
                line = progress_lines[sent_idx]
                yield f"data: {json.dumps({'type': 'progress', 'line': line})}\n\n"
                sent_idx += 1

        # Drain remaining progress
        while sent_idx < len(progress_lines):
            line = progress_lines[sent_idx]
            yield f"data: {json.dumps({'type': 'progress', 'line': line})}\n\n"
            sent_idx += 1

        if error_holder[0]:
            yield f"data: {json.dumps({'type': 'error', 'error': error_holder[0]})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'result', 'data': result_holder[0]})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Index folder ─────────────────────────────────────────────────────────────

@app.post("/api/index")
async def index_endpoint(req: IndexRequest):
    """Index a folder (extract text + store in DB). Returns summary."""
    folder = normalize_system_path(req.folder)
    if folder.startswith("client://"):
        cid = req.clientId or (folder.split("/")[2] if len(folder.split("/")) > 2 else "client")
        db = get_db()
        cnt = db.get_client_files_count(cid, folder)
        return {
            "ok": True,
            "folder": folder,
            "display_folder": to_display_path(folder),
            "total_files": cnt,
            "already_indexed": cnt,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "search_time_seconds": 0,
        }

    if not os.path.isdir(folder):
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.folder}")

    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: _index_folder_sync(folder, req.ocrQuality, force=req.force),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "display_folder": to_display_path(folder), **result}


# ── Index with SSE progress ─────────────────────────────────────────────────

@app.post("/api/index/stream")
async def index_stream(req: IndexRequest):
    """Index a folder with Server-Sent Events for progress updates."""
    folder = normalize_system_path(req.folder)
    if folder.startswith("client://"):
        cid = req.clientId or (folder.split("/")[2] if len(folder.split("/")) > 2 else "client")
        db = get_db()
        cnt = db.get_client_files_count(cid, folder)
        async def client_event_generator():
            yield f"data: {json.dumps({'type': 'progress', 'line': f'Checking client files for session {cid}...' })}\n\n"
            yield f"data: {json.dumps({'type': 'progress', 'line': f'Client folder has {cnt} file(s) indexed in database.'})}\n\n"
            yield f"data: {json.dumps({'type': 'result', 'data': {'ok': True, 'total_files': cnt, 'indexed': cnt, 'already_indexed': cnt, 'skipped': 0, 'errors': 0, 'search_time_seconds': 0}})}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"
        return StreamingResponse(
            client_event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if not os.path.isdir(folder):
        raise HTTPException(status_code=400, detail=f"Not a directory: {req.folder}")

    async def event_generator():
        import io

        loop = asyncio.get_event_loop()
        progress_lines = []
        original_stderr = sys.stderr

        class ProgressCapture(io.TextIOBase):
            def write(self, s):
                if s.strip():
                    progress_lines.append(s.strip())
                try:
                    original_stderr.write(s)
                except Exception:
                    pass
                return len(s)

            def flush(self):
                try:
                    original_stderr.flush()
                except Exception:
                    pass

        result_holder = [None]
        error_holder = [None]

        def run_index():
            capture = ProgressCapture()
            sys.stderr = capture
            try:
                result = _index_folder_sync(folder, req.ocrQuality, force=req.force)
                result_holder[0] = {
                    "ok": True,
                    "display_folder": to_display_path(folder),
                    **result,
                }
            except Exception as e:
                error_holder[0] = str(e)
            finally:
                sys.stderr = original_stderr

        future = loop.run_in_executor(_executor, run_index)

        sent_idx = 0
        while not future.done():
            await asyncio.sleep(0.3)
            while sent_idx < len(progress_lines):
                line = progress_lines[sent_idx]
                yield f"data: {json.dumps({'type': 'progress', 'line': line})}\n\n"
                sent_idx += 1

        while sent_idx < len(progress_lines):
            line = progress_lines[sent_idx]
            yield f"data: {json.dumps({'type': 'progress', 'line': line})}\n\n"
            sent_idx += 1

        if error_holder[0]:
            yield f"data: {json.dumps({'type': 'error', 'error': error_holder[0]})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'result', 'data': result_holder[0]})}\n\n"

        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Index status ─────────────────────────────────────────────────────────────

@app.get("/api/index-status")
async def index_status_endpoint(folder: Optional[str] = Query(None), clientId: Optional[str] = Query(None)):
    """Check how many files in a folder or client session are indexed."""
    clean_folder = (folder or "").strip()
    is_client = clean_folder.startswith("client://") or bool(clientId)

    if is_client and clientId:
        cid = clientId
        db = get_db()
        total_client_files = db.get_client_files_count(cid, None)
        folder_prefix = clean_folder if (clean_folder and clean_folder not in ('all', 'client://', 'client://local', f'client://{cid}')) else None
        cnt = db.get_client_files_count(cid, folder_prefix) if folder_prefix else total_client_files
        disp_folder = to_display_path(clean_folder) if folder_prefix else f"[My Computer] All Indexed Files ({total_client_files})"
        return {
            "ok": True,
            "folder": clean_folder or f"client://{cid}",
            "display_folder": disp_folder,
            "total_files": cnt or total_client_files,
            "indexed_files": cnt or total_client_files,
            "is_fully_indexed": (cnt or total_client_files) > 0,
        }

    norm_folder = normalize_system_path(clean_folder or "/")
    if not os.path.isdir(norm_folder):
        raise HTTPException(status_code=400, detail=f"Not a directory: {clean_folder}")

    loop = asyncio.get_event_loop()
    try:
        status = await loop.run_in_executor(
            _executor, lambda: _index_status_sync(norm_folder)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "folder": norm_folder,
        "display_folder": to_display_path(norm_folder),
        **status,
    }


# ── Browse folders (Windows, WSL, Shared Folders) ───────────────────────────

@app.get("/api/browse")
async def browse_folders(request: Request, path: str = Query("/", description="Directory to list")):
    """High-performance directory scanner returning full files, folders, and rich metadata."""
    remote = not is_local_request(request)
    norm_path = normalize_system_path(path)
    loop = asyncio.get_event_loop()

    data, err = await loop.run_in_executor(
        _executor, lambda: scan_directory_sync(norm_path, is_remote=remote)
    )
    if err:
        if "restricted" in err or "Permission denied" in err:
            raise HTTPException(status_code=403, detail=err)
        raise HTTPException(status_code=400, detail=err)

    data["quick_access"] = get_quick_access_roots(is_remote=remote)
    data["is_remote"] = remote
    return data


# ── Client Local File Processing Endpoints ─────────────────────────────────

CLIENT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "client_storage"
CLIENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/client/process-file")
async def process_client_file(
    file: UploadFile = File(...),
    client_id: str = Form(...),
    folder_name: str = Form(...),
    relative_path: str = Form(...),
    ocr_quality: str = Form("balanced"),
    language: Optional[str] = Form(None)
):
    """Receive a file from a remote client browser, store permanently in client-isolated storage,
    perform server-side OCR & text extraction, and index into SQLite FTS5."""
    clean_rel = relative_path.replace("\\", "/").strip("/")
    suffix = Path(clean_rel).suffix.lower()

    # Save to persistent client-isolated directory
    client_dir = CLIENT_STORAGE_DIR / client_id / folder_name
    client_dir.mkdir(parents=True, exist_ok=True)
    target_disk_path = client_dir / clean_rel
    target_disk_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    with open(target_disk_path, "wb") as f_out:
        f_out.write(content)

    try:
        ocr_languages = resolve_ocr_languages(language)
        loop = asyncio.get_event_loop()
        text, method, confidence = await loop.run_in_executor(
            _executor,
            lambda: extract_text(str(target_disk_path), ocr_languages, ocr_quality)
        )
    except Exception as e:
        return {"ok": False, "filename": clean_rel, "error": str(e)}

    if not text.strip():
        return {
            "ok": True,
            "indexed": False,
            "filename": clean_rel,
            "ocr_method": method or "no_text",
            "confidence": 0.0,
            "reason": "No text extracted"
        }

    try:
        text_lang, _ = detect_language(text)
    except Exception:
        text_lang = "en"

    raw_key = f"{client_id}|{folder_name}|{clean_rel}|{len(content)}"
    ck = hashlib.md5(raw_key.encode()).hexdigest()

    db = get_db()
    virtual_path = f"client://{client_id}/{folder_name}/{clean_rel}"
    db.add_file(
        filepath=virtual_path,
        filename=clean_rel,
        extension=suffix,
        ocr_method=method or "client_ocr",
        confidence=confidence,
        language=text_lang or "en",
        content_hash=ck,
        text=text,
        client_id=client_id,
    )

    return {
        "ok": True,
        "indexed": True,
        "filename": clean_rel,
        "ocr_method": method or "client_ocr",
        "confidence": round(confidence, 3),
        "language": text_lang or "en",
        "char_count": len(text),
    }


@app.get("/api/client/status")
def get_client_status(clientId: str = Query(...)):
    """Return total indexed files and folders summary for this client session."""
    db = get_db()
    total_count = db.get_client_files_count(clientId, None)
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT 
            substr(filepath, length('client://' || client_id || '/') + 1, 
                   instr(substr(filepath, length('client://' || client_id || '/') + 1), '/') - 1) as folder_name,
            count(*) as file_count
        FROM files 
        WHERE client_id = ?
        GROUP BY folder_name
    """, (clientId,))
    folders = []
    for row in cursor.fetchall():
        fn = row["folder_name"]
        if fn:
            folders.append({
                "name": fn,
                "folder_name": fn,
                "virtual_path": f"client://{clientId}/{fn}",
                "display_path": f"[My Computer] {fn}",
                "file_count": row["file_count"],
            })
    return {
        "ok": True,
        "clientId": clientId,
        "total_indexed_files": total_count,
        "folders": folders,
    }


@app.get("/api/client/index-status")
def client_index_status(client_id: str = Query(...), folder_name: str = Query(...)):
    """Count files indexed for a client session and folder."""
    db = get_db()
    prefix = f"client://{client_id}/{folder_name}"
    count = db.get_client_files_count(client_id, prefix)
    return {
        "ok": True,
        "client_id": client_id,
        "folder_name": folder_name,
        "indexed_files": count,
        "virtual_path": prefix
    }


@app.post("/api/client/clear")
def clear_client_index(client_id: str = Query(...)):
    """Clear all indexed data and stored files for a specific client session."""
    db = get_db()
    db.clear_client_files(client_id)
    # Remove client storage on disk
    c_dir = CLIENT_STORAGE_DIR / client_id
    if c_dir.exists():
        import shutil
        shutil.rmtree(str(c_dir), ignore_errors=True)
    return {"ok": True, "message": f"Cleared index for client {client_id}"}


@app.delete("/api/client/folder")
def delete_client_folder_endpoint(client_id: str = Query(...), folder_name: str = Query(...)):
    """Delete a specific folder from the client's index and remove its stored files."""
    db = get_db()
    deleted_count = db.delete_client_folder(client_id, folder_name)
    f_dir = CLIENT_STORAGE_DIR / client_id / folder_name
    if f_dir.exists():
        import shutil
        shutil.rmtree(str(f_dir), ignore_errors=True)
    return {"ok": True, "deleted_files": deleted_count, "folder_name": folder_name}


@app.get("/api/client/folders")
def get_client_folders(clientId: str = Query(...)):
    """List all previously indexed folders for this client session."""
    db = get_db()
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT 
            substr(filepath, length('client://' || client_id || '/') + 1, 
                   instr(substr(filepath, length('client://' || client_id || '/') + 1), '/') - 1) as folder_name,
            count(*) as file_count
        FROM files 
        WHERE client_id = ?
        GROUP BY folder_name
    """, (clientId,))
    folders = []
    for row in cursor.fetchall():
        fn = row["folder_name"]
        if fn:
            folders.append({
                "name": fn,
                "folder_name": fn,
                "virtual_path": f"client://{clientId}/{fn}",
                "display_path": f"[My Computer] {fn}",
                "file_count": row["file_count"],
            })
    return {"ok": True, "folders": folders}


@app.get("/api/client/folder-files")
def get_client_folder_files(clientId: str = Query(...), folder_name: str = Query(...)):
    """List all files indexed in a specific client folder."""
    db = get_db()
    cursor = db.conn.cursor()
    prefix = f"client://{clientId}/{folder_name}/"
    cursor.execute("""
        SELECT id, filepath, filename, extension, ocr_method, confidence, language 
        FROM files 
        WHERE client_id = ? AND filepath LIKE ?
    """, (clientId, f"{prefix}%"))
    files = []
    for row in cursor.fetchall():
        rel = row["filename"]
        base = rel.split("/")[-1]
        ext = (row["extension"] or "").lstrip(".").lower()
        files.append({
            "name": base,
            "relativePath": rel,
            "path": row["filepath"],
            "extension": ext,
            "type": "file",
            "category": get_file_category("." + ext),
            "ocr_method": row["ocr_method"],
            "confidence": row["confidence"],
            "is_supported": True,
        })
    return {"ok": True, "files": files}


@app.get("/api/client/file")
async def get_client_file(path: str = Query(...), clientId: str = Query(...)):
    """Serve a file uploaded by this client. Strictly isolated to the client's session."""
    if not path.startswith("client://"):
        raise HTTPException(status_code=400, detail="Not a client path")

    clean = path.replace("client://", "")
    parts = clean.split("/")
    file_cid = parts[0]
    if file_cid != clientId:
        raise HTTPException(status_code=403, detail="Forbidden: You cannot access files belonging to another client.")

    # Target path on disk: CLIENT_STORAGE_DIR / clientId / folder_name / clean_rel
    sub = "/".join(parts[1:])
    target_disk = CLIENT_STORAGE_DIR / file_cid / sub
    if target_disk.is_file():
        mime, _ = mimetypes.guess_type(str(target_disk))
        return FileResponse(
            str(target_disk),
            media_type=mime or "application/octet-stream",
            filename=target_disk.name,
        )

    # Fallback for files indexed before persistent disk storage was introduced:
    # Serve the cached full_text from database as a plain text response
    db = get_db()
    cursor = db.conn.cursor()
    cursor.execute("SELECT filename, full_text FROM files WHERE filepath = ? AND client_id = ?", (path, clientId))
    row = cursor.fetchone()
    if row and row["full_text"]:
        from fastapi.responses import PlainTextResponse
        fname = row["filename"].split("/")[-1]
        return PlainTextResponse(
            f"--- Extracted Text Preview for {fname} ---\n\n" + row["full_text"],
            headers={"Content-Disposition": f'inline; filename="{fname}.txt"'}
        )

    raise HTTPException(status_code=404, detail="File content not found on server")


# ── Open / download file ────────────────────────────────────────────────────

@app.get("/api/file")
async def get_file(request: Request, path: str = Query(...)):
    """Return a file for preview/download."""
    if path.startswith("client://"):
        raise HTTPException(status_code=400, detail="Client-local files reside on your computer. Use /api/client/file.")

    remote = not is_local_request(request)
    norm_path = normalize_system_path(path)
    if remote and (norm_path.startswith("/mnt/c/Users/Admin") or norm_path.startswith("/mnt/c/Windows") or norm_path == "/mnt/c"):
        raise HTTPException(status_code=403, detail="Forbidden: Remote network clients cannot access host server private files.")

    filepath = Path(norm_path)
    if not filepath.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    mime, _ = mimetypes.guess_type(str(filepath))
    return FileResponse(
        str(filepath),
        media_type=mime or "application/octet-stream",
        filename=filepath.name,
    )


# ── Serve React build as SPA (catch-all must be last) ───────────────────────

BUILD_DIR = Path(__file__).resolve().parent.parent / "build"


@app.on_event("startup")
async def startup():
    """Mount static files if build directory exists."""
    if BUILD_DIR.is_dir():
        # Mount static assets (JS, CSS, images)
        static_dir = BUILD_DIR / "static"
        if static_dir.is_dir():
            app.mount(
                "/static",
                StaticFiles(directory=str(static_dir)),
                name="static",
            )


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve the React SPA. Any non-API route returns index.html."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")

    # Check for exact static file first (e.g., manifest, favicon)
    requested = BUILD_DIR / full_path
    if full_path and requested.is_file():
        mime, _ = mimetypes.guess_type(str(requested))
        return FileResponse(str(requested), media_type=mime)

    # Fall back to index.html for SPA routing
    index = BUILD_DIR / "index.html"
    if index.is_file():
        return FileResponse(str(index), media_type="text/html")

    return HTMLResponse(
        content="<h1>FileScout</h1><p>Build not found. Run <code>npm run react:build</code> first.</p>",
        status_code=200,
    )


# ── Run directly ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "search_api:app",
        host="0.0.0.0",
        port=3001,
        reload=False,
        log_level="info",
    )
