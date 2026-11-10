/**
 * register-context-menu.js
 * ─────────────────────────
 * Registers "Open with FileScout" in the Windows right-click menu for folders.
 * This is called automatically by the NSIS installer on Windows.
 * On macOS, the context menu is handled via the app's Info.plist Services entry.
 *
 * To run manually (Windows, as Administrator):
 *   node electron/register-context-menu.js
 *
 * To unregister:
 *   node electron/register-context-menu.js --unregister
 */

const { execSync } = require('child_process');
const path = require('path');
const os = require('os');

const appName = 'FileScout';

if (os.platform() !== 'win32') {
  console.log('Context menu registration via this script is Windows-only.');
  console.log('On macOS, the right-click service is registered via Info.plist automatically.');
  process.exit(0);
}

const exePath = process.execPath; // path to FileScout.exe when run from installed app

const unregister = process.argv.includes('--unregister');

if (unregister) {
  try {
    execSync(`reg delete "HKCU\\Software\\Classes\\Directory\\shell\\${appName}" /f`);
    execSync(`reg delete "HKCU\\Software\\Classes\\Directory\\Background\\shell\\${appName}" /f`);
    console.log(`✅ "${appName}" removed from right-click menu.`);
  } catch (e) {
    console.error('Failed to remove registry entries:', e.message);
  }
} else {
  try {
    // Right-click on a FOLDER
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\shell\\${appName}" /ve /d "Open with ${appName}" /f`);
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\shell\\${appName}" /v "Icon" /d "${exePath},0" /f`);
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\shell\\${appName}\\command" /ve /d "\\"${exePath}\\" \\"%V\\"" /f`);

    // Right-click on FOLDER BACKGROUND (inside an open folder)
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\Background\\shell\\${appName}" /ve /d "Open with ${appName}" /f`);
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\Background\\shell\\${appName}" /v "Icon" /d "${exePath},0" /f`);
    execSync(`reg add "HKCU\\Software\\Classes\\Directory\\Background\\shell\\${appName}\\command" /ve /d "\\"${exePath}\\" \\"%V\\"" /f`);

    console.log(`✅ "${appName}" added to right-click menu for folders.`);
  } catch (e) {
    console.error('Failed to write registry entries (try running as Administrator):', e.message);
  }
}
