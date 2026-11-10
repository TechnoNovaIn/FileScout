#!/bin/bash
# scripts/mac-register-service.sh
# ─────────────────────────────────
# Registers "Open with FileScout" in macOS Finder right-click Services menu.
# Called once after the user drags FileScout.app into /Applications.
#
# Usage (Terminal):
#   bash mac-register-service.sh

APP_NAME="FileScout"
APP_PATH="/Applications/FileScout.app"
SERVICE_DIR="$HOME/Library/Services"
SERVICE_NAME="Open with FileScout.workflow"
SERVICE_PATH="$SERVICE_DIR/$SERVICE_NAME"

# ── Check app is installed ─────────────────────────────────────────────────────
if [ ! -d "$APP_PATH" ]; then
  echo "❌  FileScout.app not found in /Applications."
  echo "    Please drag FileScout.app to /Applications first."
  exit 1
fi

# ── Create Services folder if needed ──────────────────────────────────────────
mkdir -p "$SERVICE_DIR"

# ── Write the Automator workflow bundle ───────────────────────────────────────
mkdir -p "$SERVICE_PATH/Contents"

cat > "$SERVICE_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Open with FileScout</string>
  <key>CFBundleIdentifier</key>
  <string>com.filescout.service</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>NSServices</key>
  <array>
    <dict>
      <key>NSMenuItem</key>
      <dict>
        <key>default</key>
        <string>Open with FileScout</string>
      </dict>
      <key>NSMessage</key>
      <string>runWorkflowAsService</string>
      <key>NSSendTypes</key>
      <array/>
      <key>NSSendFileTypes</key>
      <array>
        <string>public.folder</string>
      </array>
    </dict>
  </array>
</dict>
</plist>
PLIST

mkdir -p "$SERVICE_PATH/Contents/document.wflow"

cat > "$SERVICE_PATH/Contents/document.wflow" << 'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>AMApplicationBuild</key>
  <string>523</string>
  <key>AMApplicationVersion</key>
  <string>2.10</string>
  <key>AMDocumentVersion</key>
  <string>2</string>
  <key>actions</key>
  <array>
    <dict>
      <key>action</key>
      <dict>
        <key>AMAccepts</key>
        <dict>
          <key>Container</key>
          <string>List</string>
          <key>Optional</key>
          <true/>
          <key>Types</key>
          <array><string>com.apple.cocoa.path</string></array>
        </dict>
        <key>AMActionVersion</key>
        <string>2.0.3</string>
        <key>AMApplication</key>
        <array><string>Automator</string></array>
        <key>AMParameterProperties</key>
        <dict>
          <key>COMMAND_STRING</key>
          <dict/>
          <key>shell</key>
          <dict/>
          <key>source</key>
          <dict/>
        </dict>
        <key>AMProvides</key>
        <dict>
          <key>Container</key>
          <string>List</string>
          <key>Types</key>
          <array><string>com.apple.cocoa.path</string></array>
        </dict>
        <key>ActionBundlePath</key>
        <string>/System/Library/Automator/Run Shell Script.action</string>
        <key>ActionName</key>
        <string>Run Shell Script</string>
        <key>ActionParameters</key>
        <dict>
          <key>COMMAND_STRING</key>
          <string>for f in "$@"; do open -a /Applications/FileScout.app "$f"; done</string>
          <key>shell</key>
          <string>/bin/bash</string>
          <key>source</key>
          <string>0</string>
        </dict>
        <key>BundleIdentifier</key>
        <string>com.apple.RunShellScript</string>
        <key>CFBundleVersion</key>
        <string>2.0.3</string>
        <key>CanShowSelectedItemsWhenRunning</key>
        <false/>
        <key>CanShowWhenRunning</key>
        <true/>
        <key>Category</key>
        <array><string>AMCategoryUtilities</string></array>
        <key>Class Name</key>
        <string>RunShellScriptAction</string>
        <key>InputUUID</key>
        <string>B4B1CBD0-3B9E-4B0F-B85C-3E7B57E99AD5</string>
        <key>Keywords</key>
        <array><string>Shell</string><string>Script</string><string>Command</string></array>
        <key>OutputUUID</key>
        <string>9F9E9E3D-5E5E-5E5E-5E5E-9F9E9E9E9E9E</string>
        <key>UUID</key>
        <string>A1B2C3D4-E5F6-7890-ABCD-EF1234567890</string>
        <key>UnlocalizedApplications</key>
        <array><string>Automator</string></array>
        <key>arguments</key>
        <dict>
          <key>0</key>
          <dict>
            <key>default value</key>
            <string></string>
            <key>name</key>
            <string>source</string>
            <key>required</key>
            <string>0</string>
            <key>type</key>
            <string>0</string>
            <key>uuid</key>
            <string>0</string>
          </dict>
        </dict>
        <key>isViewVisible</key>
        <true/>
        <key>location</key>
        <string>200.000000:253.000000</string>
        <key>nibPath</key>
        <string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/English.lproj/main.nib</string>
      </dict>
      <key>isViewVisible</key>
      <true/>
    </dict>
  </array>
  <key>connectors</key>
  <dict/>
  <key>workflowMetaData</key>
  <dict>
    <key>serviceInputTypeIdentifier</key>
    <string>com.apple.Automator.fileSystemObject.folder</string>
    <key>serviceOutputTypeIdentifier</key>
    <string>com.apple.Automator.nothing</string>
    <key>serviceProcessesInput</key>
    <integer>0</integer>
    <key>workflowTypeIdentifier</key>
    <string>com.apple.Automator.servicesMenu</string>
  </dict>
</dict>
</plist>
WFLOW

# ── Refresh Services ───────────────────────────────────────────────────────────
/System/Library/CoreServices/pbs -update 2>/dev/null || true

echo ""
echo "✅  'Open with FileScout' added to Finder right-click Services menu."
echo ""
echo "    Right-click any folder in Finder → Services → Open with FileScout"
echo ""
echo "    Note: You may need to log out and back in for the service to appear."
