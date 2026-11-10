const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

const isDev = !app.isPackaged;

function getPythonBinary() {
  if (isDev) {
    const venvPython = path.join(__dirname, '../venv/bin/python3');
    const cmd = fs.existsSync(venvPython) ? venvPython : 'python3';
    return { cmd, script: path.join(__dirname, '../python/search.py') };
  }
  const ext = process.platform === 'win32' ? 'search.exe' : 'search';
  return { cmd: path.join(process.resourcesPath, 'python', ext), script: null };
}

function getFolderFromArgs() {
  const args = process.argv.slice(isDev ? 2 : 1);
  return args.find(a => { try { return fs.existsSync(a) && fs.statSync(a).isDirectory(); } catch { return false; } }) || null;
}

function extractJson(raw) {
  // On Windows, stderr progress lines can leak into the stdout pipe.
  // Strategy 1: Extract between ###JSON_START### / ###JSON_END### delimiters.
  // Strategy 2: Fall back to finding the first '{' and last '}'.
  const startMarker = '###JSON_START###';
  const endMarker = '###JSON_END###';
  const sIdx = raw.indexOf(startMarker);
  const eIdx = raw.indexOf(endMarker);
  if (sIdx !== -1 && eIdx !== -1 && eIdx > sIdx) {
    return raw.substring(sIdx + startMarker.length, eIdx).trim();
  }
  const jsonStart = raw.indexOf('{');
  const jsonEnd = raw.lastIndexOf('}');
  if (jsonStart !== -1 && jsonEnd > jsonStart) {
    return raw.substring(jsonStart, jsonEnd + 1);
  }
  return raw;
}

function spawnPython(event, args, { timeout = 300000, progressChannel = 'search-progress' } = {}) {
  const { cmd, script } = getPythonBinary();
  const fullArgs = script ? [script, ...args] : args;

  const proc = spawn(cmd, fullArgs, {
    maxBuffer: 1024 * 1024 * 10,
    timeout,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });

  let buffer = '';
  let stderrBuf = '';

  proc.stdout.on('data', (data) => { buffer += data.toString(); });
  proc.stderr.on('data', (data) => {
    const chunk = data.toString();
    stderrBuf += chunk;
    const line = chunk.trim();
    if (line) event.sender.send(progressChannel, line);
  });

  return { proc, getBuffer: () => buffer, getStderr: () => stderrBuf };
}

let mainWindow;

function createWindow(folderPath) {
  mainWindow = new BrowserWindow({
    width: 1100, height: 720, minWidth: 700, minHeight: 500,
    backgroundColor: '#0a0a0f',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:3000');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../build/index.html'));
  }

  mainWindow.webContents.on('did-finish-load', () => {
    if (folderPath) mainWindow.webContents.send('set-folder', folderPath);
  });
}

app.whenReady().then(() => {
  const folderPath = getFolderFromArgs();
  createWindow(folderPath);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(folderPath); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

// ── Run search (default: DB cache + extract uncached) ──────────────────────
ipcMain.on('run-search', (event, { folder, keyword, contextChars, language, ocrQuality, searchOnly }) => {
  const args = ['--folder', folder, '--keyword', keyword, '--context', String(contextChars || 80), '--json'];

  if (searchOnly) {
    args.push('--search-only');
  }
  if (language && language !== 'en') {
    args.push('--language', language);
  }
  if (ocrQuality && ocrQuality !== 'balanced') {
    args.push('--ocr-quality', ocrQuality);
  }

  const { proc, getBuffer, getStderr } = spawnPython(event, args);

  proc.on('close', (code) => {
    let raw = getBuffer().trim();
    if (!raw) {
      event.sender.send('search-results', { ok: false, results: [], error: `Python exited with code ${code}. stderr: ${getStderr().slice(-500)}` });
      return;
    }
    try {
      const parsed = JSON.parse(extractJson(raw));
      const results = Array.isArray(parsed) ? parsed : (parsed.results || []);
      event.sender.send('search-results', {
        ok: true,
        results,
        searchTime: parsed.search_time_seconds,
        language: parsed.language,
        totalMatched: parsed.total_files_matched,
      });
    } catch (e) {
      event.sender.send('search-results', { ok: false, results: [], error: `Failed to parse results: ${e.message}. Raw: ${raw.slice(-300)}` });
    }
  });

  proc.on('error', (err) => {
    event.sender.send('search-results', { ok: false, results: [], error: err.message });
  });
});

// ── Index folder (extract + store in DB, no search) ────────────────────────
ipcMain.on('index-folder', (event, { folder, ocrQuality }) => {
  const args = ['--folder', folder, '--index-only', '--json'];

  if (ocrQuality && ocrQuality !== 'balanced') {
    args.push('--ocr-quality', ocrQuality);
  }

  const { proc, getBuffer, getStderr } = spawnPython(event, args, {
    timeout: 3600000, // 1 hour for large folders
    progressChannel: 'index-progress',
  });

  proc.on('close', (code) => {
    let raw = getBuffer().trim();
    if (!raw) {
      event.sender.send('index-results', { ok: false, error: `Python exited with code ${code}. stderr: ${getStderr().slice(-500)}` });
      return;
    }
    try {
      const parsed = JSON.parse(extractJson(raw));
      event.sender.send('index-results', {
        ok: true,
        totalFiles: parsed.total_files,
        alreadyIndexed: parsed.already_indexed,
        indexed: parsed.indexed,
        skipped: parsed.skipped,
        errors: parsed.errors,
        searchTime: parsed.search_time_seconds,
      });
    } catch (e) {
      event.sender.send('index-results', { ok: false, error: `Failed to parse results: ${e.message}. Raw: ${raw.slice(-300)}` });
    }
  });

  proc.on('error', (err) => {
    event.sender.send('index-results', { ok: false, error: err.message });
  });
});

// ── Check index status for a folder ────────────────────────────────────────
ipcMain.handle('index-status', async (event, folder) => {
  const args = ['--folder', folder, '--index-status', '--json'];
  const { proc, getBuffer } = spawnPython(event, args, { timeout: 30000 });

  return new Promise((resolve) => {
    proc.on('close', () => {
      let raw = getBuffer().trim();
      try {
        const parsed = JSON.parse(extractJson(raw));
        resolve({ ok: true, ...parsed });
      } catch (e) {
        resolve({ ok: false, error: e.message });
      }
    });
    proc.on('error', (err) => {
      resolve({ ok: false, error: err.message });
    });
  });
});

// ── Open file in system viewer ─────────────────────────────────────────────
ipcMain.on('open-file', (event, filePath) => { shell.openPath(filePath); });

// ── Browse for folder ──────────────────────────────────────────────────────
ipcMain.handle('browse-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'], title: 'Select folder to search',
  });
  return result.canceled ? null : result.filePaths[0];
});
